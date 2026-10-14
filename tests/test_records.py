import pytest

from cost_per_task.prices import Price
from cost_per_task.records import Attempt, attempt_cost, load_records

PRICE_WITH_CACHE = Price("m", input_per_m=1.0, output_per_m=2.0, cached_input_per_m=0.5)
PRICE_NO_CACHE = Price("m", input_per_m=10.0, output_per_m=20.0, cached_input_per_m=None)


def test_cost_math_with_explicit_cached_price():
    # 800k uncached @ $1 + 200k cached @ $0.50 + 500k output @ $2 = 0.80+0.10+1.00
    attempt = Attempt(model="m", task_id="t", success=True,
                      input_tokens=1_000_000, output_tokens=500_000,
                      cached_input_tokens=200_000)
    assert attempt_cost(attempt, PRICE_WITH_CACHE) == pytest.approx(1.90)


def test_cost_math_cached_fraction_fallback():
    # No cached price in table -> cached billed at fraction (0.5) of input:
    # 60k @ $10 + 40k @ $5 + 10k output @ $20 = 0.60 + 0.20 + 0.20 = 1.00
    attempt = Attempt(model="m", task_id="t", success=True,
                      input_tokens=100_000, output_tokens=10_000,
                      cached_input_tokens=40_000)
    assert attempt_cost(attempt, PRICE_NO_CACHE, cached_fraction=0.5) == pytest.approx(1.00)


def test_cost_math_no_cache_tokens():
    attempt = Attempt(model="m", task_id="t", success=False,
                      input_tokens=2_000_000, output_tokens=0)
    assert attempt_cost(attempt, PRICE_WITH_CACHE) == pytest.approx(2.00)


def test_cached_fraction_only_applies_to_cached_share():
    attempt = Attempt(model="m", task_id="t", success=True,
                      input_tokens=100_000, output_tokens=0,
                      cached_input_tokens=100_000)
    free = attempt_cost(attempt, PRICE_NO_CACHE, cached_fraction=0.0)
    full = attempt_cost(attempt, PRICE_NO_CACHE, cached_fraction=1.0)
    assert free == 0.0
    assert full == pytest.approx(1.00)  # 100k @ $10/M


def test_from_dict_defaults():
    attempt = Attempt.from_dict({
        "model": "m", "task_id": "t1", "success": True,
        "input_tokens": 10, "output_tokens": 5,
    })
    assert attempt.cached_input_tokens == 0
    assert attempt.latency_s == 0.0
    assert attempt.attempts is None
    assert attempt.total_tokens == 15


def test_from_dict_rejects_cached_exceeding_input():
    with pytest.raises(ValueError, match="exceeds"):
        Attempt.from_dict({
            "model": "m", "task_id": "t1", "success": True,
            "input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 11,
        })


def test_from_dict_rejects_missing_field():
    with pytest.raises(ValueError, match="missing"):
        Attempt.from_dict({"model": "m", "task_id": "t1", "success": True,
                           "input_tokens": 10})


def test_load_records_skips_blank_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"model": "m", "task_id": "a", "success": true, "input_tokens": 1, "output_tokens": 2}\n'
        "\n"
        '{"model": "m", "task_id": "b", "success": false, "input_tokens": 3, "output_tokens": 4}\n',
        encoding="utf-8",
    )
    records = load_records(path)
    assert [r.task_id for r in records] == ["a", "b"]


def test_load_records_reports_line_number_on_bad_json(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"model": "m", "task_id": "a", "success": true, "input_tokens": 1, "output_tokens": 2}\n'
        "not json\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=":2:"):
        load_records(path)
