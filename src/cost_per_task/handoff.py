"""Handoff curves: at which step should a task switch models?

A *handoff experiment* runs the same tasks repeatedly. Every run starts on
a strong (expensive) model and hands the task over to a cheap model after
a chosen number of steps -- a different switch point per run. This module
aggregates such a log by switch point so the tradeoff is visible as a
curve:

    switch early  ->  cheap, but the cheap model may fumble the task
    switch late   ->  nearly as reliable as never switching, nearly as
                      expensive too

Runs are identified by the optional `run_id` field on each attempt (see
`records`). The two endpoints are curve points of their own: switching
"after 0 steps" (the cheap model does the whole task) and "never" (the
strong model does the whole task).

The report recommends a switch point: among points whose success rate is
within `tolerance` of the best observed rate, the one with the lowest
cost per successful run -- the repo's headline metric, applied per run.
The pattern is motivated by AgentBaton's finding (reported in Latent
Space AINews, 2026-10-30) that a single well-placed mid-task handoff
from a strong model to a cheap one cut API cost by 65.9%:
https://www.latent.space/p/ainews-building-aie-code-the-top-coding-agent-conference/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .analyze import DEFAULT_CACHED_FRACTION, DEFAULT_CACHE_LOSS_FRACTION
from .prices import PriceTable
from .records import Attempt, attempt_cost, cache_loss_charge

DEFAULT_TOLERANCE = 0.05


@dataclass
class HandoffPoint:
    """Aggregate of all runs that switched after the same number of steps.

    `switch_after` is the number of leading attempts served by the
    from-model before the first to-model attempt. None means the run
    never switched (the from-model served the whole run).
    """

    switch_after: Optional[int]
    runs: int = 0
    successes: int = 0
    total_cost: float = 0.0
    cache_loss_cost: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.runs == 0:
            return 0.0
        return self.successes / self.runs

    @property
    def mean_cost_per_run(self) -> float:
        if self.runs == 0:
            return 0.0
        return self.total_cost / self.runs

    @property
    def cost_per_successful_run(self) -> Optional[float]:
        if self.successes == 0:
            return None
        return self.total_cost / self.successes


@dataclass
class HandoffReport:
    from_model: str
    to_model: str
    tolerance: float
    points: list[HandoffPoint]  # switch_after ascending; the None point last
    recommended: Optional[HandoffPoint]
    n_runs: int  # classifiable runs (sum of point runs)
    n_excluded_runs: int  # runs not classifiable for this model pair

    @property
    def best_success_rate(self) -> float:
        if not self.points:
            return 0.0
        return max(p.success_rate for p in self.points)


def _classify_run(
    run: list[Attempt], from_model: str, to_model: str
) -> Optional[int]:
    """Return the run's switch point, or raise ValueError if the run is
    not classifiable for the (from_model -> to_model) handoff.

    The switch point is the count of leading from-model attempts before
    the first to-model attempt: 0 when the run is all to-model, None when
    the run never switches (all from-model). Switch-backs after the first
    switch are allowed and do not move the point.
    """
    models = [a.model for a in run]
    if any(m not in (from_model, to_model) for m in models):
        raise ValueError("run involves a model outside the handoff pair")
    if all(m == from_model for m in models):
        return None
    if models[0] == to_model:
        if all(m == to_model for m in models):
            return 0
        raise ValueError("run escalates (to-model first, from-model later)")
    # Starts with the from-model and switches at least once.
    return models.index(to_model)


def _run_cost(
    run: list[Attempt],
    prices: PriceTable,
    cached_fraction: float,
    cache_loss_fraction: float,
) -> tuple[float, float]:
    """(total cost, cache-loss portion) of one run.

    Mirrors analyze()'s cache-loss semantics: when opted in, every model
    change inside the run charges the destination model a one-time
    re-billing of the run's accumulated input tokens at the destination's
    full input price, scaled by cache_loss_fraction.
    """
    total = 0.0
    cache_loss = 0.0
    context_tokens = 0
    previous_model: Optional[str] = None
    for attempt in run:
        price = prices.price_for(attempt.model)
        total += attempt_cost(attempt, price, cached_fraction)
        if (
            cache_loss_fraction > 0.0
            and previous_model is not None
            and attempt.model != previous_model
        ):
            charge = cache_loss_charge(
                context_tokens, price, cache_loss_fraction
            )
            total += charge
            cache_loss += charge
        context_tokens += attempt.input_tokens
        previous_model = attempt.model
    return total, cache_loss


def analyze_handoff(
    attempts: Iterable[Attempt],
    prices: PriceTable,
    from_model: str,
    to_model: str,
    cached_fraction: float = DEFAULT_CACHED_FRACTION,
    cache_loss_fraction: float = DEFAULT_CACHE_LOSS_FRACTION,
    tolerance: float = DEFAULT_TOLERANCE,
) -> HandoffReport:
    """Aggregate a handoff-experiment log into a switch-point curve.

    Every attempt must carry `run_id` -- without run identity there is no
    way to tell which attempts belong to the same run, so a missing
    `run_id` anywhere in the log is an error, not a silent skip. Runs
    that are not classifiable for the (from_model -> to_model) pair
    (a third model appears, or the run escalates in the wrong direction)
    are counted in `n_excluded_runs` and left out of the curve.
    """
    if from_model == to_model:
        raise ValueError("from_model and to_model must differ")
    if not 0.0 <= tolerance <= 1.0:
        raise ValueError(f"tolerance must be between 0 and 1, got {tolerance}")
    if not 0.0 <= cache_loss_fraction <= 1.0:
        raise ValueError(
            f"cache_loss_fraction must be between 0 and 1, "
            f"got {cache_loss_fraction}"
        )
    # Fail fast on unknown models, with the price table's own message.
    prices.price_for(from_model)
    prices.price_for(to_model)

    runs: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        if attempt.run_id is None:
            raise ValueError(
                "handoff analysis requires every record to carry a run_id; "
                f"found one without (model {attempt.model!r}, "
                f"task {attempt.task_id!r})"
            )
        runs.setdefault(attempt.run_id, []).append(attempt)
    if not runs:
        raise ValueError("no records: the log is empty")

    points_by_key: dict[Optional[int], HandoffPoint] = {}
    n_excluded = 0
    for run in runs.values():
        try:
            switch_after = _classify_run(run, from_model, to_model)
        except ValueError:
            n_excluded += 1
            continue
        point = points_by_key.setdefault(
            switch_after, HandoffPoint(switch_after=switch_after)
        )
        cost, cache_loss = _run_cost(
            run, prices, cached_fraction, cache_loss_fraction
        )
        point.runs += 1
        if any(a.success for a in run):
            point.successes += 1
        point.total_cost += cost
        point.cache_loss_cost += cache_loss

    if not points_by_key:
        raise ValueError(
            f"no runs classifiable as {from_model} -> {to_model} handoffs "
            f"({n_excluded} run(s) excluded)"
        )

    points = sorted(
        points_by_key.values(),
        key=lambda p: (p.switch_after is None, p.switch_after or 0),
    )
    best_rate = max(p.success_rate for p in points)

    def _recommendation_key(p: HandoffPoint) -> tuple:
        cps = p.cost_per_successful_run
        return (
            cps if cps is not None else float("inf"),
            p.mean_cost_per_run,
            p.switch_after is None,
            p.switch_after or 0,
        )

    eligible = [
        p
        for p in points
        if p.cost_per_successful_run is not None
        and p.success_rate >= best_rate - tolerance
    ]
    recommended = min(eligible, key=_recommendation_key) if eligible else None

    return HandoffReport(
        from_model=from_model,
        to_model=to_model,
        tolerance=tolerance,
        points=points,
        recommended=recommended,
        n_runs=sum(p.runs for p in points),
        n_excluded_runs=n_excluded,
    )


def build_handoff_insights(report: HandoffReport) -> list[str]:
    """Short human-readable takeaways that fall out of the curve."""
    insights: list[str] = []
    recommended = report.recommended
    never = next(
        (p for p in report.points if p.switch_after is None), None
    )
    if (
        recommended is not None
        and never is not None
        and recommended is not never
        and never.cost_per_successful_run
        and recommended.cost_per_successful_run is not None
    ):
        saving = 1.0 - (
            recommended.cost_per_successful_run / never.cost_per_successful_run
        )
        label = (
            "immediately (switch after 0 steps)"
            if recommended.switch_after == 0
            else f"after {recommended.switch_after} step(s)"
        )
        insights.append(
            f"Recommended: hand off {label} -- "
            f"${recommended.cost_per_successful_run:.4f} per successful run "
            f"at {recommended.success_rate:.0%} success, {saving:.0%} cheaper "
            f"per successful run than never switching "
            f"(${never.cost_per_successful_run:.4f} at "
            f"{never.success_rate:.0%})."
        )
    if report.n_excluded_runs:
        insights.append(
            f"{report.n_excluded_runs} run(s) were excluded: they involve "
            f"models outside the {report.from_model} -> {report.to_model} "
            f"pair, or escalate in the opposite direction."
        )
    for p in report.points:
        if p.runs and p.successes == 0:
            label = (
                "never switching"
                if p.switch_after is None
                else f"switching after {p.switch_after} step(s)"
            )
        else:
            continue
        insights.append(
            f"No run succeeded when {label} "
            f"({p.runs} run(s), ${p.total_cost:.4f} spent)."
        )
    return insights
