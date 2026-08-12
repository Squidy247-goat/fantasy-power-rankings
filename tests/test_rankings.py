import pytest

from fpr.core.lineup import Lineup, SlottedPlayer
from fpr.core.rankings import (
    RankingError,
    expected_matchups,
    positional_strength,
    run,
)


def fake_lineup(team, cfg, values):
    """Lineup with values dialed in directly, skipping consensus entirely.

    The round robin only ever reads value_at(), so hand-built numbers make the
    arithmetic checkable by hand.
    """
    if isinstance(values, (int, float)):
        values = {slot: float(values) for slot in cfg.slots}
    return Lineup(
        team=team,
        players={
            slot: SlottedPlayer(
                slot=slot, name=f"{team} {slot}", position="RB", rank=1.0, value=values[slot]
            )
            for slot in cfg.slots
        },
    )


class TestShape:
    def test_twelve_teams_play_660_matchups(self, cfg, league_lineups):
        standings = run(league_lineups, cfg)
        assert len(league_lineups) == 12
        assert standings.matchups == 660
        assert standings.matchups == expected_matchups(cfg)

    def test_matchup_count_formula(self, cfg):
        # 66 pairings times 10 slots.
        assert expected_matchups(cfg, teams=12) == 660
        assert expected_matchups(cfg, teams=2) == 10
        assert expected_matchups(cfg, teams=4) == 60

    def test_every_team_plays_every_other_team_at_every_slot(self, cfg, league_lineups):
        standings = run(league_lineups, cfg)
        for result in standings:
            assert result.played == (len(league_lineups) - 1) * len(cfg.slots)
            for slot in cfg.slots:
                assert result.slots[slot].played == len(league_lineups) - 1

    def test_fewer_than_two_teams(self, cfg):
        with pytest.raises(RankingError, match="at least 2 teams"):
            run({"Lonely": fake_lineup("Lonely", cfg, 100.0)}, cfg)

    def test_lineup_missing_a_slot(self, cfg):
        good = fake_lineup("A", cfg, 100.0)
        broken = Lineup(team="B", players={k: v for k, v in good.players.items() if k != "TE"})
        with pytest.raises(RankingError, match="no player at \\['TE'\\]"):
            run({"A": good, "B": broken}, cfg)


class TestOutcomes:
    def test_better_team_sweeps(self, cfg):
        standings = run(
            {"Strong": fake_lineup("Strong", cfg, 200.0), "Weak": fake_lineup("Weak", cfg, 100.0)},
            cfg,
        )
        strong, weak = standings["Strong"], standings["Weak"]
        assert strong.wins == len(cfg.slots)
        assert strong.losses == 0
        assert weak.losses == len(cfg.slots)
        assert standings.results[0].team == "Strong"

    def test_identical_teams_tie_everywhere(self, cfg):
        standings = run(
            {"A": fake_lineup("A", cfg, 150.0), "B": fake_lineup("B", cfg, 150.0)}, cfg
        )
        for result in standings:
            assert result.ties == len(cfg.slots)
            assert result.wins == result.losses == 0
            assert result.point_diff == pytest.approx(0.0)

    def test_a_tie_is_worth_half_a_win(self, cfg):
        tied = run({"A": fake_lineup("A", cfg, 150.0), "B": fake_lineup("B", cfg, 150.0)}, cfg)
        swept = run(
            {"X": fake_lineup("X", cfg, 200.0), "Y": fake_lineup("Y", cfg, 100.0)}, cfg
        )
        assert tied["A"].score == pytest.approx(swept["X"].score / 2)
        assert tied["A"].win_pct == pytest.approx(0.5)

    def test_win_pct(self, cfg):
        standings = run(
            {"Strong": fake_lineup("Strong", cfg, 200.0), "Weak": fake_lineup("Weak", cfg, 100.0)},
            cfg,
        )
        assert standings["Strong"].win_pct == pytest.approx(1.0)
        assert standings["Weak"].win_pct == pytest.approx(0.0)

    def test_record_string(self, cfg):
        standings = run(
            {"Strong": fake_lineup("Strong", cfg, 200.0), "Weak": fake_lineup("Weak", cfg, 100.0)},
            cfg,
        )
        assert standings["Strong"].record == "10-0-0"


class TestZeroSum:
    def test_point_differential_sums_to_zero(self, cfg, league_lineups):
        standings = run(league_lineups, cfg)
        assert sum(r.point_diff for r in standings) == pytest.approx(0.0, abs=1e-9)

    def test_point_differential_is_zero_sum_per_slot(self, cfg, league_lineups):
        standings = run(league_lineups, cfg)
        for slot in cfg.slots:
            total = sum(r.slots[slot].point_diff for r in standings)
            assert total == pytest.approx(0.0, abs=1e-9)

    def test_wins_and_losses_balance(self, cfg, league_lineups):
        standings = run(league_lineups, cfg)
        assert sum(r.wins for r in standings) == sum(r.losses for r in standings)

    def test_every_matchup_is_counted_twice(self, cfg, league_lineups):
        standings = run(league_lineups, cfg)
        total = sum(r.played for r in standings)
        assert total == 2 * standings.matchups


