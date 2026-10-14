"""The bundled sample must make the tool's point: the cheapest model per
token is not the cheapest per successful task."""

from importlib import resources
from pathlib import Path

from cost_per_task.analyze import analyze, build_insights
from cost_per_task.prices import load_prices
from cost_per_task.records import load_records


def _sample_stats():
    path = Path(str(resources.files("cost_per_task").joinpath("data/sample_runs.jsonl")))
    prices = load_prices()
    return analyze(load_records(path), prices), prices


def test_sample_covers_three_models_twenty_tasks_each():
    stats, _ = _sample_stats()
    assert {s.model for s in stats} == {"gpt-5-mini", "claude-haiku-4-5", "gpt-5"}
    assert all(s.tasks_attempted == 20 for s in stats)


def test_sample_ranking_flips_against_per_token_price():
    stats, prices = _sample_stats()
    cheapest_token = prices.cheapest_per_token().model
    assert cheapest_token == "gpt-5-mini"
    assert stats[0].model != cheapest_token  # the flip
    assert stats[0].model == "claude-haiku-4-5"
    mini = next(s for s in stats if s.model == "gpt-5-mini")
    assert mini.success_rate < stats[0].success_rate
    assert mini.cost_per_successful_task > stats[0].cost_per_successful_task


def test_sample_produces_flip_insight():
    stats, prices = _sample_stats()
    insights = build_insights(stats, prices)
    assert any("NOT cheapest per successful task" in text for text in insights)
