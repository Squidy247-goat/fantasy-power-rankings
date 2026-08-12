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
from fpr.adapters import fantasypros, raw_csv, sources
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
    platform: str | None = None
    # Non-fatal problems worth putting in front of a reader: a stale ranking
    # column, a position two sources disagree about. Not exceptions, because
    # none of them should stop a daily run, but not silent either.
    warnings: tuple[str, ...] = ()

    @property
    def teams(self) -> list[str]:
        return list(self.lineups)

    @property
    def source_description(self) -> str:
        return f"synced from {self.platform}" if self.platform else "from the roster file"


def build(
    *,
    config_path: pathlib.Path | str = config.DEFAULT_PATH,
    rankings_path: pathlib.Path | str = DEFAULT_RANKINGS,
    rosters_path: pathlib.Path | str = DEFAULT_ROSTERS,
    rosters: dict[str, Roster] | None = None,
    injury_status: dict[str, str] | None = None,
    platform: str | None = None,
    env_path: str = ".env",
    refresh_rankings: bool = False,
    season: int | None = None,
    optimal: bool = False,
) -> League:
    """Load everything and slot every roster.

    Rosters come from one of three places, in precedence order: passed in
    directly, synced from a platform, or read from the YAML file.
    """
    cfg = config.load(config_path)
    warnings: list[str] = []

    players = raw_csv.load(rankings_path)
    if refresh_rankings:
        fetched, fetch_warnings = _refresh(env_path, season)
        warnings.extend(fetch_warnings)
        # The committed file goes first, so it owns the display spelling and
        # position and the API just fills in a fresher ECR column.
        merged = sources.combine(players, fetched)
        players = merged.players
        warnings.extend(merged.warnings)

    table = build_consensus(players, cfg)

    if rosters is None and platform:
        rosters, synced_status = _sync(platform, env_path)
        # An explicitly supplied status wins, so a caller can override what the
        # platform reported.
        injury_status = injury_status or synced_status

    if rosters is None:
        rosters = roster_file.load(rosters_path)

    _check_everyone_is_ranked(rosters, table)

    slot = lineup_mod.build_optimal if optimal else lineup_mod.build
    lineups = {team: slot(team, roster, table, cfg) for team, roster in rosters.items()}

    return League(
        cfg=cfg,
        table=table,
        rosters=rosters,
        lineups=lineups,
        injury_status=injury_status or {},
        optimal=optimal,
        platform=platform,
        warnings=tuple(warnings),
    )


def _refresh(env_path: str, season: int | None) -> tuple[list, list[str]]:
    """Pull a fresh FantasyPros ECR column.

    Needs FANTASYPROS_API_KEY. Section 4.2 settled on the API rather than a
    scraper for this source; see docs/source-investigation.md.
    """
    import os

    from fpr.platforms.base import load_env

    load_env(env_path)
    api_key = os.getenv("FANTASYPROS_API_KEY")
    if not api_key:
        raise fantasypros.FantasyProsError(
            "FANTASYPROS_API_KEY is not set. Request a key at "
            "secure.fantasypros.com/api-keys/request/ and add it to .env, or "
            "drop --refresh-rankings to use the committed CSV."
        )

    if season is None:
        from fpr.platforms.espn import _current_season

        season = _current_season()

    return fantasypros.load(api_key, season)


class MissingPlayers(KeyError):
    """Rostered players with no row in the rankings input."""


def _check_everyone_is_ranked(rosters: dict[str, Roster], table: ConsensusTable) -> None:
    """Fail once with the whole list rather than once per player.

    Rosters drift from a rankings snapshot constantly -- waiver claims, and
    especially backup quarterbacks, who most published lists don't bother
    ranking. Hitting these one at a time means editing the CSV, re-running,
    and hitting the next one. Collecting them makes it a single edit.

    Note this is a different situation from section 1.1's unranked player. That
    one has a row in the input with no source ranks and correctly gets
    replacement level. This one has no row at all, so there's nothing to say
    what position he plays or whether he belongs in the league.
    """
    missing: dict[str, list[str]] = {}
    for team, roster in rosters.items():
        absent = [name for name in roster.all_players() if name not in table]
        if absent:
            missing[team] = absent

    if not missing:
        return

    total = sum(len(names) for names in missing.values())
    detail = "; ".join(f"{team}: {', '.join(names)}" for team, names in missing.items())
    raise MissingPlayers(
        f"{total} rostered player(s) have no row in the rankings input -- {detail}. "
        f"Add a row for each (name, position, and whatever ranks the sources give, "
        f"blank where a source doesn't list him). Backup quarterbacks are the usual "
        f"culprit; they're excluded from bench eligibility anyway, but they still "
        f"need a row so nothing gets silently dropped."
    )


def _sync(platform: str, env_path: str) -> tuple[dict[str, Roster], dict[str, str]]:
    """Fetch rosters from a platform.

    Deliberately not wrapped in a fallback. A sync failure has to stop the run,
    because the alternative -- quietly carrying on with stale or partial
    rosters -- produces a report that looks entirely normal and is wrong.
    """
    from fpr import platforms

    adapter = platforms.get(platform)
    credentials = platforms.credentials_for(platform, env_path)
    return adapter.sync_with_status(credentials)
