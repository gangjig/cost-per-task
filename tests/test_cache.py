"""Cache scenarios: recompute one log under three caching assumptions.

Toy arithmetic: model "m" charges $10 per 1M input, $20 output, $1
cached input, so with input_tokens=1,000,000 and output_tokens=0 an
attempt costs exactly $10 uncached, and each cached token saves $9
per million. Model "n" has no cached price, so the cached-fraction
fallback applies. Model "other" ($5/$5, no cached price) exists for
the cross-model session test.
"""

import json
from importlib import resources
from pathlib import Path

import pytest

from cost_per_task.cache import analyze_cache_scenarios, build_cache_insights
from cost_per_task.cli import main
from cost_per_task.prices import Price, PriceTable, load_prices
from cost_per_task.records import Attempt, load_records
from cost_per_task.report import render_cache_json, render_cache_markdown

PRICES = PriceTable(
    {
        "m": Price(
            "m", input_per_m=10.0, output_per_m=20.0, cached_input_per_m=1.0
        ),
        "n": Price("n", input_per_m=10.0, output_per_m=20.0),
        "other": Price("other", input_per_m=5.0, output_per_m=5.0),
    }
)


def att(model, task, inp=1_000_000, out=0, cached=0, run=None, success=True):
    return Attempt(
        model=model,
        task_id=task,
        success=success,
        input_tokens=inp,
        output_tokens=out,
        cached_input_tokens=cached,
        run_id=run,
    )


def _row(report, model):
    return next(r for r in report.rows if r.model == model)


def test_three_scenarios_exact_math():
    attempts = [
        att("m", "t1", cached=0),
        att("m", "t1", cached=500_000),
        att("m", "t1", cached=800_000),
    ]
    report = analyze_cache_scenarios(attempts, PRICES)
    row = _row(report, "m")
    # with cache: 10 + (5 + 0.5) + (2 + 0.8); no cache: 3 x 10;
    # one bust: the 800K read is re-billed in full -> 10 + 5.5 + 10.
    assert round(row.with_cache_cost, 6) == 18.3
    assert round(row.no_cache_cost, 6) == 30.0
    assert round(row.one_bust_cost, 6) == 25.5
    assert round(row.cache_savings, 6) == 11.7
    assert round(row.cache_savings_share, 4) == 0.39
    assert round(row.no_cache_multiplier, 4) == 1.6393
    assert round(row.bust_uplift, 6) == 7.2
    assert round(row.bust_uplift_pct, 4) == 0.3934
    assert row.attempts == 3
    assert row.sessions == 1
    assert row.cached_tokens == 1_300_000
    assert report.n_sessions == 1
    assert report.n_attempts == 3
    assert report.n_busted_sessions == 1


def test_cached_fraction_fallback_when_no_cached_price():
    attempts = [att("n", "t1", cached=400_000)]
    report = analyze_cache_scenarios(attempts, PRICES)
    row = _row(report, "n")
    # Default fraction 0.5: cached rate 5 -> 0.6*10 + 0.4*5 = 8.
    assert round(row.with_cache_cost, 6) == 8.0
    assert round(row.no_cache_cost, 6) == 10.0
    # A lone attempt is its own session's largest read: bust = no cache.
    assert round(row.one_bust_cost, 6) == 10.0
    report = analyze_cache_scenarios(attempts, PRICES, cached_fraction=0.25)
    row = _row(report, "n")
    # Fraction 0.25: cached rate 2.5 -> 0.6*10 + 0.4*2.5 = 7.
    assert round(row.with_cache_cost, 6) == 7.0


def test_sessions_group_by_run_id_else_task_id():
    two_runs = [
        att("m", "t1", cached=500_000, run="r1"),
        att("m", "t1", cached=500_000, run="r2"),
    ]
    report = analyze_cache_scenarios(two_runs, PRICES)
    row = _row(report, "m")
    assert report.n_sessions == 2
    assert report.n_busted_sessions == 2  # one bust per run
    assert round(row.with_cache_cost, 6) == 11.0
    assert round(row.one_bust_cost, 6) == 20.0

    one_task = [att("m", "t1", cached=500_000), att("m", "t1", cached=500_000)]
    report = analyze_cache_scenarios(one_task, PRICES)
    row = _row(report, "m")
    assert report.n_sessions == 1
    assert report.n_busted_sessions == 1  # one bust for the whole task
    assert round(row.one_bust_cost, 6) == 15.5


def test_bust_picks_the_largest_cache_read():
    attempts = [
        att("m", "t1", cached=200_000),  # with-cache 8.2
        att("m", "t1", cached=900_000),  # with-cache 1.9 <- bust target
        att("m", "t1", cached=400_000),  # with-cache 6.4
    ]
    report = analyze_cache_scenarios(attempts, PRICES)
    row = _row(report, "m")
    assert round(row.with_cache_cost, 6) == 16.5
    assert round(row.one_bust_cost, 6) == 24.6  # 8.2 + 10 + 6.4


