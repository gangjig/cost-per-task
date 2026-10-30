"""Handoff curves: aggregate a handoff-experiment log by switch point.

Each run starts on the strong model and hands off to the cheap model
after a chosen number of steps. Attempts carry a run_id identifying the
run. With input_tokens=1,000,000 and output_tokens=0, one attempt costs
exactly its model's input price: strong $10, cheap $1.
"""

import json
from importlib import resources
from pathlib import Path

import pytest

from cost_per_task.cli import main
from cost_per_task.handoff import analyze_handoff, build_handoff_insights
from cost_per_task.prices import Price, PriceTable, load_prices
from cost_per_task.records import Attempt, load_records

PRICES = PriceTable(
    {
        "strong": Price("strong", input_per_m=10.0, output_per_m=20.0),
        "cheap": Price(
            "cheap", input_per_m=1.0, output_per_m=2.0, cached_input_per_m=0.5
        ),
        "other": Price("other", input_per_m=5.0, output_per_m=5.0),
    }
)


def att(model, task, success, run, inp=1_000_000, out=0):
    return Attempt(
        model=model,
        task_id=task,
        success=success,
        input_tokens=inp,
        output_tokens=out,
        run_id=run,
    )


def _points(report):
    return {p.switch_after: p for p in report.points}


def test_run_id_parsed_and_optional():
    with_id = Attempt.from_dict(
        {
            "model": "m",
            "task_id": "t",
            "success": True,
            "input_tokens": 1,
            "output_tokens": 2,
            "run_id": "r-1",
        }
    )
    without = Attempt.from_dict(
        {
            "model": "m",
            "task_id": "t",
            "success": True,
            "input_tokens": 1,
            "output_tokens": 2,
        }
    )
    assert with_id.run_id == "r-1"
    assert without.run_id is None


def test_missing_run_id_fails_loudly():
    attempts = [
        Attempt(
            model="strong", task_id="t1", success=True,
            input_tokens=1, output_tokens=0,
        )
    ]
    with pytest.raises(ValueError, match="run_id"):
        analyze_handoff(attempts, PRICES, "strong", "cheap")


def test_switch_point_classification():
    attempts = [
        att("strong", "t1", True, "r-never"),
        att("strong", "t1", False, "r-never"),
        att("cheap", "t2", True, "r-zero"),
        att("strong", "t3", False, "r-two"),
        att("strong", "t3", False, "r-two"),
        att("cheap", "t3", True, "r-two"),
        att("strong", "t4", False, "r-one"),
        att("cheap", "t4", True, "r-one"),
    ]
    report = analyze_handoff(attempts, PRICES, "strong", "cheap")
    points = _points(report)
    assert set(points) == {0, 1, 2, None}
    assert report.n_runs == 4
    assert report.n_excluded_runs == 0
    # Points are ordered by switch point, "never" last.
    assert [p.switch_after for p in report.points] == [0, 1, 2, None]


def test_cost_and_success_aggregation():
    # never: two strong attempts, success -> cost 20, 1 success.
    # after 1: strong fails, cheap succeeds -> cost 11, 1 success.
    attempts = [
        att("strong", "t1", False, "r-never"),
        att("strong", "t1", True, "r-never"),
        att("strong", "t2", False, "r-one"),
        att("cheap", "t2", True, "r-one"),
    ]
    report = analyze_handoff(attempts, PRICES, "strong", "cheap")
    points = _points(report)
    assert points[None].total_cost == pytest.approx(20.0)
    assert points[None].cost_per_successful_run == pytest.approx(20.0)
    assert points[1].total_cost == pytest.approx(11.0)
    assert points[1].cost_per_successful_run == pytest.approx(11.0)
    # Same success rate, cheaper per success -> switch after 1 wins.
    assert report.recommended is points[1]


def test_recommendation_respects_tolerance():
    # Point 0 is cheapest per success but only 2/4 succeed; "never" is
    # 4/4. With the default tolerance (0.05) point 0 is not eligible.
    attempts = []
    for i in range(4):
        attempts.append(att("cheap", f"c{i}", i < 2, f"r-zero-{i}"))
        attempts.append(att("strong", f"s{i}", True, f"r-never-{i}"))
    strict = analyze_handoff(attempts, PRICES, "strong", "cheap")
    assert strict.recommended.switch_after is None
    loose = analyze_handoff(
        attempts, PRICES, "strong", "cheap", tolerance=0.6
    )
    assert loose.recommended.switch_after == 0


def test_zero_success_point_has_no_cost_per_success():
    attempts = [
        att("cheap", "t1", False, "r-zero"),
        att("strong", "t2", True, "r-never"),
    ]
    report = analyze_handoff(attempts, PRICES, "strong", "cheap")
    points = _points(report)
    assert points[0].successes == 0
    assert points[0].cost_per_successful_run is None
    assert report.recommended is points[None]
    insights = build_handoff_insights(report)
    assert any("No run succeeded" in text for text in insights)


