"""Cache-loss accounting for router/escalation logs.

When a task (one session) switches models mid-flight, the destination
model cannot reuse the previous model's prompt cache: the context
accumulated so far is re-billed once at the destination's full input
price, scaled by cache_loss_fraction. Opt-in; default is off.
"""

import json

import pytest

from cost_per_task.analyze import analyze, build_insights
from cost_per_task.cli import main
from cost_per_task.prices import Price, PriceTable
from cost_per_task.records import Attempt, cache_loss_charge

PRICES = PriceTable({
    "cheap": Price("cheap", input_per_m=1.0, output_per_m=2.0, cached_input_per_m=0.5),
    "pro": Price("pro", input_per_m=10.0, output_per_m=20.0),
})
PRO = PRICES.price_for("pro")


def att(model, task, success, inp=1_000_000, out=0):
    return Attempt(model=model, task_id=task, success=success,
                   input_tokens=inp, output_tokens=out)


def _escalation():
    """cheap tries t1 twice and fails, then the session escalates to pro.

    Attempt costs: cheap $1 + $1; pro $10. Context accumulated in t1 by
    the switch: 2,000,000 input tokens.
    """
    return [
        att("cheap", "t1", False),
        att("cheap", "t1", False),
        att("pro", "t1", True),
    ]


def _by_model(stats):
    return {s.model: s for s in stats}


def test_charge_math_unit():
    # 2M tokens re-billed at pro's $10/M input price.
    assert cache_loss_charge(2_000_000, PRO, 1.0) == pytest.approx(20.00)
    assert cache_loss_charge(2_000_000, PRO, 0.5) == pytest.approx(10.00)
    assert cache_loss_charge(2_000_000, PRO, 0.0) == 0.0
    assert cache_loss_charge(0, PRO, 1.0) == 0.0


def test_switch_rebills_accumulated_context_at_destination_price():
    stats = _by_model(analyze(_escalation(), PRICES, cache_loss_fraction=1.0))
    pro = stats["pro"]
    # $10 attempt + 2M accumulated tokens re-billed @ $10/M = $20 cache loss.
    assert pro.cache_loss_cost == pytest.approx(20.00)
    assert pro.switches_in == 1
    assert pro.total_cost == pytest.approx(30.00)
    assert pro.cost_per_successful_task == pytest.approx(30.00)
    cheap = stats["cheap"]
    assert cheap.total_cost == pytest.approx(2.00)
    assert cheap.cache_loss_cost == 0.0
    assert cheap.switches_in == 0


def test_escalation_without_cache_loss_keeps_old_totals():
    attempts = _escalation()
    default = _by_model(analyze(attempts, PRICES))
    explicit_zero = _by_model(analyze(attempts, PRICES, cache_loss_fraction=0.0))
    for stats in (default, explicit_zero):
        assert stats["pro"].total_cost == pytest.approx(10.00)
        assert stats["pro"].cache_loss_cost == 0.0
        assert stats["pro"].switches_in == 0
        assert stats["cheap"].total_cost == pytest.approx(2.00)


def test_fraction_scales_the_charge():
    stats = _by_model(analyze(_escalation(), PRICES, cache_loss_fraction=0.5))
    assert stats["pro"].cache_loss_cost == pytest.approx(10.00)
    assert stats["pro"].total_cost == pytest.approx(20.00)


def test_multiple_switches_accumulate():
    attempts = [
        att("cheap", "t1", False),   # $1; context now 1M
        att("pro", "t1", False),     # $10 + re-bill 1M @ $10 = $10
        att("cheap", "t1", False),   # $1 + re-bill 2M @ $1 = $2
        att("pro", "t1", True),      # $10 + re-bill 3M @ $10 = $30
    ]
    stats = _by_model(analyze(attempts, PRICES, cache_loss_fraction=1.0))
    pro = stats["pro"]
    assert pro.switches_in == 2
    assert pro.cache_loss_cost == pytest.approx(10.00 + 30.00)
    assert pro.total_cost == pytest.approx(20.00 + 40.00)
    cheap = stats["cheap"]
    assert cheap.switches_in == 1
    assert cheap.cache_loss_cost == pytest.approx(2.00)
    assert cheap.total_cost == pytest.approx(2.00 + 2.00)


def test_same_model_retries_are_not_switches():
    attempts = [att("cheap", "t1", False), att("cheap", "t1", True)]
    (stats,) = analyze(attempts, PRICES, cache_loss_fraction=1.0)
    assert stats.cache_loss_cost == 0.0
    assert stats.switches_in == 0
    assert stats.total_cost == pytest.approx(2.00)


