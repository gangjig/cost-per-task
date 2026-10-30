"""Render ranked results as a Markdown table or as JSON."""

from __future__ import annotations

import json
from typing import Optional

from .analyze import ModelStats
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
