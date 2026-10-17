"""cost-per-task: rank LLMs by cost per completed task, not price per token."""

from .analyze import ModelStats, analyze, build_insights
from .prices import Price, PriceTable, load_prices
from .records import Attempt, load_records

__version__ = "0.2.0"

__all__ = [
    "Attempt",
    "ModelStats",
    "Price",
    "PriceTable",
    "analyze",
    "build_insights",
    "load_prices",
    "load_records",
    "__version__",
]
