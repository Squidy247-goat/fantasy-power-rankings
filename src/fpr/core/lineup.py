"""Turning a roster into an ordered lineup.

Two rules here carry most of the weight.

Sort within every group by value, best first, and never trust the order the
platform hands back. RB1 has to be the team's better back or the RB1-vs-RB1
matchup is comparing whichever back ESPN happened to list first, which is
sometimes alphabetical and sometimes just stale. This is also why the optimal
lineup mode exists -- managers forget to set their lineup and leave their best
player on the bench, and "how good is this roster" and "how good is this roster
as actually set" are different questions worth asking separately.

Bench eligibility is RB/WR/TE and deliberately excludes QB. In a single-QB
league a backup QB almost never enters a lineup, so counting one would reward
hoarding a position you can't use over the usable depth that actually covers
an injury. Only the best two bench players count at all, for the same reason:
a fourth wide receiver is not a meaningful asset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fpr.config import LeagueConfig
from fpr.core.consensus import ConsensusTable
from fpr.core.values import value as curve_value


class LineupError(ValueError):
    """Raised for a roster that can't produce a legal lineup."""


@dataclass(frozen=True)
class Roster:
    """A team's players, grouped the way every platform adapter returns them."""

    qb: list[str] = field(default_factory=list)
    rb: list[str] = field(default_factory=list)
    wr: list[str] = field(default_factory=list)
    te: list[str] = field(default_factory=list)
    flex: list[str] = field(default_factory=list)
    bench: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Roster:
        unknown = set(data) - {"qb", "rb", "wr", "te", "flex", "bench"}
        if unknown:
            raise LineupError(f"unknown roster groups: {sorted(unknown)}")
        return cls(**{k: list(v) for k, v in data.items()})

    def all_players(self) -> list[str]:
        return [*self.qb, *self.rb, *self.wr, *self.te, *self.flex, *self.bench]


@dataclass(frozen=True)
class SlottedPlayer:
    slot: str
    name: str
    position: str
    rank: float
    value: float


@dataclass(frozen=True)
class Lineup:
    team: str
    players: dict[str, SlottedPlayer]

    def __getitem__(self, slot: str) -> SlottedPlayer:
        return self.players[slot]

    def value_at(self, slot: str) -> float:
        return self.players[slot].value

    @property
    def slots(self) -> list[str]:
        return list(self.players)


def _resolve(names: list[str], table: ConsensusTable, cfg: LeagueConfig) -> list[SlottedPlayer]:
    """Look up each name and attach its value, best first.

    Slot is filled in later -- these are unassigned until they're ordered.
    """
    resolved = []
    for name in names:
        player = table[name]  # raises with an actionable message if absent
        resolved.append(
            SlottedPlayer(
                slot="",
                name=player.name,
                position=player.position,
                rank=player.rank,
                value=curve_value(player.rank, cfg.value_curve),
            )
        )
    # Best value first. Name breaks ties so the output is stable run to run.
    return sorted(resolved, key=lambda p: (-p.value, p.name))


def _assign(players: list[SlottedPlayer], slots: list[str]) -> list[SlottedPlayer]:
    from dataclasses import replace

    return [replace(p, slot=slot) for p, slot in zip(players, slots, strict=True)]


def _check_group(team: str, group: str, names: list[str], expected: int) -> None:
    if len(names) != expected:
        raise LineupError(
            f"{team}: expected {expected} player(s) in {group}, got {len(names)} ({names}). "
            f"Kickers and defenses should be left out of the roster entirely."
        )


def build(team: str, roster: Roster, table: ConsensusTable, cfg: LeagueConfig) -> Lineup:
    """Build a lineup from a roster as the platform has it slotted."""
    shape = cfg.roster_shape
    _check_group(team, "qb", roster.qb, shape.qb)
    _check_group(team, "rb", roster.rb, shape.rb)
    _check_group(team, "wr", roster.wr, shape.wr)
    _check_group(team, "te", roster.te, shape.te)
    _check_group(team, "flex", roster.flex, shape.flex)

    eligible = set(cfg.bench_eligible_positions)
    bench = [p for p in _resolve(roster.bench, table, cfg) if p.position in eligible]
    if len(bench) < shape.bench_min:
        raise LineupError(
            f"{team}: needs at least {shape.bench_min} bench players at "
            f"{sorted(eligible)}, found {len(bench)}. Backup QBs don't count "
            f"toward bench depth in a single-QB league."
        )

    slotted = [
        *_assign(_resolve(roster.qb, table, cfg), ["QB"]),
        *_assign(_resolve(roster.rb, table, cfg), ["RB1", "RB2"]),
        *_assign(_resolve(roster.wr, table, cfg), ["WR1", "WR2"]),
        *_assign(_resolve(roster.te, table, cfg), ["TE"]),
        *_assign(_resolve(roster.flex, table, cfg), ["FLEX1", "FLEX2"]),
        # Only the best two bench players score; the rest of the bench is real
        # but not worth anything a round robin can measure.
        *_assign(bench[: len(cfg.bench_slots)], list(cfg.bench_slots)),
    ]

    by_slot = {p.slot: p for p in slotted}
    missing = [s for s in cfg.slots if s not in by_slot]
    if missing:
        raise LineupError(f"{team}: no player assigned to {missing}")

    return Lineup(team=team, players={slot: by_slot[slot] for slot in cfg.slots})


def build_optimal(team: str, roster: Roster, table: ConsensusTable, cfg: LeagueConfig) -> Lineup:
    """Build the best legal lineup from the roster, ignoring how it's set.

    Answers "how good could this roster be" rather than "how good is it right
    now". Greedy is genuinely optimal here: every starter slot weighs the same,
    so filling the fixed position slots with the best available at each, then
    taking the best leftovers for FLEX, maximizes total starter value.
    """
    shape = cfg.roster_shape
    pool = _resolve(roster.all_players(), table, cfg)

    def take(position: str, count: int) -> list[SlottedPlayer]:
        nonlocal pool
        picked = [p for p in pool if p.position == position][:count]
        if len(picked) < count:
            raise LineupError(
                f"{team}: needs {count} player(s) at {position} for a legal lineup, "
                f"roster has {len(picked)}"
            )
        chosen = {id(p) for p in picked}
        pool = [p for p in pool if id(p) not in chosen]
        return picked

    qb = take("QB", shape.qb)
    rb = take("RB", shape.rb)
    wr = take("WR", shape.wr)
    te = take("TE", shape.te)

    eligible = set(cfg.bench_eligible_positions)
    remaining = [p for p in pool if p.position in eligible]
    flex = remaining[: shape.flex]
    if len(flex) < shape.flex:
        raise LineupError(
            f"{team}: needs {shape.flex} flex-eligible player(s) at {sorted(eligible)} "
            f"after filling the position slots, found {len(flex)}"
        )

    bench = remaining[shape.flex : shape.flex + len(cfg.bench_slots)]
    if len(bench) < shape.bench_min:
        raise LineupError(
            f"{team}: needs at least {shape.bench_min} bench players at {sorted(eligible)}, "
            f"found {len(bench)}"
        )

    slotted = [
        *_assign(qb, ["QB"]),
        *_assign(rb, ["RB1", "RB2"]),
        *_assign(wr, ["WR1", "WR2"]),
        *_assign(te, ["TE"]),
        *_assign(flex, ["FLEX1", "FLEX2"]),
        *_assign(bench, list(cfg.bench_slots)),
    ]
    by_slot = {p.slot: p for p in slotted}
    return Lineup(team=team, players={slot: by_slot[slot] for slot in cfg.slots})