def test_no_switch_log_unaffected_by_fraction():
    attempts = [
        att("cheap", "t1", False), att("cheap", "t1", True),
        att("pro", "t2", True),
        att("cheap", "t3", True),
    ]
    on = analyze(attempts, PRICES, cache_loss_fraction=1.0)
    off = analyze(attempts, PRICES, cache_loss_fraction=0.0)
    assert [(s.model, s.total_cost) for s in on] == [
        (s.model, s.total_cost) for s in off
    ]
    assert all(s.cache_loss_cost == 0.0 and s.switches_in == 0 for s in on)


def test_context_is_tracked_per_task():
    attempts = [
        att("cheap", "t1", False, inp=1_000_000),   # t1 context: 1M
        att("cheap", "t2", False, inp=3_000_000),   # t2 context: 3M
        att("pro", "t1", True, inp=1_000_000),      # switch on t1 only
    ]
    stats = _by_model(analyze(attempts, PRICES, cache_loss_fraction=1.0))
    # Only t1's 1M accumulated tokens are re-billed, not t2's 3M.
    assert stats["pro"].cache_loss_cost == pytest.approx(10.00)
    assert stats["pro"].total_cost == pytest.approx(20.00)


def test_fraction_out_of_range_raises():
    with pytest.raises(ValueError, match="between 0 and 1"):
        analyze(_escalation(), PRICES, cache_loss_fraction=1.5)
    with pytest.raises(ValueError, match="between 0 and 1"):
        analyze(_escalation(), PRICES, cache_loss_fraction=-0.25)


def test_insight_reports_cache_loss_only_when_charged():
    stats = analyze(_escalation(), PRICES, cache_loss_fraction=1.0)
    insights = build_insights(stats, PRICES)
    assert any("Cache loss" in text and "pro" in text for text in insights)
    stats_off = analyze(_escalation(), PRICES)
    assert not any("Cache loss" in text for text in build_insights(stats_off, PRICES))


# --- CLI -----------------------------------------------------------------
# Bundled prices: gpt-5-mini input $0.25/M, gpt-5 input $1.25/M.

def _write_escalation_log(tmp_path):
    log = tmp_path / "router.jsonl"
    lines = [
        {"model": "gpt-5-mini", "task_id": "t1", "success": False,
         "input_tokens": 1_000_000, "output_tokens": 0},
        {"model": "gpt-5", "task_id": "t1", "success": True,
         "input_tokens": 1_000_000, "output_tokens": 0},
    ]
    log.write_text("".join(json.dumps(line) + "\n" for line in lines),
                   encoding="utf-8")
    return log


def test_cli_cache_loss_fraction_json(tmp_path):
    log = _write_escalation_log(tmp_path)
    dest = tmp_path / "report.json"
    rc = main(["analyze", str(log), "--cache-loss-fraction", "1",
               "--format", "json", "--output", str(dest)])
    assert rc == 0
    models = {m["model"]: m for m in json.loads(dest.read_text(encoding="utf-8"))["models"]}
    gpt5 = models["gpt-5"]
    # $1.25 attempt + 1M accumulated tokens re-billed @ $1.25/M = $1.25.
    assert gpt5["cache_loss_cost_usd"] == 1.25
    assert gpt5["switches_in"] == 1
    assert gpt5["total_cost_usd"] == 2.50
    assert models["gpt-5-mini"]["total_cost_usd"] == 0.25
    assert models["gpt-5-mini"]["cache_loss_cost_usd"] == 0.0


def test_cli_default_has_no_cache_loss(tmp_path, capsys):
    log = _write_escalation_log(tmp_path)
    assert main(["analyze", str(log)]) == 0
    out = capsys.readouterr().out
    assert "Cache loss" not in out
    assert "$1.2500" in out  # gpt-5 billed for its attempt only


def test_cli_cache_loss_markdown_insight(tmp_path, capsys):
    log = _write_escalation_log(tmp_path)
    assert main(["analyze", str(log), "--cache-loss-fraction", "1"]) == 0
    assert "Cache loss" in capsys.readouterr().out


def test_cli_fraction_out_of_range_is_a_clean_error(tmp_path, capsys):
    log = _write_escalation_log(tmp_path)
    assert main(["analyze", str(log), "--cache-loss-fraction", "2"]) == 2
    assert "error:" in capsys.readouterr().err
