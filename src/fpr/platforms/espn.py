"""ESPN adapter, built on the espn-api package.

The network call and the parsing are kept apart on purpose. `extract()` takes
anything shaped like an espn-api League object and returns the shared roster
shape, which means the parsing -- where all the actual bugs live -- gets tested
against fixtures instead of against ESPN's servers and someone's cookies.

Two things about ESPN specifically.

Slots are strings and there are more of them than you'd expect. Anything
flex-shaped (`RB/WR`, `WR/TE`, `RB/WR/TE`, `FLEX`, `OP`) collapses to one flex
group. Kickers, defenses and IR slots are dropped entirely rather than carried
around and filtered later.

A player in the IR slot is on injured reserve regardless of what
`injuryStatus` claims, and the two do disagree. The slot is the more reliable
signal because it reflects a roster move somebody actually made, so it wins.
"""

from __future__ import annotations

from fpr.core.availability import INJURY_RESERVE
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

# ESPN's lineupSlot strings mapped onto our position groups.
SLOT_GROUPS = {
    "QB": "qb",
    "RB": "rb",
    "WR": "wr",
    "TE": "te",
    "RB/WR": "flex",
    "WR/TE": "flex",
    "RB/WR/TE": "flex",
    "FLEX": "flex",
    "OP": "flex",
    "BE": "bench",
    "Bench": "bench",
}

# Never modeled. K and D/ST don't appear in any ranking source we use, and IR
# is a roster state rather than a lineup position.
SKIP_SLOTS = {"IR", "K", "D/ST", "DST", "D", "P", "HC"}

IR_SLOT = "IR"

ENV_KEYS = {
    # Canonical name -> other env vars that might hold it. The unprefixed names
    # are what the original .env used before platforms got prefixes.
    "league_id": ("ESPN_LEAGUE_ID", "LEAGUE_ID"),
    "year": ("ESPN_YEAR", "YEAR", "SEASON"),
    "espn_s2": ("ESPN_S2", "ESPN_SWID_S2"),
    "swid": ("ESPN_SWID", "SWID"),
}


class ESPNPlatform(Platform):
    name = "espn"

    def sync_with_status(
        self, credentials: Credentials
    ) -> tuple[dict[str, Roster], dict[str, str]]:
        return extract(self._connect(credentials))

    def _connect(self, credentials: Credentials):
        try:
            from espn_api.football import League
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise PlatformError(
                "the espn-api package isn't installed. Install the platform "
                'extra with: pip install -e ".[platforms]"'
            ) from exc

        (league_id,) = credentials.require("league_id")
        year = credentials.get("year")

        kwargs = {"league_id": int(league_id)}
        kwargs["year"] = int(year) if year else _current_season()

        # Cookies are only needed for private leagues, so they're optional here
        # rather than required -- a public league shouldn't need them.
        s2, swid = credentials.get("espn_s2"), credentials.get("swid")
        if s2 and swid:
            kwargs["espn_s2"] = s2
            kwargs["swid"] = swid

        try:
            return League(**kwargs)
        except Exception as exc:  # espn-api raises a variety of things
            raise PlatformError(
                f"could not open ESPN league {league_id}: {exc}. "
                f"Private leagues need ESPN_S2 and SWID cookies."
            ) from exc


def credentials_from_env(env_path: str = ".env") -> Credentials:
    """Read ESPN credentials.

    load_env() runs first. It has to -- reading the environment before the .env
    file is loaded gives you None for everything and an authentication failure
    that looks nothing like the configuration problem it actually is.
    """
    load_env(env_path)
    return Credentials.from_env("ESPN", ENV_KEYS)


def extract(league) -> tuple[dict[str, Roster], dict[str, str]]:
    """Pull rosters and injury designations out of an espn-api League.

    Takes anything with `.teams`, so tests can hand it a fixture.
    """
    teams = getattr(league, "teams", None)
    if not teams:
        raise PlatformError("ESPN returned no teams. Check the league ID and season.")

    rosters: dict[str, Roster] = {}
    statuses: dict[str, str] = {}

    for team in teams:
        name = team_name(team)
        groups = empty_groups()

        for player in getattr(team, "roster", []) or []:
            slot = str(getattr(player, "lineupSlot", "") or "").strip()
            if slot in SKIP_SLOTS and slot != IR_SLOT:
                continue

            position = str(getattr(player, "position", "") or "").strip().upper()
            if position in ("K", "D/ST", "DST"):
                continue

            player_name = str(getattr(player, "name", "") or "").strip()
            if not player_name:
                continue

            status = injury_status(player)
            if status != "ACTIVE":
                statuses[player_name] = status

            # An IR player is still rostered, but he isn't in the lineup. He
            # goes to the bench so his availability (near zero) is modeled
            # rather than his absence being invisible.
            if slot == IR_SLOT:
                groups["bench"].append(player_name)
                continue

            group = SLOT_GROUPS.get(slot)
            if group is None:
                raise PlatformError(
                    f"{name}: unrecognized ESPN lineup slot {slot!r} for {player_name}. "
                    f"Add it to SLOT_GROUPS or SKIP_SLOTS in platforms/espn.py."
                )
            groups[group].append(player_name)

        rosters[name] = to_roster(groups)

    return rosters, statuses


def team_name(team) -> str:
    """Team display name, however this version of espn-api spells it."""
    for attribute in ("team_name", "name", "teamName"):
        value = getattr(team, attribute, None)
        if value:
            return str(value).strip()
    return f"Team {getattr(team, 'team_id', '?')}"


def injury_status(player) -> str:
    """Shared-vocabulary status for one player.

    The IR slot overrides whatever injuryStatus says. They disagree in
    practice, and the slot reflects a roster move somebody actually made.
    """
    if str(getattr(player, "lineupSlot", "") or "").strip() == IR_SLOT:
        return INJURY_RESERVE
    if getattr(player, "injuryStatus", None) is None and getattr(player, "injured", False):
        return INJURY_RESERVE
    return normalize_status(getattr(player, "injuryStatus", None))


def projected_points(player) -> float | None:
    """ESPN's own projection, if it's there under any of its several names.

    Nothing in this project ranks on ESPN's projections -- consensus ranks come
    from the ranking sources -- so this exists only for diagnostics. Kept
    because the attribute name is a known trap: it's `projected_avg_points`,
    not `projected_points`, and the wrong one silently returns None on some
    versions rather than raising. Probing beats trusting either the docs or a
    memory of them.
    """
    for attribute in ("projected_avg_points", "projected_total_points", "projected_points"):
        if hasattr(player, attribute):
            value = getattr(player, attribute)
            if value is not None:
                return float(value)
    return None


def _current_season() -> int:
    """Fantasy seasons are named for the year they start in.

    January through July belongs to the previous season, since that's when the
    playoffs and the offseason live.
    """
    from datetime import date

    today = date.today()
    return today.year if today.month >= 8 else today.year - 1