def test_no_cached_tokens_scenarios_coincide():
    attempts = [att("m", "t1"), att("m", "t1"), att("m", "t2")]
    report = analyze_cache_scenarios(attempts, PRICES)
    row = _row(report, "m")
    assert row.with_cache_cost == row.no_cache_cost == row.one_bust_cost
    assert row.cache_savings == 0.0
    assert report.n_busted_sessions == 0
    insights = build_cache_insights(report)
    assert any("coincide" in text for text in insights)


def test_cross_model_session_bust_lands_on_owning_model():
    attempts = [
        att("m", "t1", cached=100_000),
        att("other", "t1", cached=900_000),
    ]
    report = analyze_cache_scenarios(attempts, PRICES)
    assert report.n_sessions == 1
    m_row = _row(report, "m")
    other_row = _row(report, "other")
    assert m_row.sessions == 1 and other_row.sessions == 1
    # The bust target is "other"'s attempt (the larger read).
    assert m_row.one_bust_cost == m_row.with_cache_cost
    # other, fraction fallback 0.5 -> cached rate 2.5:
    # with cache 0.1*5 + 0.9*2.5 = 2.75; busted 1M * 5 = 5.
    assert round(other_row.with_cache_cost, 6) == 2.75
    assert round(other_row.one_bust_cost, 6) == 5.0


def test_rows_ranked_by_cache_savings():
    attempts = [
        att("m", "t1", cached=800_000),  # saves 7.2
        att("n", "t2", cached=400_000),  # saves 2.0 at the default fraction
    ]
    report = analyze_cache_scenarios(attempts, PRICES)
    assert [r.model for r in report.rows] == ["m", "n"]


def test_empty_log_raises():
    with pytest.raises(ValueError, match="no records"):
        analyze_cache_scenarios([], PRICES)


def test_unknown_model_raises():
    with pytest.raises(ValueError, match="no price known"):
        analyze_cache_scenarios([att("mystery", "t1")], PRICES)


def test_insights_headline_numbers():
    attempts = [
        att("m", "t1", cached=0),
        att("m", "t1", cached=500_000),
        att("m", "t1", cached=800_000),
    ]
    report = analyze_cache_scenarios(attempts, PRICES)
    insights = build_cache_insights(report)
    assert any("pays for 39% of the bill" in text for text in insights)
    assert any("1.64x" in text for text in insights)
    assert any("+39.3%" in text for text in insights)


def test_render_markdown_and_json():
    attempts = [att("m", "t1", cached=500_000), att("m", "t1", cached=800_000)]
    report = analyze_cache_scenarios(attempts, PRICES)
    insights = build_cache_insights(report)
    md = render_cache_markdown(report, insights)
    assert "# Cache scenarios" in md
    assert "| m |" in md
    data = json.loads(render_cache_json(report, insights))
    assert data["metric"] == "cache_scenarios_usd"
    assert data["n_sessions"] == 1
    assert data["totals"]["cached_tokens"] == 1_300_000
    assert data["models"][0]["model"] == "m"
    assert data["models"][0]["no_cache_multiplier"] == 2.4096


def _write_log(tmp_path, records):
    path = tmp_path / "log.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return path


def test_cli_cache_markdown_and_json(tmp_path, capsys):
    path = _write_log(
        tmp_path,
        [
            {
                "model": "gpt-5",
                "task_id": "t1",
                "success": True,
                "input_tokens": 100_000,
                "output_tokens": 500,
                "cached_input_tokens": 60_000,
            }
        ],
    )
    assert main(["cache", str(path)]) == 0
    out = capsys.readouterr().out
    assert "# Cache scenarios" in out
    assert "gpt-5" in out
    assert main(["cache", str(path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["metric"] == "cache_scenarios_usd"


def test_cli_cache_missing_file_returns_2(tmp_path, capsys):
    assert main(["cache", str(tmp_path / "nope.jsonl")]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_demo_cache(capsys):
    assert main(["demo-cache"]) == 0
    out = capsys.readouterr().out
    assert "# Cache scenarios" in out
    assert "gpt-5" in out and "claude-sonnet-4-5" in out


def test_bundled_sample_cache_story():
    path = Path(
        str(
            resources.files("cost_per_task").joinpath("data/sample_cache.jsonl")
        )
    )
    report = analyze_cache_scenarios(load_records(path), load_prices())
    assert {r.model for r in report.rows} == {"gpt-5", "claude-sonnet-4-5"}
    assert report.n_sessions == 10
    assert report.n_busted_sessions == 10
    for row in report.rows:
        assert row.no_cache_multiplier > 2.5
        assert row.bust_uplift > 0
