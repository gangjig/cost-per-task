#!/usr/bin/env python3
"""Generate the bundled cache-scenario sample log
(src/cost_per_task/data/sample_cache.jsonl).

Ten agent sessions (five per model, task_id identifies the session):
eight attempts each, the context growing by roughly 10K tokens per
step. From the second attempt on, almost the whole accumulated context
is served from the prompt cache -- each attempt's `cached_input_tokens`
is the previous attempt's full input minus a small delta (the new
turn's own tokens, which cannot be cached yet). Outputs stay small, so
input dominates the bill: exactly the shape where prompt caching
carries the cost.

Designed outcome (deterministic; token sizes jittered by the seeded
RNG): with the bundled example prices, both models land near a 2.9x
no-cache multiplier, and one worst-case bust per session adds roughly
half again to the recorded bill.

Deterministic: fixed seed, no network. Run:
    python scripts/generate_cache_sample.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 20261031
MODELS = ("gpt-5", "claude-sonnet-4-5")
SESSIONS_PER_MODEL = 5
STEPS = 8  # attempts per session


def main() -> None:
    rng = random.Random(SEED)
    out = (
        Path(__file__).resolve().parent.parent
        / "src/cost_per_task/data/sample_cache.jsonl"
    )
    lines = []
    task_no = 0
    for model in MODELS:
        for _ in range(SESSIONS_PER_MODEL):
            task_no += 1
            task_id = f"task-c{task_no:02d}"
            input_tokens = rng.randint(9_000, 11_000)
            previous_input = 0
            for step in range(1, STEPS + 1):
                if step > 1:
                    input_tokens += rng.randint(9_000, 11_000)
                cached = (
                    0
                    if step == 1
                    else previous_input - rng.randint(600, 1_400)
                )
                record = {
                    "model": model,
                    "task_id": task_id,
                    "success": step == STEPS,
                    "input_tokens": input_tokens,
                    "output_tokens": rng.randint(180, 260),
                    "cached_input_tokens": cached,
                    "latency_s": round(rng.uniform(2.5, 6.0), 2),
                    "attempts": step,
                }
                lines.append(json.dumps(record))
                previous_input = input_tokens
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} records to {out}")


if __name__ == "__main__":
    main()
