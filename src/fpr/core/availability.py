"""How often a player is actually in the lineup.

Two multiplicative factors, both documented estimates rather than fitted
values -- which is worth saying out loud, because a number in a config file
looks equally authoritative whether it came from a regression or from someone's
reasonable guess. These are the latter. Fitting them against real outcomes is
what the history/calibration work is eventually for.

**Position base rate.** Running backs miss the most games and quarterbacks the
fewest, which is not a subtle effect and shouldn't be flattened into one league
-wide number.

**Injury designation.** A player's current status matters, but the multipliers
are deliberately gentler than the odds of him sitting out one specific week.
This is a season-long factor: a QUESTIONABLE tag in week 1 is usually a
non-issue by week 4, so treating it as a 15% haircut across fourteen weeks is
closer to right than treating it as a 15% chance of missing every single one.

The product is clamped at both ends. Nobody plays every snap of every game and
nobody is completely written off, so modelling anyone at 0.0 or 1.0 would be
false precision in a model whose entire purpose is refusing to state things
more confidently than the inputs support.
"""

from __future__ import annotations

from dataclasses import dataclass

from fpr.config import LeagueConfig
from fpr.core.names import normalize

# One vocabulary, which every platform adapter maps into before anything here
# sees it. ESPN, Yahoo and Sleeper all spell these differently.
ACTIVE = "ACTIVE"
PROBABLE = "PROBABLE"
QUESTIONABLE = "QUESTIONABLE"
DOUBTFUL = "DOUBTFUL"
OUT = "OUT"
INJURY_RESERVE = "INJURY_RESERVE"

STATUSES = (ACTIVE, PROBABLE, QUESTIONABLE, DOUBTFUL, OUT, INJURY_RESERVE)


@dataclass(frozen=True)
class PlayerAvailability:
    name: str
    position: str
    status: str
    rate: float


def status_for(name: str, injury_status: dict[str, str] | None) -> str:
    """Look up a designation by any spelling of the player's name."""
    if not injury_status:
        return ACTIVE
    key = normalize(name)
    for raw_name, status in injury_status.items():
        if normalize(raw_name) == key:
            return (status or ACTIVE).upper()
    return ACTIVE


def for_player(
    name: str, position: str, cfg: LeagueConfig, injury_status: dict[str, str] | None = None
) -> PlayerAvailability:
    status = status_for(name, injury_status)
    return PlayerAvailability(
        name=name,
        position=position,
        status=status,
        rate=cfg.availability.rate_for(position, status),
    )


def flat_rate(cfg: LeagueConfig) -> float:
    """One rate for everybody, as a comparison baseline.

    The mean of the position base rates, ignoring position and designation
    entirely. Exists so the per-player model can be shown to actually change
    something -- if switching between the two moves nobody in the standings,
    the per-player path isn't wired up properly.
    """
    rates = cfg.availability.position_base_rate.values()
    return sum(rates) / len(rates)
