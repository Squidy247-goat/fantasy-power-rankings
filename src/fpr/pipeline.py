"""The one place config, rankings and rosters get turned into lineups.

Every command calls build() exactly once and works off what it returns. The
rule this enforces is that no two commands independently re-derive the same
setup -- that duplication is the thing most worth avoiding in this codebase,
because the moment two paths build consensus separately they start disagreeing
about it, and the disagreement shows up as a ranking that changes depending on
which subcommand you ran.

Rosters can come from a YAML file or from a platform adapter. Both produce the
same shape, so everything downstream of here is identical either way.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

from fpr import config
from fpr.adapters import raw_csv
from fpr.adapters import rosters as roster_file
from fpr.config import LeagueConfig
from fpr.core import lineup as lineup_mod
from fpr.core.consensus import ConsensusTable
from fpr.core.consensus import build as build_consensus
from fpr.core.lineup import Lineup, Roster

DEFAULT_RANKINGS = pathlib.Path("raw_rankings.csv")
DEFAULT_ROSTERS = pathlib.Path("config/rosters.yaml")


@dataclass(frozen=True)
class League:
    """Everything downstream needs, derived once."""

    cfg: LeagueConfig
    table: ConsensusTable
    rosters: dict[str, Roster]
    lineups: dict[str, Lineup]
    injury_status: dict[str, str]
    optimal: bool

    @property
    def teams(self) -> list[str]:
        return list(self.lineups)


def build(
    *,
    config_path: pathlib.Path | str = config.DEFAULT_PATH,
    rankings_path: pathlib.Path | str = DEFAULT_RANKINGS,
    rosters_path: pathlib.Path | str = DEFAULT_ROSTERS,
    rosters: dict[str, Roster] | None = None,
    injury_status: dict[str, str] | None = None,
    optimal: bool = False,
) -> League:
    """Load everything and slot every roster.

    Pass `rosters` to use what a platform adapter returned instead of reading
    the YAML file; `rosters_path` is ignored in that case.
    """
    cfg = config.load(config_path)
    table = build_consensus(raw_csv.load(rankings_path), cfg)

    if rosters is None:
        rosters = roster_file.load(rosters_path)

    slot = lineup_mod.build_optimal if optimal else lineup_mod.build
    lineups = {team: slot(team, roster, table, cfg) for team, roster in rosters.items()}

    return League(
        cfg=cfg,
        table=table,
        rosters=rosters,
        lineups=lineups,
        injury_status=injury_status or {},
        optimal=optimal,
    )