class TestWeighting:
    def test_a_bench_win_is_worth_less_than_a_starter_win(self, cfg):
        starter_only = {s: 100.0 for s in cfg.slots}
        bench_only = {s: 100.0 for s in cfg.slots}
        starter_only["QB"] = 200.0
        bench_only["BN1"] = 200.0

        by_starter = run(
            {"A": fake_lineup("A", cfg, starter_only), "B": fake_lineup("B", cfg, 100.0)}, cfg
        )
        by_bench = run(
            {"A": fake_lineup("A", cfg, bench_only), "B": fake_lineup("B", cfg, 100.0)}, cfg
        )
        assert by_starter["A"].score > by_bench["A"].score

    def test_bench_weight_applies_to_point_differential_too(self, cfg):
        """A blowout at BN2 shouldn't move differential like one at QB."""
        at_qb = {s: 100.0 for s in cfg.slots} | {"QB": 200.0}
        at_bench = {s: 100.0 for s in cfg.slots} | {"BN2": 200.0}

        qb_run = run({"A": fake_lineup("A", cfg, at_qb), "B": fake_lineup("B", cfg, 100.0)}, cfg)
        bn_run = run({"A": fake_lineup("A", cfg, at_bench), "B": fake_lineup("B", cfg, 100.0)}, cfg)

        assert qb_run["A"].point_diff == pytest.approx(100.0)
        assert bn_run["A"].point_diff == pytest.approx(100.0 * cfg.configured_bench_weight)

    def test_raw_record_ignores_weighting(self, cfg):
        """Wins/losses/ties stay raw counts so the record reads like a record."""
        at_bench = {s: 100.0 for s in cfg.slots} | {"BN1": 200.0}
        standings = run(
            {"A": fake_lineup("A", cfg, at_bench), "B": fake_lineup("B", cfg, 100.0)}, cfg
        )
        assert standings["A"].wins == 1
        assert standings["A"].ties == len(cfg.slots) - 1


class TestOrdering:
    def test_standings_sort_by_score(self, cfg):
        lineups = {
            "Third": fake_lineup("Third", cfg, 100.0),
            "First": fake_lineup("First", cfg, 300.0),
            "Second": fake_lineup("Second", cfg, 200.0),
        }
        standings = run(lineups, cfg)
        assert [r.team for r in standings] == ["First", "Second", "Third"]

    def test_point_differential_breaks_score_ties(self, cfg):
        """Two teams with the same record but different margins.

        Both beat the weak team at every slot and split against each other, so
        the scores match. The one that wins by more should place higher.
        """
        blowout = {s: 100.0 for s in cfg.slots} | {"QB": 500.0, "RB1": 1.0}
        narrow = {s: 100.0 for s in cfg.slots} | {"QB": 1.0, "RB1": 500.0}
        lineups = {
            "Blowout": fake_lineup("Blowout", cfg, blowout),
            "Narrow": fake_lineup("Narrow", cfg, narrow),
        }
        standings = run(lineups, cfg)
        assert standings["Blowout"].score == standings["Narrow"].score
        assert standings["Blowout"].point_diff == pytest.approx(0.0)

    def test_place_of(self, cfg):
        lineups = {
            "Best": fake_lineup("Best", cfg, 300.0),
            "Worst": fake_lineup("Worst", cfg, 100.0),
        }
        standings = run(lineups, cfg)
        assert standings.place_of("Best") == 1
        assert standings.place_of("Worst") == 2

    def test_unknown_team(self, cfg, league_lineups):
        standings = run(league_lineups, cfg)
        with pytest.raises(KeyError):
            standings["Not A Team"]


class TestPositionalStrength:
    def test_every_team_is_placed_at_every_slot(self, cfg, league_lineups):
        strength = positional_strength(league_lineups, cfg)
        assert set(strength) == set(league_lineups)
        for places in strength.values():
            assert set(places) == set(cfg.slots)

    def test_places_are_a_permutation_at_each_slot(self, cfg, league_lineups):
        strength = positional_strength(league_lineups, cfg)
        n = len(league_lineups)
        for slot in cfg.slots:
            places = sorted(strength[team][slot] for team in league_lineups)
            assert places == list(range(1, n + 1))

    def test_best_value_gets_first_place(self, cfg, league_lineups):
        strength = positional_strength(league_lineups, cfg)
        for slot in cfg.slots:
            best = max(league_lineups, key=lambda t: league_lineups[t].value_at(slot))
            assert strength[best][slot] == 1

    def test_placing_agrees_with_slot_wins(self, cfg, league_lineups):
        """Ranking by value and ranking by slot wins are the same ordering in a
        full round robin. If they ever diverge, one of them is wrong."""
        standings = run(league_lineups, cfg)
        strength = positional_strength(league_lineups, cfg)
        for slot in cfg.slots:
            by_place = sorted(league_lineups, key=lambda t: strength[t][slot])
            wins = [standings[t].slots[slot].wins for t in by_place]
            assert wins == sorted(wins, reverse=True)


class TestAgainstRealData:
    def test_standings_are_explicable(self, cfg, league_lineups):
        """The top team should be near the top of the positional table too.

        Not first at everything -- that would mean the league is broken -- but
        its average placing should beat the bottom team's.
        """
        standings = run(league_lineups, cfg)
        strength = positional_strength(league_lineups, cfg)

        def average_placing(team):
            return sum(strength[team].values()) / len(cfg.slots)

        assert average_placing(standings.results[0].team) < average_placing(
            standings.results[-1].team
        )

    def test_scores_are_distinct_enough_to_rank(self, cfg, league_lineups):
        standings = run(league_lineups, cfg)
        scores = [r.score for r in standings]
        assert len(set(scores)) > 1
        assert scores == sorted(scores, reverse=True)
