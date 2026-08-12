"""Monte Carlo over ranking uncertainty and availability.

The deterministic model gives one answer and states it with confidence it
hasn't earned. Four sources disagree about these players -- sometimes by a
hundred spots -- and averaging that disagreement away doesn't resolve it, it
just hides it. Run `coin_flip_fraction` on this league and a large share of the
660 slot matchups turn out to be decided by less than the sources' own spread.
Those aren't wins, they're coin flips being reported as certainties.

So every trial redraws each player's rank from a normal centred on his
consensus and widened by how much the sources argue about him, then replays the
whole season. The output is a distribution over finish position rather than a
number.

Two things worth knowing about how the season replay works.

**Lineups are re-sorted every trial.** If a drawn rank makes the nominal RB2 the
better back that trial, he plays RB1. Slot order is a consequence of value, not
a fixed property of the roster, and freezing it would let the model compare a
team's worse back against everyone else's better one.

**Bench value is earned, not assumed.** The deterministic model needs a bench
weight, and rather than guess it, this simulates a 14-week season where every
starter has a per-week chance of being unavailable. When one is out, the best
healthy bench player who's eligible for that slot covers -- and a bench player
who is himself unavailable can't, which is what keeps this from being a free
lunch. The share of realized lineup value that came from those substitutions is
the bench weight, measured rather than assumed.

A consequence worth noticing: nobody covers for an injured quarterback, because
backup QBs aren't bench-eligible. That's the rule from section 1.4 doing what
it's supposed to do. A single-QB league that loses its starter loses the slot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from fpr.config import LeagueConfig
from fpr.core import availability as avail_mod
from fpr.core.lineup import Lineup
from fpr.core.values import value as curve_value
from fpr.pipeline import League


class SimulationError(ValueError):
    pass


@dataclass(frozen=True)
class TeamPlan:
    """Static index arrays into the global player table, one set per team."""

    team: str
    groups: dict[str, np.ndarray]  # 'qb'/'rb'/'wr'/'te'/'flex' -> player indices
    bench: np.ndarray


@dataclass
class SimulationResult:
    teams: list[str]
    trials: int
    weeks: int
    finishes: dict[str, np.ndarray]  # team -> count of finishes at each place
    bench_weight: float
    bench_weight_ci: tuple[float, float]
    configured_bench_weight: float
    bench_weight_tolerance: float
    flat_availability: bool
    mean_score: dict[str, float] = field(default_factory=dict)

    def expected_finish(self, team: str) -> float:
        counts = self.finishes[team]
        places = np.arange(1, len(counts) + 1)
        return float((counts * places).sum() / counts.sum())

    def probability(self, team: str, places) -> float:
        """P(team finishes in any of these 1-based places)."""
        counts = self.finishes[team]
        return float(sum(counts[p - 1] for p in places) / counts.sum())

    def p_first(self, team: str) -> float:
        return self.probability(team, [1])

    def p_top(self, team: str, n: int) -> float:
        return self.probability(team, range(1, n + 1))

    def p_last(self, team: str) -> float:
        return self.probability(team, [len(self.teams)])

    def ordered(self) -> list[str]:
        """Teams by expected finish, best first."""
        return sorted(self.teams, key=lambda t: (self.expected_finish(t), t))

    @property
    def bench_weight_disagrees(self) -> bool:
        """Whether the configured weight has drifted from the measured one."""
        return abs(self.bench_weight - self.configured_bench_weight) > self.bench_weight_tolerance


def slot_positions(cfg: LeagueConfig) -> dict[str, frozenset[str]]:
    """Which positions may fill each slot.

    Derived from the slot name so a league with different slots still works:
    QB2 wants a QB, FLEX3 takes anything bench-eligible.
    """
    eligible = frozenset(cfg.bench_eligible_positions)
    mapping = {}
    for slot in cfg.starter_slots:
        base = re.sub(r"\d+$", "", slot).upper()
        if base in cfg.skill_positions:
            mapping[slot] = frozenset({base})
        elif base in ("FLEX", "OP", "SUPERFLEX"):
            mapping[slot] = eligible
        else:
            raise SimulationError(f"don't know what positions can fill slot {slot!r}")
    return mapping


def _plans(league: League) -> tuple[list[str], TeamPlan, np.ndarray, np.ndarray, np.ndarray]:
    """Flatten the league into arrays the trial loop can index into."""
    keys: dict[str, int] = {}
    names: list[str] = []
    positions: list[str] = []
    ranks: list[float] = []
    spreads: list[float] = []

    def index_of(name: str) -> int:
        player = league.table[name]
        if player.key not in keys:
            keys[player.key] = len(names)
            names.append(player.name)
            positions.append(player.position)
            ranks.append(player.rank)
            spreads.append(player.spread)
        return keys[player.key]

    eligible = set(league.cfg.bench_eligible_positions)
    plans = []
    for team, roster in league.rosters.items():
        groups = {
            group: np.array([index_of(n) for n in getattr(roster, group)], dtype=np.intp)
            for group in ("qb", "rb", "wr", "te", "flex")
        }
        bench = np.array(
            [index_of(n) for n in roster.bench if league.table[n].position in eligible],
            dtype=np.intp,
        )
        plans.append(TeamPlan(team=team, groups=groups, bench=bench))

    return (
        names,
        plans,
        np.array(positions),
        np.array(ranks, dtype=float),
        np.array(spreads, dtype=float),
    )


def _rates(names, positions, league: League, flat: bool) -> np.ndarray:
    cfg = league.cfg
    if flat:
        return np.full(len(names), avail_mod.flat_rate(cfg), dtype=float)
    return np.array(
        [
            avail_mod.for_player(name, pos, cfg, league.injury_status).rate
            for name, pos in zip(names, positions, strict=True)
        ],
        dtype=float,
    )


def _starter_indices(plan: TeamPlan, values: np.ndarray, cfg: LeagueConfig) -> list[int]:
    """Player index for each starter slot, groups re-sorted by drawn value."""
    ordered: list[int] = []
    for group, count in (
        ("qb", cfg.roster_shape.qb),
        ("rb", cfg.roster_shape.rb),
        ("wr", cfg.roster_shape.wr),
        ("te", cfg.roster_shape.te),
        ("flex", cfg.roster_shape.flex),
    ):
        idx = plan.groups[group]
        best_first = idx[np.argsort(-values[idx], kind="stable")]
        ordered.extend(int(i) for i in best_first[:count])
    return ordered


def run(
    league: League,
    *,
    trials: int | None = None,
    weeks: int | None = None,
    seed: int | None = None,
    flat_availability: bool = False,
) -> SimulationResult:
    cfg = league.cfg
    trials = trials if trials is not None else cfg.simulation.trials
    weeks = weeks if weeks is not None else cfg.simulation.weeks
    if trials < 1:
        raise SimulationError(f"need at least one trial, got {trials}")

    names, plans, positions, base_ranks, spreads = _plans(league)
    rates = _rates(names, positions, league, flat_availability)

    starter_slots = list(cfg.starter_slots)
    allowed = slot_positions(cfg)
    slot_allows = [allowed[s] for s in starter_slots]
    weights = np.array([cfg.slot_weights[s] for s in starter_slots], dtype=float)

    n_teams = len(plans)
    n_slots = len(starter_slots)
    rng = np.random.default_rng(seed)

    finishes = np.zeros((n_teams, n_teams), dtype=np.int64)
    score_totals = np.zeros(n_teams, dtype=float)
    bench_fractions = np.empty(trials, dtype=float)

    for trial in range(trials):
        drawn = np.maximum(1.0, rng.normal(base_ranks, spreads))
        values = np.array([curve_value(r, cfg.value_curve) for r in drawn])

        healthy = rng.random((weeks, len(names))) < rates

        realized = np.zeros((n_teams, n_slots), dtype=float)
        from_bench = 0.0
        total = 0.0

        for t, plan in enumerate(plans):
            starters = _starter_indices(plan, values, cfg)
            starter_values = values[starters]
            bench_order = plan.bench[np.argsort(-values[plan.bench], kind="stable")]
            bench_positions = positions[bench_order]

            for week in range(weeks):
                week_healthy = healthy[week]
                out = [s for s in range(n_slots) if not week_healthy[starters[s]]]

                if not out:
                    realized[t] += starter_values
                    total += starter_values.sum()
                    continue

                for s in range(n_slots):
                    if week_healthy[starters[s]]:
                        realized[t, s] += starter_values[s]
                        total += starter_values[s]

                # Only bench players who are themselves healthy this week can
                # cover, and each of them covers at most one slot.
                used = set()
                for s in out:
                    for b, (idx, pos) in enumerate(
                        zip(bench_order, bench_positions, strict=True)
                    ):
                        if b in used or pos not in slot_allows[s] or not week_healthy[idx]:
                            continue
                        cover = values[idx]
                        realized[t, s] += cover
                        from_bench += cover
                        total += cover
                        used.add(b)
                        break

        realized /= weeks
        bench_fractions[trial] = from_bench / total if total else 0.0

        score = _round_robin_scores(realized, weights)
        score_totals += score
        order = np.argsort(-score, kind="stable")
        for place, team_idx in enumerate(order):
            finishes[team_idx, place] += 1

    teams = [plan.team for plan in plans]
    low, high = np.percentile(bench_fractions, [2.5, 97.5])

    return SimulationResult(
        teams=teams,
        trials=trials,
        weeks=weeks,
        finishes={plan.team: finishes[i] for i, plan in enumerate(plans)},
        bench_weight=float(bench_fractions.mean()),
        bench_weight_ci=(float(low), float(high)),
        configured_bench_weight=cfg.configured_bench_weight,
        bench_weight_tolerance=cfg.bench_weight_tolerance,
        flat_availability=flat_availability,
        mean_score={plan.team: float(score_totals[i] / trials) for i, plan in enumerate(plans)},
    )


def _round_robin_scores(realized: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted slot wins for every team, all pairs at once.

    Same rules as the deterministic round robin -- higher value takes the slot,
    equal values split it -- just expressed as a broadcast comparison so it can
    run a couple of thousand times without taking all afternoon.
    """
    score = np.zeros(realized.shape[0], dtype=float)
    for s in range(realized.shape[1]):
        column = realized[:, s]
        diff = column[:, None] - column[None, :]
        wins = (diff > 0).sum(axis=1)
        ties = (diff == 0).sum(axis=1) - 1  # every team ties itself
        score += weights[s] * (wins + 0.5 * ties)
    return score


def coin_flip_fraction(league: League) -> float:
    """Share of deterministic slot matchups closer than the sources' spread.

    Section 2.1's premise, measured on whatever league is loaded rather than
    quoted from the spec. For each matchup, the two players' rank spreads are
    converted onto the value scale and combined; if the margin of victory is
    smaller than that, the sources don't actually agree on who won.
    """
    cfg = league.cfg
    lineups = list(league.lineups.values())

    uncertainty: dict[str, float] = {}

    def value_sd(lineup: Lineup, slot: str) -> float:
        player = league.table[lineup[slot].name]
        key = f"{player.key}"
        if key not in uncertainty:
            here = curve_value(player.rank, cfg.value_curve)
            there = curve_value(player.rank + player.spread, cfg.value_curve)
            uncertainty[key] = abs(here - there)
        return uncertainty[key]

    close = 0
    total = 0
    for i, home in enumerate(lineups):
        for away in lineups[i + 1 :]:
            for slot in cfg.slots:
                margin = abs(home.value_at(slot) - away.value_at(slot))
                combined = np.hypot(value_sd(home, slot), value_sd(away, slot))
                total += 1
                if margin < combined:
                    close += 1
    return close / total if total else 0.0
