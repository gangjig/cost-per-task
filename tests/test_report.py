import json

from cost_per_task.analyze import analyze
from cost_per_task.prices import Price, PriceTable
from cost_per_task.records import Attempt
from cost_per_task.report import render_json, render_markdown

PRICES = PriceTable({
    "cheap-flaky": Price("cheap-flaky", input_per_m=1.0, output_per_m=1.0),
    "steady": Price("steady", input_per_m=5.0, output_per_m=5.0),
})


def _flip_fixture():
    """cheap-flaky is cheapest per token but needs 4 attempts for 1 success
    (4 x $2 = $8/success); steady does it in one $10 attempt... wait, tune:
    steady input is 1M @ $5 = $5/success, so steady must win."""
    attempts = []
    for _ in range(3):
        attempts.append(Attempt("cheap-flaky", "t1", False,
                                input_tokens=1_000_000, output_tokens=1_000_000))
    attempts.append(Attempt("cheap-flaky", "t1", True,
                            input_tokens=1_000_000, output_tokens=1_000_000))
    attempts.append(Attempt("steady", "t1", True,
                            input_tokens=1_000_000, output_tokens=0))
    return attempts


def test_markdown_ranking_order_and_winner():
    from cost_per_task.analyze import build_insights
    stats = analyze(_flip_fixture(), PRICES)
    assert stats[0].model == "steady"  # $5/success beats $8/success
    md = render_markdown(stats, build_insights(stats, PRICES))
    assert md.index("steady") < md.index("cheap-flaky")
    assert "| 1 | steady |" in md
    assert "$5.0000" in md and "$8.0000" in md


def test_markdown_insight_names_the_flip():
    from cost_per_task.analyze import build_insights
    stats = analyze(_flip_fixture(), PRICES)
    md = render_markdown(stats, build_insights(stats, PRICES))
    assert "Cheapest per token (cheap-flaky) is NOT cheapest per successful task" in md


def test_json_structure_and_null_cost_for_zero_success():
    attempts = _flip_fixture() + [
        Attempt("cheap-flaky", "t2", False, input_tokens=10, output_tokens=10),
        Attempt("steady", "t2", False, input_tokens=10, output_tokens=10),
    ]
    payload = json.loads(render_json(analyze(attempts, PRICES), []))
    assert payload["metric"] == "cost_per_successful_task_usd"
    first = payload["models"][0]
    assert first["rank"] == 1 and first["model"] == "steady"
    assert first["tasks_attempted"] == 2
    assert first["success_rate"] == 0.5
    assert isinstance(first["cost_per_successful_task_usd"], float)
    assert isinstance(payload["insights"], list)


def test_markdown_handles_zero_success_with_dash():
    attempts = [Attempt("steady", "t1", False, input_tokens=10, output_tokens=10)]
    md = render_markdown(analyze(attempts, PRICES), [])
    assert "—" in md  # cost per successful task is undefined, shown as a dash
