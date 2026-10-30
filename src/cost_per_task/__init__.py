"""cost-per-task: rank LLMs by cost per completed task, not price per token."""

from .analyze import ModelStats, analyze, build_insights
from .handoff import (
    HandoffPoint,
    HandoffReport,
    analyze_handoff,
    build_handoff_insights,
)
from .prices import Price, PriceTable, load_prices
from .records import Attempt, load_records

__version__ = "0.3.0"

__all__ = [
    "Attempt",
    "HandoffPoint",
    "HandoffReport",
    "ModelStats",
    "Price",
    "PriceTable",
    "analyze",
    "analyze_handoff",
    "build_handoff_insights",
    "build_insights",
    "load_prices",
    "load_records",
    "__version__",
]
