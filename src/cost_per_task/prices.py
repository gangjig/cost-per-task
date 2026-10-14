"""Model price table: bundled example data plus user overrides.

Prices are USD per 1M tokens. The bundled table (data/prices.json) is a
dated snapshot provided as example data -- providers change prices often,
so verify against the provider's pricing page before relying on it, or
supply your own file with --prices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


@dataclass(frozen=True)
class Price:
    """Per-1M-token prices for one model (USD)."""

    model: str
    input_per_m: float
    output_per_m: float
    cached_input_per_m: Optional[float] = None

    @property
    def per_token_price(self) -> float:
        """Crude per-token price proxy used only for the 'cheapest per token'
        comparison: input + output list price. Real workloads weight these
        differently -- which is exactly the point of this tool."""
        return self.input_per_m + self.output_per_m

    @classmethod
    def from_dict(cls, model: str, data: dict) -> "Price":
        return cls(
            model=model,
            input_per_m=float(data["input_per_m"]),
            output_per_m=float(data["output_per_m"]),
            cached_input_per_m=(
                float(data["cached_input_per_m"])
                if data.get("cached_input_per_m") is not None
                else None
            ),
        )


class PriceTable:
    """A set of model prices with an 'as of' date and a disclaimer."""

    def __init__(
        self,
        prices: dict[str, Price],
        as_of: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        self._prices = dict(prices)
        self.as_of = as_of
        self.note = note

    def __contains__(self, model: str) -> bool:
        return model in self._prices

    def __len__(self) -> int:
        return len(self._prices)

    def models(self) -> list[str]:
        return sorted(self._prices)

    def price_for(self, model: str) -> Price:
        try:
            return self._prices[model]
        except KeyError:
            known = ", ".join(self.models()) or "(none)"
            raise ValueError(
                f"no price known for model {model!r}; known models: {known}. "
                "Add it via a --prices file."
            ) from None

    def cheapest_per_token(self) -> Optional[Price]:
        if not self._prices:
            return None
        return min(self._prices.values(), key=lambda p: p.per_token_price)

    def merged_with(self, other: "PriceTable") -> "PriceTable":
        """Return a new table where `other` overrides/extends this one."""
        merged = dict(self._prices)
        merged.update(other._prices)
        return PriceTable(merged, as_of=other.as_of or self.as_of, note=self.note)


def _parse_price_data(data: dict) -> PriceTable:
    entries = data.get("models", data)
    prices = {
        model: Price.from_dict(model, entry) for model, entry in entries.items()
    }
    return PriceTable(prices, as_of=data.get("as_of"), note=data.get("note"))


def bundled_prices_text() -> str:
    return (
        resources.files("cost_per_task")
        .joinpath("data/prices.json")
        .read_text(encoding="utf-8")
    )


def load_prices(path: Optional[PathLike] = None) -> PriceTable:
    """Load the bundled price table, optionally overridden/extended by a
    user JSON file of the same shape. User entries win on name collisions."""
    table = _parse_price_data(json.loads(bundled_prices_text()))
    if path is not None:
        user_data = json.loads(Path(path).read_text(encoding="utf-8"))
        table = table.merged_with(_parse_price_data(user_data))
    return table
