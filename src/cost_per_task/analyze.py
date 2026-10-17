"""Aggregation: turn attempt records into per-model statistics.

The headline metric is *cost per successful task*:

    total spend on a model (every attempt, failures included)
    ---------------------------------------------------------
         number of tasks that succeeded at least once

Models are ranked by it ascending. A model with zero successful tasks has
no meaningful cost per success: it sorts last and reports None.

Router/escalation logs -- where one task is a single session handed from
model to model -- can also account for *cache loss*: a model cannot reuse
another model's prompt cache, so on each mid-task switch the destination
model is charged a one-time re-billing of the context accumulated in the
task so far (see `analyze`'s `cache_loss_fraction`).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .prices import PriceTable
from .records import Attempt, attempt_cost, cache_loss_charge

DEFAULT_CACHED_FRACTION = 0.5
DEFAULT_CACHE_LOSS_FRACTION = 0.0


@dataclass
class ModelStats:
    model: str
    tasks_attempted: int = 0
    tasks_succeeded: int = 0
    attempts: int = 0
    total_cost: float = 0.0
    total_tokens: int = 0
    latencies: list[float] = field(default_factory=list)
    # Cache-loss accounting (only nonzero when the analysis opted in via
    # cache_loss_fraction > 0): re-billing charged to this model when a
    # task switched INTO it mid-session, and how many such switches landed
    # on it.
    cache_loss_cost: float = 0.0
    switches_in: int = 0

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
    cache_loss_fraction: float = DEFAULT_CACHE_LOSS_FRACTION,
) -> list[ModelStats]:
    """Aggregate attempts into per-model stats, ranked by cost per
    successful task (best first).

    Attempts are consumed in the order given (log order). When
    `cache_loss_fraction` > 0, consecutive attempts for the same task_id
    that name *different* models count as a mid-session switch: the
    destination model is charged a one-time re-billing of the task's
    accumulated context -- the sum of `input_tokens` over the task's
    earlier attempts -- at its full (uncached) input price, scaled by
    `cache_loss_fraction` (0..1). The charge lands in the destination
    model's `total_cost` and is broken out in `cache_loss_cost`.

    The default fraction is 0.0 (off): the log format has no session
    marker, and paired-comparison logs legitimately reuse task ids across
    models, so charging by default would tax head-to-head logs for
    switches that never happened. Opt in for router/escalation logs,
    where one task_id really is one session handed between models.
    """
    if not 0.0 <= cache_loss_fraction <= 1.0:
        raise ValueError(
            f"cache_loss_fraction must be between 0 and 1, got {cache_loss_fraction}"
        )
    by_model: dict[str, ModelStats] = {}
    task_success: dict[tuple[str, str], bool] = defaultdict(bool)
    task_seen: dict[str, set[str]] = defaultdict(set)
    # Per-task session state for cache-loss accounting: the model of the
    # task's previous attempt and the input tokens accumulated so far.
    task_last_model: dict[str, str] = {}
    task_context_tokens: dict[str, int] = defaultdict(int)

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
        if cache_loss_fraction > 0.0:
            previous_model = task_last_model.get(attempt.task_id)
            if previous_model is not None and previous_model != attempt.model:
                charge = cache_loss_charge(
                    task_context_tokens[attempt.task_id],
                    price,
                    cache_loss_fraction,
                )
                stats.total_cost += charge
                stats.cache_loss_cost += charge
                stats.switches_in += 1
            task_context_tokens[attempt.task_id] += attempt.input_tokens
            task_last_model[attempt.task_id] = attempt.model

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

    for s in stats:
        if s.cache_loss_cost > 0:
            insights.append(
                f"Cache loss: {s.switches_in} mid-task switch(es) into "
                f"{s.model} re-billed the accumulated context at its full "
                f"input price, adding ${s.cache_loss_cost:.4f} to its spend "
                f"-- a new model cannot reuse the previous model's prompt "
                f"cache, so mid-session escalation costs more than the new "
                f"model's per-token price suggests."
            )
    return insights
