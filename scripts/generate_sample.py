#!/usr/bin/env python3
"""Generate the bundled sample run log (src/cost_per_task/data/sample_runs.jsonl).

Synthetic but shaped like real harness output: the same 20 tasks are run
against 3 models. Each retry re-sends a growing context, and retries past
the first lean on the prompt cache (cached_input_tokens). The cheapest
model per token (gpt-5-mini) is flaky, so it is NOT the cheapest per
successful task -- the flip this tool exists to show.

Deterministic: fixed seed, no network. Run:
    python scripts/generate_sample.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 20261014
N_TASKS = 20

# Per-model behaviour knobs.
MODELS = {
    # cheap per token, unreliable: low per-attempt success, context balloons
    "gpt-5-mini": dict(p_success=0.30, max_attempts=4, input_tokens=4000,
                       growth=1.30, output_tokens=800, latency=6.0),
    # mid price, terse and reliable
    "claude-haiku-4-5": dict(p_success=0.80, max_attempts=3, input_tokens=4000,
                             growth=1.15, output_tokens=500, latency=4.0),
    # pricey, strong, a bit verbose
    "gpt-5": dict(p_success=0.92, max_attempts=2, input_tokens=4200,
                  growth=1.10, output_tokens=900, latency=9.0),
}


def jitter(rng: random.Random, value: float, spread: float = 0.15) -> int:
    return max(1, int(round(value * rng.uniform(1 - spread, 1 + spread))))


def main() -> None:
    rng = random.Random(SEED)
    records = []
    for model, cfg in MODELS.items():
        for i in range(1, N_TASKS + 1):
            task_id = f"task-{i:02d}"
            input_tokens = cfg["input_tokens"]
            for attempt_no in range(1, cfg["max_attempts"] + 1):
                success = rng.random() < cfg["p_success"]
                # Retries lean on the prompt cache for most of the input.
                cached = int(input_tokens * 0.6) if attempt_no > 1 else 0
                records.append({
                    "model": model,
                    "task_id": task_id,
                    "success": success,
                    "input_tokens": jitter(rng, input_tokens),
                    "output_tokens": jitter(rng, cfg["output_tokens"]),
                    "cached_input_tokens": cached,
                    "latency_s": round(cfg["latency"] * rng.uniform(0.8, 1.3), 2),
                    "attempts": attempt_no,
                })
                if success:
                    break
                input_tokens = int(input_tokens * cfg["growth"])

    out = Path(__file__).resolve().parent.parent / "src" / "cost_per_task" / "data" / "sample_runs.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    print(f"wrote {len(records)} attempt records to {out}")


if __name__ == "__main__":
    main()
