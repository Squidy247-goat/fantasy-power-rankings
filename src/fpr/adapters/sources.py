"""Merging several sources into one set of raw per-source ranks.

The consensus code takes a list of RawPlayer, each carrying whatever ranks the
sources published for that player. It doesn't care whether a given column came
out of a CSV export or off an HTTP call, and that's deliberate -- it's what
lets one source be automated while the others stay manual exports without the
maths changing at all.

Merging is by normalized name, so a player the CSV spells "James Cook III" and
an API returns as "James Cook" ends up with both ranks on one row rather than
as two half-ranked players.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fpr.adapters.raw_csv import RawPlayer
from fpr.core.names import normalize


@dataclass
class Merged:
    players: list[RawPlayer]
    warnings: list[str] = field(default_factory=list)


def combine(*groups: list[RawPlayer]) -> Merged:
    """Merge several per-source player lists into one.

    Earlier groups win on the display name and position, so pass the
    hand-maintained file first and let automated sources fill in around it.
    Rank columns union rather than overwrite -- a player listed by both keeps
    both numbers.
    """
    merged: dict[str, RawPlayer] = {}
    warnings: list[str] = []

    for group in groups:
        for player in group:
            key = normalize(player.name)
            existing = merged.get(key)

            if existing is None:
                merged[key] = player
                continue

            if existing.position != player.position:
                warnings.append(
                    f"{existing.name} is {existing.position} in one source and "
                    f"{player.position} in another; keeping {existing.position}"
                )

            overlap = set(existing.ranks) & set(player.ranks)
            if overlap:
                warnings.append(
                    f"{existing.name} has two values for {sorted(overlap)}; keeping the first"
                )

            merged[key] = RawPlayer(
                name=existing.name,
                position=existing.position,
                # Existing last so it wins any collision, per the rule above.
                ranks={**player.ranks, **existing.ranks},
            )

    return Merged(players=list(merged.values()), warnings=warnings)
