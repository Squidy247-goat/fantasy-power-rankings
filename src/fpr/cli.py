"""Command line entry point.

Deliberately thin: parse arguments, call pipeline.build() once, render, write.
If this file ever grows into the biggest one in the project, something that
belongs in pipeline.py or core/ has been duplicated in here instead.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from fpr import config, pipeline, platforms
from fpr.adapters.raw_csv import SourceDataError
from fpr.adapters.rosters import RosterFileError
from fpr.core import simulate
from fpr.core.consensus import ConsensusError
from fpr.core.lineup import LineupError
from fpr.core.simulate import SimulationError
from fpr.report import json as json_report
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
    SimulationError,
    platforms.PlatformError,
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
    parser.add_argument(
        "--platform",
        choices=platforms.available(),
        help="sync rosters live instead of reading the roster file",
    )
    parser.add_argument("--env", default=".env", help="path to the credentials file")
    parser.add_argument("-o", "--out", type=pathlib.Path, help="write to a file instead of stdout")
    parser.add_argument(
        "--snapshot",
        type=pathlib.Path,
        help="also write a dated JSON snapshot here, for later calibration",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fpr", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    rank = sub.add_parser("rank", help="run the deterministic power rankings")
    _common_args(rank)
    rank.add_argument(
        "--simulate",
        action="store_true",
        help="also run the Monte Carlo and report finish probabilities",
    )
    _simulation_args(rank)

    sim = sub.add_parser("simulate", help="rankings plus the Monte Carlo, always")
    _common_args(sim)
    _simulation_args(sim)

    sync = sub.add_parser(
        "sync", help="fetch rosters from a platform and write them to a roster file"
    )
    sync.add_argument("--platform", choices=platforms.available(), required=True)
    sync.add_argument("--env", default=".env", help="path to the credentials file")
    sync.add_argument(
        "-o",
        "--out",
        type=pathlib.Path,
        default=pipeline.DEFAULT_ROSTERS,
        help="where to write the roster file",
    )

    return parser


def _simulation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--trials", type=int, help="override the configured trial count")
    parser.add_argument("--weeks", type=int, help="override the configured season length")
    parser.add_argument("--seed", type=int, help="seed the RNG for a reproducible run")
    parser.add_argument(
        "--flat-availability",
        action="store_true",
        help="ignore position and injury status, one rate for everybody "
        "(a baseline for checking the per-player model does something)",
    )


def _build(args):
    return pipeline.build(
        config_path=args.config,
        rankings_path=args.rankings,
        rosters_path=args.rosters,
        platform=args.platform,
        env_path=args.env,
        optimal=args.optimal,
    )


def cmd_sync(args) -> str:
    """Fetch rosters and write them out, without ranking anything.

    Useful on its own for snapshotting a league, and it's the thing to run
    first when a platform sync is misbehaving -- it fails on the sync rather
    than somewhere inside the consensus build.
    """
    adapter = platforms.get(args.platform)
    credentials = platforms.credentials_for(args.platform, args.env)
    rosters, statuses = adapter.sync_with_status(credentials)

    if not rosters:
        raise platforms.PlatformError(f"{args.platform} returned no rosters")

    print(
        f"synced {len(rosters)} teams from {args.platform}, "
        f"{sum(len(r.all_players()) for r in rosters.values())} players, "
        f"{len(statuses)} with an injury designation",
        file=sys.stderr,
    )
    return rosters_yaml(rosters, args.platform, statuses)


def rosters_yaml(rosters, platform: str, statuses: dict[str, str]) -> str:
    lines = [
        f"# Synced from {platform}. Regenerate with: fpr sync --platform {platform}",
        "#",
        "# Slot keys are position groups. Which back is RB1 gets decided by value",
        "# at runtime, so the order here doesn't matter.",
        "",
        "teams:",
    ]
    for team, roster in rosters.items():
        lines.append(f"  {_quote(team)}:")
        for group in ("qb", "rb", "wr", "te", "flex", "bench"):
            names = ", ".join(getattr(roster, group))
            lines.append(f"    {group}: [{names}]")
        lines.append("")

    if statuses:
        lines.append("# Injury designations at sync time, for reference. These are read")
        lines.append("# live from the platform on each run, not from this file.")
        for name, status in sorted(statuses.items()):
            lines.append(f"#   {name}: {status}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _quote(name: str) -> str:
    """YAML-safe team name. Managers put anything in these."""
    if any(ch in name for ch in ":#{}[],&*?|<>=!%@`\"'") or name.strip() != name:
        escaped = name.replace('"', '\\"')
        return f'"{escaped}"'
    return name


def cmd_rank(args) -> str:
    league = _build(args)
    result = _simulate(league, args) if args.simulate else None
    # Render before writing the snapshot, so a failure in the report doesn't
    # leave a snapshot on disk with no report to go with it.
    report = markdown.render(league, result)
    _write_snapshot(args, league, result)
    return report


def cmd_simulate(args) -> str:
    league = _build(args)
    result = _simulate(league, args)
    report = markdown.render(league, result)
    _write_snapshot(args, league, result)
    return report


def _write_snapshot(args, league, result) -> None:
    path = getattr(args, "snapshot", None)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_report.render(league, result), encoding="utf-8")
    print(f"wrote {path}", file=sys.stderr)


def _simulate(league, args):
    return simulate.run(
        league,
        trials=args.trials,
        weeks=args.weeks,
        seed=args.seed,
        flat_availability=args.flat_availability,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    handlers = {"rank": cmd_rank, "simulate": cmd_simulate, "sync": cmd_sync}
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
