"""Aggregation: turn attempt records into per-model statistics.

The headline metric is *cost per successful task*:

    total spend on a model (every attempt, failures included)
    ---------------------------------------------------------
         number of tasks that succeeded at least once

Models are ranked by it ascending. A model with zero successful tasks has
no meaningful cost per success: it sorts last and reports None.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .prices import PriceTable
from .records import Attempt, attempt_cost

DEFAULT_CACHED_FRACTION = 0.5


@dataclass
class ModelStats:
    model: str
    tasks_attempted: int = 0
    tasks_succeeded: int = 0
    attempts: int = 0
    total_cost: float = 0.0
    total_tokens: int = 0
    latencies: list[float] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.tasks_attempted == 0:
            return 0.0
        return self.tasks_succeeded / self.tasks_attempted

    @property
    def cost_per_successful_task(self) -> Optional[float]:
        if self.tasks_succeeded == 0:
            return None
        return self.total_cost / self.tasks_succeeded

    @property
    def cost_per_attempt(self) -> float:
        if self.attempts == 0:
            return 0.0
        return self.total_cost / self.attempts

    @property
    def median_latency_s(self) -> Optional[float]:
        if not self.latencies:
            return None
        return statistics.median(self.latencies)

    @property
    def avg_tokens_per_task(self) -> float:
        if self.tasks_attempted == 0:
            return 0.0
        return self.total_tokens / self.tasks_attempted

    @property
    def avg_attempts_per_task(self) -> float:
        if self.tasks_attempted == 0:
            return 0.0
        return self.attempts / self.tasks_attempted


def _rank_key(stats: ModelStats) -> tuple[int, float]:
    cps = stats.cost_per_successful_task
    if cps is None:
        return (1, float("inf"))
    return (0, cps)


def analyze(
    attempts: Iterable[Attempt],
    prices: PriceTable,
    cached_fraction: float = DEFAULT_CACHED_FRACTION,
) -> list[ModelStats]:
    """Aggregate attempts into per-model stats, ranked by cost per
    successful task (best first)."""
    by_model: dict[str, ModelStats] = {}
    task_success: dict[tuple[str, str], bool] = defaultdict(bool)
    task_seen: dict[str, set[str]] = defaultdict(set)

    for attempt in attempts:
        stats = by_model.setdefault(attempt.model, ModelStats(model=attempt.model))
        price = prices.price_for(attempt.model)
        stats.attempts += 1
        stats.total_cost += attempt_cost(attempt, price, cached_fraction)
        stats.total_tokens += attempt.total_tokens
        stats.latencies.append(attempt.latency_s)
        task_seen[attempt.model].add(attempt.task_id)
        if attempt.success:
            task_success[(attempt.model, attempt.task_id)] = True

    for model, stats in by_model.items():
        stats.tasks_attempted = len(task_seen[model])
        stats.tasks_succeeded = sum(
            1 for task_id in task_seen[model] if task_success[(model, task_id)]
        )

    return sorted(by_model.values(), key=_rank_key)


def build_insights(stats: list[ModelStats], prices: PriceTable) -> list[str]:
    """Short human-readable takeaways that fall out of the numbers."""
    insights: list[str] = []
    if not stats:
        return insights

    cheapest_token = prices.cheapest_per_token()
    best = stats[0]
    if (
        cheapest_token is not None
        and best.cost_per_successful_task is not None
        and cheapest_token.model != best.model
        and cheapest_token.model in {s.model for s in stats}
    ):
        cheap_stats = next(s for s in stats if s.model == cheapest_token.model)
        insights.append(
            f"Cheapest per token ({cheapest_token.model}) is NOT cheapest per "
            f"successful task: it succeeds on {cheap_stats.success_rate:.0%} of "
            f"tasks with {cheap_stats.avg_attempts_per_task:.1f} attempts/task, so "
            f"{best.model} wins once retries and failures are counted "
            f"(${best.cost_per_successful_task:.4f} vs "
            f"${cheap_stats.cost_per_successful_task:.4f} per successful task)."
            if cheap_stats.cost_per_successful_task is not None
            else f"Cheapest per token ({cheapest_token.model}) never completes a "
            f"task in this log; {best.model} is the cheapest model that does."
        )

    for s in stats:
        if s.tasks_succeeded == 0 and s.tasks_attempted > 0:
            insights.append(
                f"{s.model} completed 0 of {s.tasks_attempted} tasks "
                f"(${s.total_cost:.4f} spent, no successes to show for it)."
            )
    return insights
