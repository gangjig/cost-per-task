import pytest

from cost_per_task.analyze import analyze, build_insights
from cost_per_task.prices import Price, PriceTable
from cost_per_task.records import Attempt

PRICES = PriceTable({
    "cheap": Price("cheap", input_per_m=1.0, output_per_m=2.0, cached_input_per_m=0.5),
    "pro": Price("pro", input_per_m=10.0, output_per_m=20.0),
})


def att(model, task, success, inp=1_000_000, out=0, cached=0, latency=1.0):
    return Attempt(model=model, task_id=task, success=success,
                   input_tokens=inp, output_tokens=out,
                   cached_input_tokens=cached, latency_s=latency)


def test_retry_aggregation_semantics():
    """All attempts cost money; a task succeeds if ANY attempt succeeded."""
    attempts = [
        att("cheap", "t1", False),          # $1.00
        att("cheap", "t1", True),           # $1.00 -> t1 succeeded on retry
        att("cheap", "t2", True),           # $1.00
        att("cheap", "t3", False),          # $1.00
        att("cheap", "t3", False),          # $1.00 -> t3 never succeeded
    ]
    (stats,) = analyze(attempts, PRICES)
    assert stats.tasks_attempted == 3
    assert stats.tasks_succeeded == 2
    assert stats.attempts == 5
    assert stats.success_rate == pytest.approx(2 / 3)
    assert stats.total_cost == pytest.approx(5.00)          # failures billed too
    assert stats.cost_per_successful_task == pytest.approx(2.50)
    assert stats.avg_attempts_per_task == pytest.approx(5 / 3)


def test_cost_per_success_hand_computed_with_cache_and_output():
    attempts = [
        # 800k @1 + 200k cached @0.5 + 500k out @2 = 0.80+0.10+1.00 = 1.90 (fail)
        att("cheap", "t1", False, inp=1_000_000, out=500_000, cached=200_000),
        # same again, success -> total 3.80 for one successful task
        att("cheap", "t1", True, inp=1_000_000, out=500_000, cached=200_000),
    ]
    (stats,) = analyze(attempts, PRICES)
    assert stats.total_cost == pytest.approx(3.80)
    assert stats.cost_per_successful_task == pytest.approx(3.80)


def test_median_latency_and_tokens_per_task():
    attempts = [
        att("pro", "t1", True, inp=100, out=50, latency=1.0),
        att("pro", "t2", True, inp=300, out=50, latency=3.0),
        att("pro", "t3", True, inp=200, out=100, latency=2.0),
    ]
    (stats,) = analyze(attempts, PRICES)
    assert stats.median_latency_s == 2.0
    assert stats.avg_tokens_per_task == pytest.approx((150 + 350 + 300) / 3)


def test_ranking_by_cost_per_success_not_total_cost():
    attempts = [
        # pro: one expensive success ($10) -> cost/success 10.00
        att("pro", "t1", True),
        # cheap: one failure then one success ($1 + $1) -> cost/success 2.00
        att("cheap", "t1", False),
        att("cheap", "t1", True),
    ]
    stats = analyze(attempts, PRICES)
    assert [s.model for s in stats] == ["cheap", "pro"]


def test_zero_success_model_sorts_last_with_none():
    attempts = [
        att("cheap", "t1", True),                       # cost/success = 1.00
        att("pro", "t1", False), att("pro", "t2", False),  # never succeeds
    ]
    stats = analyze(attempts, PRICES)
    assert stats[0].model == "cheap"
    assert stats[-1].model == "pro"
    assert stats[-1].cost_per_successful_task is None
    assert stats[-1].success_rate == 0.0


def test_unknown_model_in_log_raises():
    with pytest.raises(ValueError, match="no price known"):
        analyze([att("mystery", "t1", True)], PRICES)


def test_insight_flags_zero_success_model():
    attempts = [att("pro", "t1", False), att("cheap", "t1", True)]
    stats = analyze(attempts, PRICES)
    insights = build_insights(stats, PRICES)
    assert any("pro" in text and "0 of 1" in text for text in insights)


def test_empty_input():
    assert analyze([], PRICES) == []
    assert build_insights([], PRICES) == []
