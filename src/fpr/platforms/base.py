"""The interface every platform adapter implements.

This gets defined before any platform-specific code, and that ordering is the
point. ESPN, Sleeper and Yahoo disagree about nearly everything -- how a lineup
slot is identified, whether there's an auth flow, what an injury designation is
called, even whether "the roster" is a list of players or a list of slots. If
the first adapter gets written before the shared shape exists, the shared shape
ends up being whatever ESPN happened to return, and the other two get written
as translations of ESPN instead of as peers.

So: every adapter returns a dict of team name to Roster, and every adapter maps
its platform's injury vocabulary into the one in core.availability before
anything downstream sees it. Nothing past this module knows or cares which
platform the data came from.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from fpr.core.availability import (
    ACTIVE,
    DOUBTFUL,
    INJURY_RESERVE,
    OUT,
    PROBABLE,
    QUESTIONABLE,
    STATUSES,
)
from fpr.core.lineup import Roster

# Position groups a roster can have. Adapters map their platform's slot codes
# into these; anything that isn't one of them (kickers, defenses, IR slots) is
# dropped before it reaches us.
GROUPS = ("qb", "rb", "wr", "te", "flex", "bench")


class PlatformError(RuntimeError):
    """A sync failed. Deliberately loud -- a partial roster silently produces a
    plausible-looking report built on missing players."""


class MissingCredentials(PlatformError):
    pass


@dataclass
class Credentials:
    """Whatever a platform needs, read from the environment.

    Values are looked up under several names because the env file predates the
    per-platform prefixes and there's no reason to break someone's existing
    setup over a rename.
    """

    values: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, prefix: str, keys: dict[str, tuple[str, ...]]) -> Credentials:
        """Read `keys` from the environment.

        Each entry maps a canonical name to the env vars that might hold it, in
        preference order. load_dotenv() must already have run -- see load_env().
        """
        found = {}
        for canonical, candidates in keys.items():
            for name in (f"{prefix}_{canonical.upper()}", *candidates):
                value = os.getenv(name)
                if value:
                    found[canonical] = value
                    break
        return cls(values=found)

    def require(self, *names: str) -> tuple[str, ...]:
        missing = [n for n in names if not self.values.get(n)]
        if missing:
            raise MissingCredentials(
                f"missing credentials: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill them in."
            )
        return tuple(self.values[n] for n in names)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.values.get(name, default)


def load_env(path: str = ".env") -> None:
    """Load the .env file, if python-dotenv is installed.

    This has to run before any os.getenv() call and before a League object gets
    constructed. Skipping it doesn't fail loudly -- every credential just comes
    back None and you get an authentication error instead of a configuration
    one, which sends you looking in entirely the wrong place.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - depends on optional extra
        return
    load_dotenv(path)


class Platform(ABC):
    """What every platform adapter must provide."""

    name: str

    @abstractmethod
    def sync_with_status(
        self, credentials: Credentials
    ) -> tuple[dict[str, Roster], dict[str, str]]:
        """Fetch rosters and injury designations.

        Returns team name -> Roster, and player display name -> one of the
        shared status constants. The status dict only needs entries for players
        who aren't healthy.
        """

    def sync(self, credentials: Credentials) -> dict[str, Roster]:
        """Fetch rosters, discarding injury designations."""
        rosters, _ = self.sync_with_status(credentials)
        return rosters


# Every platform spells these differently. Mapping into one vocabulary here
# means the availability model never has to know which platform it's looking at.
_STATUS_ALIASES = {
    ACTIVE: ("ACTIVE", "A", "NORMAL", "HEALTHY", "PLAYING", ""),
    PROBABLE: ("PROBABLE", "P", "GTD", "DTD"),
    QUESTIONABLE: ("QUESTIONABLE", "Q", "QUEST"),
    DOUBTFUL: ("DOUBTFUL", "D"),
    OUT: ("OUT", "O", "SUSPENSION", "SUSPENDED", "NA", "NOT_ACTIVE"),
    INJURY_RESERVE: (
        "INJURY_RESERVE",
        "INJURED_RESERVE",
        "IR",
        "IR-R",
        "PUP",
        "NFI",
        "COVID_19",
        "FOUR_GAME_SUSPENSION",
        "SEASON_ENDING",
    ),
}

_LOOKUP = {
    alias.upper(): canonical for canonical, aliases in _STATUS_ALIASES.items() for alias in aliases
}


def normalize_status(raw: str | None) -> str:
    """Map a platform's designation into the shared vocabulary.

    Unrecognized designations become ACTIVE rather than something pessimistic.
    Platforms invent new tags, and quietly writing a player off because of a
    string nobody has seen before is a worse failure than ignoring it -- the
    latter is visible in the report, the former isn't.
    """
    if raw is None:
        return ACTIVE
    text = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
    if not text:
        return ACTIVE
    if text in STATUSES:
        return text
    return _LOOKUP.get(text, ACTIVE)


def empty_groups() -> dict[str, list[str]]:
    return {group: [] for group in GROUPS}


def to_roster(groups: dict[str, list[str]]) -> Roster:
    return Roster(**{group: list(groups.get(group, [])) for group in GROUPS})
