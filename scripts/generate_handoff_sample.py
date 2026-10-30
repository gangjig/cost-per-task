#!/usr/bin/env python3
"""Generate the bundled handoff sample log
(src/cost_per_task/data/sample_handoff.jsonl).

A handoff experiment: the same 12 tasks are run 5 times each, handing off
from gpt-5 (strong, expensive) to gpt-5-mini (cheap) after 0, 1, 2 or 3
steps, or never. Every attempt carries a run_id identifying its run.

Designed outcome (deterministic; the per-task success sets below are
fixed, token sizes are jittered by the seeded RNG):

    switch after 0:  7/12 succeed  (cheap model alone fumbles hard tasks)
    switch after 1: 10/12
    switch after 2: 11/12         <- recommended: same success as never,
    switch after 3: 11/12             roughly half the cost per success
    never:          11/12

Deterministic: fixed seed, no network. Run:
    python scripts/generate_handoff_sample.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 20261030
STRONG = "gpt-5"
CHEAP = "gpt-5-mini"
N_TASKS = 12
STEPS = 5  # attempts per run

# Which tasks succeed at each switch point (task indexes 0..11). More
# strong-model steps rescue more tasks, with diminishing returns: even
# "never" drops the hardest task.
SUCCEED = {
    0: {0, 1, 2, 3, 4, 5, 6},
    1: {0, 1, 2, 3, 4, 5, 6, 7, 8, 9},
    2: {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
    3: {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
    None: {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
}


def main() -> None:
    rng = random.Random(SEED)
    out = Path(__file__).resolve().parent.parent / "src/cost_per_task/data/sample_handoff.jsonl"
    lines = []
    for switch_after in (0, 1, 2, 3, None):
        label = "never" if switch_after is None else str(switch_after)
        for task in range(N_TASKS):
            task_id = f"task-h{task + 1:02d}"
            run_id = f"{task_id}-s{label}"
            success = task in SUCCEED[switch_after]
            context = 4000 + task * 250  # later tasks carry longer prompts
            for step in range(STEPS):
                if switch_after is None:
                    model = STRONG
                else:
                    model = STRONG if step < switch_after else CHEAP
                context = int(context * rng.uniform(1.10, 1.25))
                lines.append(
                    json.dumps(
                        {
                            "model": model,
                            "task_id": task_id,
                            "success": success and step == STEPS - 1,
                            "input_tokens": context,
                            "output_tokens": rng.randint(600, 900),
                            "cached_input_tokens": 0,
                            "latency_s": round(rng.uniform(3.0, 8.0), 2),
                            "attempts": step + 1,
                            "run_id": run_id,
                        }
                    )
                )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} attempts ({N_TASKS * 5} runs) to {out}")


if __name__ == "__main__":
    main()
