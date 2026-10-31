"""Cache scenarios: what prompt caching is worth, and what losing it costs.

The main analysis reports what a log *actually* cost -- cached input
tokens billed at the cached rate, as recorded. That single number hides
how much of the bill the prompt cache is carrying. This module recomputes
the same log under three scenarios:

* **with cache** -- as recorded: cached tokens at the cached rate.
* **no cache** -- every cached token re-billed at the full input rate:
  what identical traffic would cost with prompt caching off.
* **one bust** -- exactly one full cache invalidation per session: the
  session's largest cache read is re-billed at the full input rate
  (the accumulated prefix must be re-ingested), every other attempt
  bills as recorded. A worst-case single bust, placed where it hurts
  most.

Sessions are the unit caching works on: attempts sharing a `run_id`
when present, else a `task_id`, in log order. The gap between the first
two scenarios is the cache's contribution to the bill; the third prices
cache fragility -- how much rides on the cache surviving a session.

Motivated by an Agent Arena cost analysis reported in Latent Space
AINews (2026-10-31): across 26K sessions, top agent tasks cost 3.6x
more with prompt caching off, and a single cache invalidation raised
the bill by ~5%:
https://www.latent.space/p/ainews-google-admits-ai-mode-hurts-search-revenue-15-500b-drop-worst-since-2004-ipo-ai-bear-market-c/
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Iterable, Optional

from .analyze import DEFAULT_CACHED_FRACTION
from .prices import PriceTable
from .records import Attempt, attempt_cost


@dataclass
class CacheModelRow:
    """One model's costs under the three cache scenarios."""

    model: str
    attempts: int = 0
    sessions: int = 0  # sessions this model appears in
    cached_tokens: int = 0
    with_cache_cost: float = 0.0
    no_cache_cost: float = 0.0
    one_bust_cost: float = 0.0

    @property
    def cache_savings(self) -> float:
        """USD the cache saved against the no-cache scenario."""
        return self.no_cache_cost - self.with_cache_cost

    @property
    def cache_savings_share(self) -> float:
        """Fraction of the no-cache bill the cache paid for (0..1)."""
        if self.no_cache_cost == 0.0:
            return 0.0
        return self.cache_savings / self.no_cache_cost

    @property
    def no_cache_multiplier(self) -> Optional[float]:
        """How many times more expensive the log is with caching off."""
        if self.with_cache_cost == 0.0:
            return None
        return self.no_cache_cost / self.with_cache_cost

    @property
    def bust_uplift(self) -> float:
        """USD added by one worst-case invalidation per session."""
        return self.one_bust_cost - self.with_cache_cost

    @property
    def bust_uplift_pct(self) -> Optional[float]:
        """The bust uplift as a fraction of the with-cache bill."""
        if self.with_cache_cost == 0.0:
            return None
        return self.bust_uplift / self.with_cache_cost


@dataclass
class CacheReport:
    rows: list[CacheModelRow]  # ranked by cache savings, biggest first
    n_sessions: int
    n_attempts: int
    n_busted_sessions: int  # sessions whose bust actually raised the bill

    @property
    def with_cache_total(self) -> float:
        return sum(r.with_cache_cost for r in self.rows)

    @property
    def no_cache_total(self) -> float:
        return sum(r.no_cache_cost for r in self.rows)

    @property
    def one_bust_total(self) -> float:
        return sum(r.one_bust_cost for r in self.rows)

    @property
    def total_cached_tokens(self) -> int:
        return sum(r.cached_tokens for r in self.rows)


def _session_key(attempt: Attempt) -> str:
    return attempt.run_id if attempt.run_id is not None else attempt.task_id


