"""Render ranked results as a Markdown table or as JSON."""

from __future__ import annotations

import json
from typing import Optional

from .analyze import ModelStats
from .cache import CacheModelRow, CacheReport
from .handoff import HandoffPoint, HandoffReport


def _money(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"${value:,.4f}"


def _seconds(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}s"


def render_markdown(stats: list[ModelStats], insights: list[str]) -> str:
    lines = ["# Cost per successful task", ""]
    if not stats:
        lines.append("_No records._")
        return "\n".join(lines) + "\n"

    lines += [
        "| Rank | Model | Tasks | Success rate | Attempts | Total cost | "
        "Cost / successful task | Median latency | Tokens / task |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, s in enumerate(stats, start=1):
        lines.append(
            f"| {rank} | {s.model} | {s.tasks_attempted} | "
            f"{s.success_rate:.0%} | {s.attempts} | {_money(s.total_cost)} | "
            f"{_money(s.cost_per_successful_task)} | "
            f"{_seconds(s.median_latency_s)} | {s.avg_tokens_per_task:,.0f} |"
        )
    if insights:
        lines += ["", "## Insights", ""]
        lines += [f"- {text}" for text in insights]
    return "\n".join(lines) + "\n"


def stats_to_dict(stats: list[ModelStats], insights: list[str]) -> dict:
    return {
        "metric": "cost_per_successful_task_usd",
        "models": [
            {
                "rank": rank,
                "model": s.model,
                "tasks_attempted": s.tasks_attempted,
                "tasks_succeeded": s.tasks_succeeded,
                "success_rate": round(s.success_rate, 4),
                "attempts": s.attempts,
                "avg_attempts_per_task": round(s.avg_attempts_per_task, 3),
                "total_cost_usd": round(s.total_cost, 6),
                "cache_loss_cost_usd": round(s.cache_loss_cost, 6),
                "switches_in": s.switches_in,
                "cost_per_successful_task_usd": (
                    round(s.cost_per_successful_task, 6)
                    if s.cost_per_successful_task is not None
                    else None
                ),
                "cost_per_attempt_usd": round(s.cost_per_attempt, 6),
                "median_latency_s": (
                    round(s.median_latency_s, 3)
                    if s.median_latency_s is not None
                    else None
                ),
                "avg_tokens_per_task": round(s.avg_tokens_per_task, 1),
            }
            for rank, s in enumerate(stats, start=1)
        ],
        "insights": insights,
    }


def render_json(stats: list[ModelStats], insights: list[str]) -> str:
    return json.dumps(stats_to_dict(stats, insights), indent=2) + "\n"


def _switch_label(point: HandoffPoint) -> str:
    if point.switch_after is None:
        return "never"
    return str(point.switch_after)


def render_handoff_markdown(
    report: HandoffReport, insights: list[str]
) -> str:
    lines = [
        f"# Handoff curve: {report.from_model} -> {report.to_model}",
        "",
        f"{report.n_runs} run(s) across {len(report.points)} switch "
        f"point(s); success rates within {report.tolerance:.0%} of the "
        f"best are eligible for the recommendation.",
        "",
        "| Switch after | Runs | Success rate | Total cost | "
        "Cost / run | Cost / successful run |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for p in report.points:
        marker = (
            " **(recommended)**" if p is report.recommended else ""
        )
        lines.append(
            f"| {_switch_label(p)}{marker} | {p.runs} | "
            f"{p.success_rate:.0%} | {_money(p.total_cost)} | "
            f"{_money(p.mean_cost_per_run)} | "
            f"{_money(p.cost_per_successful_run)} |"
        )
    if insights:
        lines += ["", "## Insights", ""]
        lines += [f"- {text}" for text in insights]
    return "\n".join(lines) + "\n"


def handoff_to_dict(report: HandoffReport, insights: list[str]) -> dict:
    def point_dict(p: HandoffPoint) -> dict:
        return {
            "switch_after": p.switch_after,
            "runs": p.runs,
            "successes": p.successes,
            "success_rate": round(p.success_rate, 4),
            "total_cost_usd": round(p.total_cost, 6),
            "cache_loss_cost_usd": round(p.cache_loss_cost, 6),
            "mean_cost_per_run_usd": round(p.mean_cost_per_run, 6),
            "cost_per_successful_run_usd": (
                round(p.cost_per_successful_run, 6)
                if p.cost_per_successful_run is not None
                else None
            ),
            "recommended": p is report.recommended,
        }

    return {
        "metric": "cost_per_successful_run_usd",
        "from_model": report.from_model,
        "to_model": report.to_model,
        "tolerance": report.tolerance,
        "n_runs": report.n_runs,
        "n_excluded_runs": report.n_excluded_runs,
        "best_success_rate": round(report.best_success_rate, 4),
        "recommended_switch_after": (
            report.recommended.switch_after if report.recommended else None
        ),
        "points": [point_dict(p) for p in report.points],
        "insights": insights,
    }


def render_handoff_json(report: HandoffReport, insights: list[str]) -> str:
    return json.dumps(handoff_to_dict(report, insights), indent=2) + "\n"


def _multiplier(row: CacheModelRow) -> str:
    mult = row.no_cache_multiplier
    if mult is None:
        return "—"
    return f"{mult:.2f}x"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"+{value:.1%}"


def render_cache_markdown(report: CacheReport, insights: list[str]) -> str:
    lines = [
        "# Cache scenarios",
        "",
        f"{report.n_sessions} session(s), {report.n_attempts} attempt(s). "
        f"One bust = each session's largest cache read re-billed at the "
        f"full input rate ({report.n_busted_sessions} session(s) had a "
        f"cache read to bust).",
        "",
        "| Model | Sessions | Attempts | With cache | No cache | "
        "No-cache x | One bust | Bust adds |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in report.rows:
        lines.append(
            f"| {r.model} | {r.sessions} | {r.attempts} | "
            f"{_money(r.with_cache_cost)} | {_money(r.no_cache_cost)} | "
            f"{_multiplier(r)} | {_money(r.one_bust_cost)} | "
            f"{_pct(r.bust_uplift_pct)} |"
        )
    if len(report.rows) > 1:
        total_mult = (
            f"{report.no_cache_total / report.with_cache_total:.2f}x"
            if report.with_cache_total > 0
            else "—"
        )
        total_bust_pct = (
            f"+{(report.one_bust_total - report.with_cache_total) / report.with_cache_total:.1%}"
            if report.with_cache_total > 0
            else "—"
        )
        lines.append(
            f"| **All models** | {report.n_sessions} | "
            f"{report.n_attempts} | {_money(report.with_cache_total)} | "
            f"{_money(report.no_cache_total)} | {total_mult} | "
            f"{_money(report.one_bust_total)} | {total_bust_pct} |"
        )
    if insights:
        lines += ["", "## Insights", ""]
        lines += [f"- {text}" for text in insights]
    return "\n".join(lines) + "\n"


def cache_to_dict(report: CacheReport, insights: list[str]) -> dict:
    def row_dict(r: CacheModelRow) -> dict:
        return {
            "model": r.model,
            "sessions": r.sessions,
            "attempts": r.attempts,
            "cached_tokens": r.cached_tokens,
            "with_cache_cost_usd": round(r.with_cache_cost, 6),
            "no_cache_cost_usd": round(r.no_cache_cost, 6),
            "one_bust_cost_usd": round(r.one_bust_cost, 6),
            "cache_savings_usd": round(r.cache_savings, 6),
            "cache_savings_share": round(r.cache_savings_share, 4),
            "no_cache_multiplier": (
                round(r.no_cache_multiplier, 4)
                if r.no_cache_multiplier is not None
                else None
            ),
            "bust_uplift_usd": round(r.bust_uplift, 6),
            "bust_uplift_pct": (
                round(r.bust_uplift_pct, 4)
                if r.bust_uplift_pct is not None
                else None
            ),
        }

    return {
        "metric": "cache_scenarios_usd",
        "n_sessions": report.n_sessions,
        "n_attempts": report.n_attempts,
        "n_busted_sessions": report.n_busted_sessions,
        "totals": {
            "with_cache_cost_usd": round(report.with_cache_total, 6),
            "no_cache_cost_usd": round(report.no_cache_total, 6),
            "one_bust_cost_usd": round(report.one_bust_total, 6),
            "cached_tokens": report.total_cached_tokens,
        },
        "models": [row_dict(r) for r in report.rows],
        "insights": insights,
    }


def render_cache_json(report: CacheReport, insights: list[str]) -> str:
    return json.dumps(cache_to_dict(report, insights), indent=2) + "\n"
