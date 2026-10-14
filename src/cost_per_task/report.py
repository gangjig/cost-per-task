"""Render ranked results as a Markdown table or as JSON."""

from __future__ import annotations

import json
from typing import Optional

from .analyze import ModelStats


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
