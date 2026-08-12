"""The round robin.

Every pair of teams plays every slot once. Twelve teams is 66 pairings, ten
slots each, so 660 individual matchups. Higher value takes the slot; equal
values split it.

Ranking by slot wins instead of by total roster value is the entire reason this
tool exists. Total value rewards hoarding -- four good backs look like a great
team even though only two of them can start on any given week. Slot-by-slot
asks the question that actually decides fantasy games: at each position, is
your guy better than his guy?

A note on weighting. Bench slots count for less than starters, and the weight
applies to both the score and the point differential rather than to the score
alone. A blowout at BN2 shouldn't move a team's differential as much as the
same blowout at QB, for the same reason it shouldn't count as a full win.
Wins, losses and ties stay as raw counts so the record still reads like a
record; `score` is the weighted version, and it's what standings sort on.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from fpr.config import LeagueConfig
from fpr.core.lineup import Lineup


class RankingError(ValueError):
    pass


@dataclass
class SlotRecord:
    wins: int = 0
    losses: int = 0
    ties: int = 0
    point_diff: float = 0.0

    @property
    def played(self) -> int:
        return self.wins + self.losses + self.ties


@dataclass
class TeamResult:
    team: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    score: float = 0.0
    point_diff: float = 0.0
    slots: dict[str, SlotRecord] = field(default_factory=dict)

    @property
    def played(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def win_pct(self) -> float:
        """Ties count as half a win, the usual convention."""
        if not self.played:
            return 0.0
        return (self.wins + 0.5 * self.ties) / self.played

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}-{self.ties}"


@dataclass
class Standings:
    results: list[TeamResult]
    matchups: int

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, team):
        if isinstance(team, int):
            return self.results[team]
        for result in self.results:
            if result.team == team:
                return result
        raise KeyError(team)

    def place_of(self, team: str) -> int:
        """1-based finishing position."""
        for i, result in enumerate(self.results, start=1):
            if result.team == team:
                return i
        raise KeyError(team)


def run(lineups: dict[str, Lineup], cfg: LeagueConfig) -> Standings:
    """Play every team against every other team at every slot."""
    teams = list(lineups)
    if len(teams) < 2:
        raise RankingError(f"need at least 2 teams to run a round robin, got {len(teams)}")

    for team, lineup in lineups.items():
        missing = [s for s in cfg.slots if s not in lineup.players]
        if missing:
            raise RankingError(f"{team} has no player at {missing}")

    results = {
        team: TeamResult(team=team, slots={s: SlotRecord() for s in cfg.slots}) for team in teams
    }

    matchups = 0
    for home, away in itertools.combinations(teams, 2):
        for slot in cfg.slots:
            weight = cfg.slot_weights[slot]
            margin = lineups[home].value_at(slot) - lineups[away].value_at(slot)
            _record(results[home], results[away], slot, margin, weight)
            matchups += 1

    ordered = sorted(
        results.values(),
        key=lambda r: (-r.score, -r.point_diff, r.team),
    )
    return Standings(results=ordered, matchups=matchups)


def _record(home: TeamResult, away: TeamResult, slot: str, margin: float, weight: float) -> None:
    weighted = margin * weight
    home.point_diff += weighted
    away.point_diff -= weighted
    home.slots[slot].point_diff += weighted
    away.slots[slot].point_diff -= weighted

    if margin > 0:
        winner, loser = home, away
    elif margin < 0:
        winner, loser = away, home
    else:
        # Exact tie. Splitting 0.5/0.5 keeps the league's total score constant
        # regardless of how many ties there are.
        for team in (home, away):
            team.ties += 1
            team.slots[slot].ties += 1
            team.score += 0.5 * weight
        return

    winner.wins += 1
    winner.slots[slot].wins += 1
    winner.score += weight
    loser.losses += 1
    loser.slots[slot].losses += 1


def expected_matchups(cfg: LeagueConfig, teams: int | None = None) -> int:
    """How many slot matchups a full round robin should produce."""
    n = teams if teams is not None else cfg.teams
    return n * (n - 1) // 2 * len(cfg.slots)


def positional_strength(lineups: dict[str, Lineup], cfg: LeagueConfig) -> dict[str, dict[str, int]]:
    """Each team's 1-N placing at each slot, 1 being the league's best.

    Ranked on value rather than on slot wins. In a full round robin the two
    orderings are the same -- a higher value beats strictly more opponents at
    that slot -- and value avoids having to break ties in the win counts.

    This table is what makes the standings explicable. "Why is this team
    fourth" should always be answerable by reading one row.
    """
    strength: dict[str, dict[str, int]] = {team: {} for team in lineups}
    for slot in cfg.slots:
        ordered = sorted(
            lineups.items(),
            key=lambda kv: (-kv[1].value_at(slot), kv[0]),
        )
        for place, (team, _) in enumerate(ordered, start=1):
            strength[team][slot] = place
    return strength
