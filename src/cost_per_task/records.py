"""Run-log records: one JSONL line per *attempt*.

A run log is produced by your own evaluation harness. Each line records a
single model invocation for a single task:

    {"model": "gpt-5-mini", "task_id": "task-01", "success": false,
     "input_tokens": 4000, "output_tokens": 812,
     "cached_input_tokens": 0, "latency_s": 5.9, "attempts": 1}

Semantics (see README for the full discussion):

* Lines are grouped by (model, task_id). A task may have several lines
  when the harness retried it.
* A task counts as *succeeded* if ANY of its attempts has success=true.
* ALL attempts count toward cost and token totals -- retries are not free.
* `cached_input_tokens` is the subset of `input_tokens` served from the
  provider's prompt cache; it is billed at the cached rate.
* `attempts` is optional and informational only: when present it is the
  1-based ordinal of the attempt within the task. Grouping never relies
  on it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Union

from .prices import Price

PathLike = Union[str, Path]


@dataclass(frozen=True)
class Attempt:
    model: str
    task_id: str
    success: bool
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    latency_s: float = 0.0
    attempts: Optional[int] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_dict(cls, data: dict, where: str = "record") -> "Attempt":
        required = ("model", "task_id", "success", "input_tokens", "output_tokens")
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"{where}: missing field(s): {', '.join(missing)}")
        attempt = cls(
            model=str(data["model"]),
            task_id=str(data["task_id"]),
            success=bool(data["success"]),
            input_tokens=int(data["input_tokens"]),
            output_tokens=int(data["output_tokens"]),
            cached_input_tokens=int(data.get("cached_input_tokens", 0)),
            latency_s=float(data.get("latency_s", 0.0)),
            attempts=(
                int(data["attempts"]) if data.get("attempts") is not None else None
            ),
        )
        for name in ("input_tokens", "output_tokens", "cached_input_tokens"):
            if getattr(attempt, name) < 0:
                raise ValueError(f"{where}: {name} must be >= 0")
        if attempt.cached_input_tokens > attempt.input_tokens:
            raise ValueError(
                f"{where}: cached_input_tokens ({attempt.cached_input_tokens}) "
                f"exceeds input_tokens ({attempt.input_tokens})"
            )
        if attempt.latency_s < 0:
            raise ValueError(f"{where}: latency_s must be >= 0")
        return attempt


def iter_records(path: PathLike) -> Iterator[Attempt]:
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            yield Attempt.from_dict(data, where=f"{path}:{lineno}")


def load_records(path: PathLike) -> list[Attempt]:
    return list(iter_records(path))


def attempt_cost(attempt: Attempt, price: Price, cached_fraction: float = 0.5) -> float:
    """USD cost of one attempt.

    Cached input tokens are billed at the model's cached-input price when
    the price table provides one; otherwise at `cached_fraction` of the
    regular input price.
    """
    cached = min(attempt.cached_input_tokens, attempt.input_tokens)
    uncached = attempt.input_tokens - cached
    if price.cached_input_per_m is not None:
        cached_rate = price.cached_input_per_m
    else:
        cached_rate = price.input_per_m * cached_fraction
    return (
        uncached * price.input_per_m
        + cached * cached_rate
        + attempt.output_tokens * price.output_per_m
    ) / 1_000_000.0
