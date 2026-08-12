"""Reader for a YAML roster file.

The hand-maintained alternative to syncing off a platform. Same output shape
either way -- a dict of team name to Roster -- so nothing downstream needs to
know which one it got.
"""

from __future__ import annotations

import pathlib

import yaml

from fpr.core.lineup import LineupError, Roster


class RosterFileError(ValueError):
    pass


def load(path: pathlib.Path | str) -> dict[str, Roster]:
    path = pathlib.Path(path)
    if not path.exists():
        raise RosterFileError(
            f"roster file not found: {path}. Copy config/rosters.example.yaml "
            f"to config/rosters.yaml and fill in your league."
        )

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict) or "teams" not in data:
        raise RosterFileError(f"{path} needs a top-level 'teams:' mapping")

    teams = data["teams"]
    if not isinstance(teams, dict) or not teams:
        raise RosterFileError(f"{path} has no teams under 'teams:'")

    rosters = {}
    for team, groups in teams.items():
        if not isinstance(groups, dict):
            raise RosterFileError(f"{path}: {team} should be a mapping of position groups")
        try:
            rosters[str(team)] = Roster.from_dict(groups)
        except LineupError as exc:
            raise RosterFileError(f"{path}: {team}: {exc}") from exc

    _check_for_duplicates(path, rosters)
    return rosters


def _check_for_duplicates(path: pathlib.Path, rosters: dict[str, Roster]) -> None:
    """One player on two rosters is a typo, and a quiet one -- both teams would
    just look slightly better than they are."""
    from fpr.core.names import normalize

    owner: dict[str, str] = {}
    clashes = []
    for team, roster in rosters.items():
        for name in roster.all_players():
            key = normalize(name)
            if key in owner and owner[key] != team:
                clashes.append(f"{name} is on both {owner[key]} and {team}")
            owner[key] = team

    if clashes:
        raise RosterFileError(f"{path}: " + "; ".join(clashes))
