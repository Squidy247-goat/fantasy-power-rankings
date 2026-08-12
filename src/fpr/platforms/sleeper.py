"""Sleeper adapter.

No authentication at all -- a league ID is the whole story -- which makes this
the cheapest possible test of whether the shared interface from base.py
actually holds, or whether it just happened to describe ESPN. It's worth
building before Yahoo's OAuth for exactly that reason.

Sleeper models rosters differently from ESPN. There's no per-player lineup slot
attribute; there's a `starters` array whose order corresponds to the league's
`roster_positions` setting, and a `players` array containing everyone. So the
slot assignment gets reconstructed by zipping those two together rather than
read off each player.

`/players/nfl` is about five megabytes and changes rarely, so it's cached on
disk. Re-downloading it on every run of a daily job would be rude.
"""

from __future__ import annotations

import json
import pathlib
import time
from collections.abc import Callable

from fpr.core.lineup import Roster
from fpr.platforms.base import (
    Credentials,
    Platform,
    PlatformError,
    empty_groups,
    load_env,
    normalize_status,
    to_roster,
)

API = "https://api.sleeper.app/v1"

# Sleeper's roster_positions codes mapped onto our groups. Bench and the
# various not-really-playing slots are handled separately.
SLOT_GROUPS = {
    "QB": "qb",
    "RB": "rb",
    "WR": "wr",
    "TE": "te",
    "FLEX": "flex",
    "WRRB_FLEX": "flex",
    "REC_FLEX": "flex",
    "SUPER_FLEX": "flex",
    "IDP_FLEX": None,  # defensive players, never modeled
    "K": None,
    "DEF": None,
    "DL": None,
    "LB": None,
    "DB": None,
}

BENCH_SLOTS = {"BN", "IR", "TAXI"}

# Anything still rostered but not in the lineup ends up on the bench.
NEVER_MODELED = {"K", "DEF", "DL", "LB", "DB"}

ENV_KEYS = {"league_id": ("SLEEPER_LEAGUE_ID",)}

PLAYER_CACHE = pathlib.Path(".cache/sleeper_players.json")
CACHE_MAX_AGE = 60 * 60 * 24  # a day; the file changes rarely


def credentials_from_env(env_path: str = ".env") -> Credentials:
    load_env(env_path)
    return Credentials.from_env("SLEEPER", ENV_KEYS)


class SleeperPlatform(Platform):
    name = "sleeper"

    def __init__(self, fetch: Callable[[str], object] | None = None, cache=PLAYER_CACHE):
        self._fetch = fetch or _http_get
        self._cache = pathlib.Path(cache) if cache else None

    def sync_with_status(
        self, credentials: Credentials
    ) -> tuple[dict[str, Roster], dict[str, str]]:
        (league_id,) = credentials.require("league_id")

        league = self._fetch(f"{API}/league/{league_id}")
        rosters = self._fetch(f"{API}/league/{league_id}/rosters")
        users = self._fetch(f"{API}/league/{league_id}/users")
        players = self._players()

        return extract(league, rosters, users, players)

    def _players(self) -> dict:
        """The player dictionary, from cache when it's fresh enough."""
        if self._cache and self._cache.exists():
            age = time.time() - self._cache.stat().st_mtime
            if age < CACHE_MAX_AGE:
                with self._cache.open(encoding="utf-8") as fh:
                    return json.load(fh)

        players = self._fetch(f"{API}/players/nfl")
        if self._cache:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps(players), encoding="utf-8")
        return players


def starter_slots(league: dict) -> list[str]:
    """The lineup slots, in the order the `starters` array uses.

    Sleeper's `roster_positions` includes bench and taxi entries; those aren't
    represented in `starters`, so they're dropped before zipping.
    """
    positions = league.get("roster_positions") or []
    if not positions:
        raise PlatformError(
            "Sleeper league has no roster_positions. Can't tell which slot each "
            "starter occupies without it."
        )
    return [p for p in positions if p not in BENCH_SLOTS]


def team_names(users: list, rosters: list) -> dict:
    """Roster ID -> display name.

    Sleeper lets a manager set a team name, but plenty don't, so it falls back
    to the username and then to the roster ID.
    """
    by_user = {}
    for user in users or []:
        metadata = user.get("metadata") or {}
        by_user[user.get("user_id")] = (
            metadata.get("team_name") or user.get("display_name") or user.get("username")
        )

    names = {}
    for roster in rosters or []:
        roster_id = roster.get("roster_id")
        names[roster_id] = by_user.get(roster.get("owner_id")) or f"Roster {roster_id}"
    return names


def extract(league: dict, rosters: list, users: list, players: dict):
    """Turn Sleeper's four responses into the shared roster shape."""
    if not rosters:
        raise PlatformError("Sleeper returned no rosters. Check the league ID.")

    slots = starter_slots(league)
    names = team_names(users, rosters)

    out: dict[str, Roster] = {}
    statuses: dict[str, str] = {}

    for roster in rosters:
        team = names.get(roster.get("roster_id"), f"Roster {roster.get('roster_id')}")
        groups = empty_groups()

        starters = [p for p in (roster.get("starters") or []) if p and p != "0"]
        everyone = [p for p in (roster.get("players") or []) if p and p != "0"]

        started = set()
        for slot, player_id in zip(slots, starters, strict=False):
            info = players.get(player_id)
            if info is None:
                continue
            position = (info.get("position") or "").upper()
            if position in NEVER_MODELED:
                continue

            name = _display_name(info)
            if not name:
                continue

            group = SLOT_GROUPS.get(slot.upper())
            if group is None:
                continue
            groups[group].append(name)
            started.add(player_id)
            _record_status(statuses, name, info)

        for player_id in everyone:
            if player_id in started:
                continue
            info = players.get(player_id)
            if info is None:
                continue
            if (info.get("position") or "").upper() in NEVER_MODELED:
                continue
            name = _display_name(info)
            if not name:
                continue
            groups["bench"].append(name)
            _record_status(statuses, name, info)

        out[team] = to_roster(groups)

    return out, statuses


def _display_name(info: dict) -> str:
    full = (info.get("full_name") or "").strip()
    if full:
        return full
    first = (info.get("first_name") or "").strip()
    last = (info.get("last_name") or "").strip()
    return f"{first} {last}".strip()


def _record_status(statuses: dict, name: str, info: dict) -> None:
    status = normalize_status(info.get("injury_status"))
    if status != "ACTIVE":
        statuses[name] = status


def _http_get(url: str):  # pragma: no cover - network
    try:
        import requests
    except ImportError as exc:
        raise PlatformError(
            'the requests package isn\'t installed. Install the platform extra '
            'with: pip install -e ".[platforms]"'
        ) from exc

    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        raise PlatformError(f"Sleeper returned {response.status_code} for {url}")
    return response.json()
