"""Combining several sources' rankings into one.

Two things here are easy to get wrong and both are silent when you do.

The first is re-indexing. Sources publish one big list with kickers and
defenses mixed in, and they don't agree on how many or where. Averaging raw
ranks across lists carrying different amounts of K/DST noise biases the result
toward whichever source pads its list least. So each source gets filtered to
skill positions and renumbered 1..N over what's left, and only those dense
ranks ever get compared to each other.

The second is missing players. A source that doesn't list someone hasn't
ranked him last -- it has no opinion, and the fix is to leave that source out
of his average rather than substitute a penalty. A player no source lists at
all is a different case and does get a number: replacement level, computed off
the deepest rank anyone actually published. He never gets dropped, because
dropping him turns a rostered player into a KeyError somewhere downstream
instead of a low number here.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, replace

from fpr.config import LeagueConfig
from fpr.core.names import normalize


class ConsensusError(ValueError):
    pass


@dataclass(frozen=True)
class PlayerConsensus:
    name: str  # display spelling, for reports
    key: str  # normalized, for lookups
    position: str
    # Re-indexed dense rank per source that listed him. Never raw ranks.
    source_ranks: dict[str, float]
    rank: float
    spread: float
    ranked: bool  # False means no source listed him; rank is replacement level

    @property
    def source_count(self) -> int:
        return len(self.source_ranks)


class ConsensusTable:
    """Lookup by any spelling of a player's name."""

    def __init__(self, players: list[PlayerConsensus]):
        self._by_key = {p.key: p for p in players}
        self._players = players

    def __len__(self) -> int:
        return len(self._players)

    def __iter__(self):
        return iter(self._players)

    def __contains__(self, name: str) -> bool:
        return normalize(name) in self._by_key

    def __getitem__(self, name: str) -> PlayerConsensus:
        try:
            return self._by_key[normalize(name)]
        except KeyError:
            raise KeyError(
                f"{name!r} is not in the consensus table. Every rostered player "
                f"needs a row in the rankings input, even if no source lists him."
            ) from None

    def get(self, name: str, default=None):
        return self._by_key.get(normalize(name), default)

    def ordered(self) -> list[PlayerConsensus]:
        """Best consensus rank first."""
        return sorted(self._players, key=lambda p: (p.rank, p.name))


def reindex(raw_ranks: dict[str, float]) -> dict[str, int]:
    """Renumber one source's ranks to a dense 1..N.

    Input maps player key -> whatever rank that source published; output maps
    the same keys to their position in that source's ordering. Ties break by
    key so the result doesn't depend on dict ordering.
    """
    ordered = sorted(raw_ranks.items(), key=lambda kv: (kv[1], kv[0]))
    return {key: i for i, (key, _) in enumerate(ordered, start=1)}


def _spread(ranks: list[float], cfg: LeagueConfig) -> float:
    """How much the sources argue about this player.

    Feeds the simulation's per-player draw width. One source gets a wide spread
    rather than a narrow one -- a lone opinion has less standing than a
    contested average, not more, and treating an unchallenged number as certain
    is exactly the overconfidence the simulation exists to avoid.
    """
    sim = cfg.simulation
    if len(ranks) < 2:
        return sim.single_source_spread
    return max(sim.min_spread, statistics.stdev(ranks))


def build(raw_players, cfg: LeagueConfig) -> ConsensusTable:
    """Build the consensus table from raw per-source ranks."""
    skill = set(cfg.skill_positions)
    players = [p for p in raw_players if p.position in skill]
    if not players:
        raise ConsensusError(
            f"no players at skill positions {sorted(skill)} in the rankings input"
        )

    keys = {}
    for player in players:
        key = normalize(player.name)
        if key in keys and keys[key] != player.name:
            raise ConsensusError(
                f"{player.name!r} and {keys[key]!r} normalize to the same key "
                f"{key!r}; one would silently shadow the other"
            )
        keys[key] = player.name

    # Re-index each source independently over the skill-position players it lists.
    dense: dict[str, dict[str, int]] = {}
    for source in _sources(players):
        listed = {
            normalize(p.name): p.ranks[source] for p in players if source in p.ranks
        }
        dense[source] = reindex(listed)

    built = []
    for player in players:
        key = normalize(player.name)
        source_ranks = {
            source: float(ranks[key]) for source, ranks in dense.items() if key in ranks
        }
        listed = list(source_ranks.values())
        built.append(
            PlayerConsensus(
                name=player.name,
                key=key,
                position=player.position,
                source_ranks=source_ranks,
                rank=statistics.fmean(listed) if listed else 0.0,
                spread=_spread(listed, cfg),
                ranked=bool(listed),
            )
        )

    return ConsensusTable(_fill_unranked(built, cfg))


def _fill_unranked(players: list[PlayerConsensus], cfg: LeagueConfig) -> list[PlayerConsensus]:
    """Give players nobody listed a replacement-level rank."""
    ranked = [p.rank for p in players if p.ranked]
    if not ranked:
        raise ConsensusError("no player was listed by any source")

    replacement = max(ranked) + cfg.consensus.unranked_offset
    return [p if p.ranked else replace(p, rank=replacement) for p in players]


def _sources(players) -> list[str]:
    seen: dict[str, None] = {}
    for player in players:
        for source in player.ranks:
            seen.setdefault(source, None)
    return list(seen)