def test_excluded_runs_are_counted_not_dropped_silently():
    attempts = [
        att("strong", "t1", True, "r-ok"),
        att("other", "t2", True, "r-third-model"),
        att("strong", "t2", False, "r-third-model"),
        att("cheap", "t3", False, "r-escalation"),
        att("strong", "t3", True, "r-escalation"),
    ]
    report = analyze_handoff(attempts, PRICES, "strong", "cheap")
    assert report.n_runs == 1
    assert report.n_excluded_runs == 2
    insights = build_handoff_insights(report)
    assert any("excluded" in text for text in insights)


def test_switch_back_keeps_first_switch_point():
    attempts = [
        att("strong", "t1", False, "r-back"),
        att("cheap", "t1", False, "r-back"),
        att("strong", "t1", False, "r-back"),
        att("cheap", "t1", True, "r-back"),
    ]
    report = analyze_handoff(attempts, PRICES, "strong", "cheap")
    points = _points(report)
    assert set(points) == {1}
    assert points[1].total_cost == pytest.approx(10 + 1 + 10 + 1)


def test_cache_loss_charged_at_switch():
    # strong (1M input) -> cheap (1M input). Fraction 1.0: the switch
    # re-bills the accumulated 1M context at cheap's $1/M input price.
    attempts = [
        att("strong", "t1", False, "r1"),
        att("cheap", "t1", True, "r1"),
    ]
    report = analyze_handoff(
        attempts, PRICES, "strong", "cheap", cache_loss_fraction=1.0
    )
    point = _points(report)[1]
    assert point.cache_loss_cost == pytest.approx(1.0)
    assert point.total_cost == pytest.approx(10 + 1 + 1)


def test_no_classifiable_runs_raises():
    attempts = [att("other", "t1", True, "r1")]
    with pytest.raises(ValueError, match="no runs classifiable"):
        analyze_handoff(attempts, PRICES, "strong", "cheap")


def test_empty_log_raises():
    with pytest.raises(ValueError, match="no records"):
        analyze_handoff([], PRICES, "strong", "cheap")


def test_same_and_unknown_models_rejected():
    attempts = [att("strong", "t1", True, "r1")]
    with pytest.raises(ValueError, match="must differ"):
        analyze_handoff(attempts, PRICES, "strong", "strong")
    with pytest.raises(ValueError, match="no price known"):
        analyze_handoff(attempts, PRICES, "strong", "nope")


def _write_log(tmp_path, rows):
    path = tmp_path / "log.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return path


def _write_prices(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps(
            {
                "models": {
                    "strong": {"input_per_m": 10.0, "output_per_m": 20.0},
                    "cheap": {
                        "input_per_m": 1.0,
                        "output_per_m": 2.0,
                        "cached_input_per_m": 0.5,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _row(model, task, success, run):
    return {
        "model": model,
        "task_id": task,
        "success": success,
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "run_id": run,
    }


def test_cli_handoff_markdown_and_json(tmp_path, capsys):
    prices = _write_prices(tmp_path)
    path = _write_log(
        tmp_path,
        [
            _row("strong", "t1", False, "r-never"),
            _row("strong", "t1", True, "r-never"),
            _row("strong", "t2", False, "r-one"),
            _row("cheap", "t2", True, "r-one"),
        ],
    )
    assert (
        main(
            [
                "handoff", str(path),
                "--from-model", "strong", "--to-model", "cheap",
                "--prices", str(prices),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Handoff curve: strong -> cheap" in out
    assert "(recommended)" in out

    assert (
        main(
            [
                "handoff", str(path),
                "--from-model", "strong", "--to-model", "cheap",
                "--prices", str(prices),
                "--format", "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["recommended_switch_after"] == 1
    assert payload["n_runs"] == 2


def test_cli_handoff_requires_run_id(tmp_path, capsys):
    prices = _write_prices(tmp_path)
    path = tmp_path / "plain.jsonl"
    row = _row("strong", "t1", True, "r1")
    del row["run_id"]
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert (
        main(
            [
                "handoff", str(path),
                "--from-model", "strong", "--to-model", "cheap",
                "--prices", str(prices),
            ]
        )
        == 2
    )
    assert "run_id" in capsys.readouterr().err


def test_bundled_handoff_sample_curve():
    path = Path(
        str(
            resources.files("cost_per_task").joinpath(
                "data/sample_handoff.jsonl"
            )
        )
    )
    attempts = load_records(path)
    assert len(attempts) == 300
    assert all(a.run_id is not None for a in attempts)
    assert len({a.run_id for a in attempts}) == 60

    report = analyze_handoff(
        attempts, load_prices(), "gpt-5", "gpt-5-mini"
    )
    points = _points(report)
    assert {k: p.successes for k, p in points.items()} == {
        0: 7,
        1: 10,
        2: 11,
        3: 11,
        None: 11,
    }
    assert report.recommended.switch_after == 2
    never = points[None]
    rec = points[2]
    assert rec.cost_per_successful_run < 0.6 * never.cost_per_successful_run


def test_cli_demo_handoff(capsys):
    assert (
        main(
            [
                "demo-handoff",
                "--from-model", "gpt-5", "--to-model", "gpt-5-mini",
                "--format", "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["recommended_switch_after"] == 2
    assert payload["n_runs"] == 60