def analyze_cache_scenarios(
    attempts: Iterable[Attempt],
    prices: PriceTable,
    cached_fraction: float = DEFAULT_CACHED_FRACTION,
) -> CacheReport:
    """Recompute a log's cost under the three cache scenarios, per model.

    Sessions are groups of attempts sharing a `run_id` (else `task_id`),
    in log order; the bust in each session re-bills that session's
    largest cache read at the full input rate. Raises ValueError on an
    empty log or a model missing from the price table.
    """
    attempts = list(attempts)
    if not attempts:
        raise ValueError("no records: the log is empty")
    # Fail fast on unknown models, with the price table's own message.
    for model in {a.model for a in attempts}:
        prices.price_for(model)

    sessions: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        sessions.setdefault(_session_key(attempt), []).append(attempt)

    rows: dict[str, CacheModelRow] = {}
    n_busted = 0
    for session in sessions.values():
        # The bust target: the attempt with the largest cache read
        # (first such attempt on ties). Only counts as busted when the
        # target actually read from cache.
        bust_index = max(
            range(len(session)),
            key=lambda i: session[i].cached_input_tokens,
        )
        if session[bust_index].cached_input_tokens > 0:
            n_busted += 1
        models_in_session = set()
        for i, attempt in enumerate(session):
            price = prices.price_for(attempt.model)
            row = rows.setdefault(
                attempt.model, CacheModelRow(model=attempt.model)
            )
            uncached = dataclasses.replace(attempt, cached_input_tokens=0)
            row.attempts += 1
            row.cached_tokens += attempt.cached_input_tokens
            row.with_cache_cost += attempt_cost(
                attempt, price, cached_fraction
            )
            row.no_cache_cost += attempt_cost(uncached, price, cached_fraction)
            row.one_bust_cost += attempt_cost(
                uncached if i == bust_index else attempt,
                price,
                cached_fraction,
            )
            models_in_session.add(attempt.model)
        for model in models_in_session:
            rows[model].sessions += 1

    ranked = sorted(
        rows.values(),
        key=lambda r: (-round(r.cache_savings, 10), r.model),
    )
    return CacheReport(
        rows=ranked,
        n_sessions=len(sessions),
        n_attempts=len(attempts),
        n_busted_sessions=n_busted,
    )


def build_cache_insights(report: CacheReport) -> list[str]:
    """Short human-readable takeaways that fall out of the scenarios."""
    insights: list[str] = []
    if report.total_cached_tokens == 0:
        insights.append(
            "No cached input tokens in this log: all three scenarios "
            "coincide. If the harness should be reusing a prompt prefix "
            "across attempts, the cache is not engaging at all."
        )
        return insights

    with_total = report.with_cache_total
    no_total = report.no_cache_total
    bust_total = report.one_bust_total
    if with_total > 0 and no_total > with_total:
        insights.append(
            f"Prompt caching pays for {1.0 - with_total / no_total:.0%} "
            f"of the bill: the same traffic costs ${no_total:.4f} with "
            f"caching off vs ${with_total:.4f} as recorded "
            f"({no_total / with_total:.2f}x). Cache hit rate is a "
            f"first-class cost metric for this workload, ahead of "
            f"per-token price."
        )
    if with_total > 0 and bust_total > with_total:
        insights.append(
            f"One full invalidation per session -- re-billing each "
            f"session's largest cache read at the full input rate -- "
            f"adds ${bust_total - with_total:.4f} "
            f"(+{(bust_total - with_total) / with_total:.1%}) over the "
            f"recorded bill, across {report.n_busted_sessions} busted "
            f"session(s). That is the budget at risk if the cache "
            f"strategy changes mid-flight."
        )
    if len(report.rows) > 1:
        top = report.rows[0]
        if top.cache_savings > 0:
            insights.append(
                f"{top.model} leans on the cache hardest: caching saves "
                f"{top.cache_savings_share:.0%} of its no-cache bill "
                f"(${top.cache_savings:.4f} of ${top.no_cache_cost:.4f})."
            )
    for row in report.rows:
        if row.cached_tokens == 0 and row.attempts > 0:
            insights.append(
                f"{row.model} recorded no cached tokens; its three "
                f"scenarios coincide."
            )
    return insights
