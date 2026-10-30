"""Command line interface: cost-per-task {analyze, handoff, prices, demo}."""

from __future__ import annotations

import argparse
import sys
from importlib import resources
from pathlib import Path
from typing import Optional, Sequence

from .analyze import (
    DEFAULT_CACHE_LOSS_FRACTION,
    DEFAULT_CACHED_FRACTION,
    analyze,
    build_insights,
)
from .handoff import (
    DEFAULT_TOLERANCE,
    analyze_handoff,
    build_handoff_insights,
)
from .prices import PriceTable, load_prices
from .records import load_records
from .report import (
    render_handoff_json,
    render_handoff_markdown,
    render_json,
    render_markdown,
)


def _sample_log_path() -> Path:
    return Path(
        str(
            resources.files("cost_per_task").joinpath("data/sample_runs.jsonl")
        )
    )


def _sample_handoff_log_path() -> Path:
    return Path(
        str(
            resources.files("cost_per_task").joinpath(
                "data/sample_handoff.jsonl"
            )
        )
    )


def _render_handoff(
    log_path: Path,
    prices: PriceTable,
    from_model: str,
    to_model: str,
    cached_fraction: float,
    fmt: str,
    cache_loss_fraction: float,
    tolerance: float,
) -> str:
    attempts = load_records(log_path)
    report = analyze_handoff(
        attempts,
        prices,
        from_model,
        to_model,
        cached_fraction=cached_fraction,
        cache_loss_fraction=cache_loss_fraction,
        tolerance=tolerance,
    )
    insights = build_handoff_insights(report)
    if fmt == "json":
        return render_handoff_json(report, insights)
    return render_handoff_markdown(report, insights)


def _render(
    log_path: Path,
    prices: PriceTable,
    cached_fraction: float,
    fmt: str,
    cache_loss_fraction: float = DEFAULT_CACHE_LOSS_FRACTION,
) -> str:
    attempts = load_records(log_path)
    stats = analyze(
        attempts,
        prices,
        cached_fraction=cached_fraction,
        cache_loss_fraction=cache_loss_fraction,
    )
    insights = build_insights(stats, prices)
    if fmt == "json":
        return render_json(stats, insights)
    return render_markdown(stats, insights)


def _print_or_write(text: str, output: Optional[str]) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--prices",
        metavar="FILE",
        help="JSON price file overriding/extending the bundled table",
    )
    parser.add_argument(
        "--cached-fraction",
        type=float,
        default=DEFAULT_CACHED_FRACTION,
        help="fraction of the input price charged for cached input tokens "
        "when a model has no explicit cached price (default: %(default)s)",
    )
    parser.add_argument(
        "--cache-loss-fraction",
        type=float,
        default=DEFAULT_CACHE_LOSS_FRACTION,
        help="for router/escalation logs: fraction of a task's accumulated "
        "context re-billed at the destination model's full input price each "
        "time the task switches models mid-session, 0-1 (default: "
        "%(default)s = off)",
    )
    parser.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        help="output format (default: md)",
    )
    parser.add_argument("--output", metavar="FILE", help="write to FILE instead of stdout")


def _add_handoff_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from-model",
        required=True,
        help="the strong model each run starts on",
    )
    parser.add_argument(
        "--to-model",
        required=True,
        help="the cheap model each run hands off to",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help="a switch point is eligible for the recommendation when its "
        "success rate is within this fraction of the best observed rate "
        "(default: %(default)s)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cost-per-task",
        description="Rank LLMs by cost per completed task, not price per token.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyze a JSONL run log")
    p_analyze.add_argument("log", help="path to the run log (JSONL, one attempt per line)")
    _add_common(p_analyze)

    p_handoff = sub.add_parser(
        "handoff",
        help="handoff curve: aggregate a handoff-experiment log by "
        "switch point (records must carry run_id)",
    )
    p_handoff.add_argument("log", help="path to the run log (JSONL, one attempt per line)")
    _add_handoff_args(p_handoff)
    _add_common(p_handoff)

    p_demo = sub.add_parser("demo", help="analyze the bundled sample run log")
    _add_common(p_demo)

    p_demo_handoff = sub.add_parser(
        "demo-handoff", help="run the handoff curve on the bundled sample log"
    )
    _add_handoff_args(p_demo_handoff)
    _add_common(p_demo_handoff)

    p_prices = sub.add_parser("prices", help="show the price table in effect")
    p_prices.add_argument(
        "--prices",
        metavar="FILE",
        help="JSON price file overriding/extending the bundled table",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        prices = load_prices(args.prices)
        if args.command == "prices":
            lines = [
                f"Price table (USD per 1M tokens"
                + (f", as of {prices.as_of}" if prices.as_of else "")
                + "):",
                "",
                "| Model | Input | Output | Cached input |",
                "|---|---:|---:|---:|",
            ]
            for model in prices.models():
                p = prices.price_for(model)
                cached = (
                    f"{p.cached_input_per_m:g}" if p.cached_input_per_m is not None else "—"
                )
                lines.append(
                    f"| {model} | {p.input_per_m:g} | {p.output_per_m:g} | {cached} |"
                )
            if prices.note:
                lines += ["", f"_{prices.note}_"]
            sys.stdout.write("\n".join(lines) + "\n")
            return 0

        if args.command in ("handoff", "demo-handoff"):
            log_path = (
                Path(args.log)
                if args.command == "handoff"
                else _sample_handoff_log_path()
            )
            text = _render_handoff(
                log_path,
                prices,
                args.from_model,
                args.to_model,
                args.cached_fraction,
                args.format,
                cache_loss_fraction=args.cache_loss_fraction,
                tolerance=args.tolerance,
            )
            _print_or_write(text, args.output)
            return 0

        log_path = Path(args.log) if args.command == "analyze" else _sample_log_path()
        text = _render(
            log_path,
            prices,
            args.cached_fraction,
            args.format,
            cache_loss_fraction=args.cache_loss_fraction,
        )
        _print_or_write(text, args.output)
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
