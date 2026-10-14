import json

import pytest

from cost_per_task.prices import Price, PriceTable, load_prices


def test_bundled_table_loads_with_metadata():
    table = load_prices()
    assert len(table) >= 5
    assert table.as_of  # snapshot date is recorded
    gpt5mini = table.price_for("gpt-5-mini")
    assert gpt5mini.input_per_m == 0.25
    assert gpt5mini.output_per_m == 2.00
    assert gpt5mini.cached_input_per_m == 0.025


def test_unknown_model_raises_with_guidance():
    table = load_prices()
    with pytest.raises(ValueError, match="no price known"):
        table.price_for("no-such-model")


def test_cheapest_per_token_is_the_mini():
    table = load_prices()
    assert table.cheapest_per_token().model == "gpt-5-mini"


def _write(tmp_path, data):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_user_file_overrides_and_extends(tmp_path):
    override = _write(tmp_path, {
        "models": {
            "gpt-5-mini": {"input_per_m": 9.99, "output_per_m": 9.99},
            "my-local-model": {"input_per_m": 0.0, "output_per_m": 0.0},
        }
    })
    table = load_prices(override)
    # overridden
    assert table.price_for("gpt-5-mini").input_per_m == 9.99
    # extended
    assert table.price_for("my-local-model").output_per_m == 0.0
    # untouched bundled entries survive
    assert table.price_for("gpt-5").input_per_m == 1.25


def test_price_per_token_proxy():
    price = Price(model="m", input_per_m=1.0, output_per_m=2.0)
    assert price.per_token_price == 3.0


def test_merged_table_keeps_original_immutable():
    base = PriceTable({"a": Price("a", 1.0, 1.0)})
    extra = PriceTable({"a": Price("a", 5.0, 5.0), "b": Price("b", 1.0, 1.0)})
    merged = base.merged_with(extra)
    assert merged.price_for("a").input_per_m == 5.0
    assert base.price_for("a").input_per_m == 1.0
    assert "b" in merged and "b" not in base
