"""Command line entry point.

Deliberately thin: parse arguments, call pipeline.build() once, render, write.
If this file ever grows into the biggest one in the project, something that
belongs in pipeline.py or core/ has been duplicated in here instead.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from fpr import config, pipeline
from fpr.adapters.raw_csv import SourceDataError
from fpr.adapters.rosters import RosterFileError
from fpr.core.consensus import ConsensusError
from fpr.core.lineup import LineupError
from fpr.report import markdown

# Anything raised deliberately by the pipeline. These get a clean one-line
# message; anything else keeps its traceback, because an unexpected exception
# is a bug and hiding the traceback makes it harder to fix.
EXPECTED = (
    config.ConfigError,
    SourceDataError,
    RosterFileError,
    ConsensusError,
    LineupError,
    KeyError,
)


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", default=config.DEFAULT_PATH, type=pathlib.Path, help="league config YAML"
    )
    parser.add_argument(
        "--rankings",
        default=pipeline.DEFAULT_RANKINGS,
        type=pathlib.Path,
        help="raw per-source rankings CSV",
    )
    parser.add_argument(
        "--rosters", default=pipeline.DEFAULT_ROSTERS, type=pathlib.Path, help="roster YAML"
    )
    parser.add_argument(
        "--optimal",
        action="store_true",
        help="recompute the best legal lineup instead of using the roster as set",
    )
    parser.add_argument("-o", "--out", type=pathlib.Path, help="write to a file instead of stdout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fpr", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    rank = sub.add_parser("rank", help="run the deterministic power rankings")
    _common_args(rank)

    return parser


def cmd_rank(args) -> str:
    league = pipeline.build(
        config_path=args.config,
        rankings_path=args.rankings,
        rosters_path=args.rosters,
        optimal=args.optimal,
    )
    return markdown.render(league)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    handlers = {"rank": cmd_rank}
    try:
        output = handlers[args.command](args)
    except EXPECTED as exc:
        message = exc.args[0] if exc.args else exc
        print(f"fpr: {message}", file=sys.stderr)
        return 1

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
