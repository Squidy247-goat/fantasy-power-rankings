import numpy as np
import pytest

from fpr import pipeline
from fpr.core import availability, simulate
from fpr.core.simulate import SimulationError, coin_flip_fraction, slot_positions

# Trial counts are small on purpose. These test that the machinery is wired up
# correctly, not that a probability has converged -- the assertions are all
# either structural or wide enough to survive the noise.
QUICK = 60
STEADY = 300


@pytest.fixture(scope="module")
def result(league):
    return simulate.run(league, trials=STEADY, seed=101)


class TestAvailability:
    def test_position_and_status_multiply(self, cfg):
        healthy = availability.for_player("Someone", "RB", cfg)
        hurt = availability.for_player("Someone", "RB", cfg, {"Someone": "QUESTIONABLE"})
        assert hurt.rate == pytest.approx(healthy.rate * 0.85)

    def test_status_lookup_normalizes_names(self, cfg):
        hurt = availability.for_player(
            "Marvin Harrison", "WR", cfg, {"Marvin Harrison Jr.": "DOUBTFUL"}
        )
        assert hurt.status == "DOUBTFUL"

    def test_unlisted_player_is_active(self, cfg):
        player = availability.for_player("Someone", "WR", cfg, {"Someone Else": "OUT"})
        assert player.status == "ACTIVE"

    def test_injured_reserve_is_nearly_but_not_quite_zero(self, cfg):
        player = availability.for_player("Someone", "RB", cfg, {"Someone": "INJURY_RESERVE"})
        assert 0.02 <= player.rate < 0.1

    def test_flat_rate_sits_among_the_position_rates(self, cfg):
        rates = cfg.availability.position_base_rate.values()
        assert min(rates) < availability.flat_rate(cfg) < max(rates)


class TestSlotEligibility:
    def test_position_slots_want_their_position(self, cfg):
        mapping = slot_positions(cfg)
        assert mapping["RB1"] == frozenset({"RB"})
        assert mapping["RB2"] == frozenset({"RB"})
        assert mapping["TE"] == frozenset({"TE"})

    def test_flex_takes_anything_bench_eligible(self, cfg):
        mapping = slot_positions(cfg)
        assert mapping["FLEX1"] == frozenset(cfg.bench_eligible_positions)

    def test_no_bench_position_can_cover_quarterback(self, cfg):
        """The consequence of excluding QB from bench eligibility: an injured
        starting QB simply costs you the slot."""
        mapping = slot_positions(cfg)
        assert mapping["QB"] == frozenset({"QB"})
        assert not mapping["QB"] & frozenset(cfg.bench_eligible_positions)


class TestShape:
    def test_every_team_gets_a_full_distribution(self, result, league):
        for team in league.teams:
            counts = result.finishes[team]
            assert len(counts) == len(league.teams)
            assert counts.sum() == result.trials

    def test_each_place_is_taken_once_per_trial(self, result, league):
        stacked = np.array([result.finishes[t] for t in league.teams])
        assert (stacked.sum(axis=0) == result.trials).all()

    def test_probabilities_are_probabilities(self, result, league):
        for team in league.teams:
            assert 0.0 <= result.p_first(team) <= 1.0
            assert result.p_first(team) <= result.p_top(team, 4)
            assert 1 <= result.expected_finish(team) <= len(league.teams)

    def test_probabilities_sum_to_one_across_teams(self, result, league):
        assert sum(result.p_first(t) for t in league.teams) == pytest.approx(1.0)
        assert sum(result.p_last(t) for t in league.teams) == pytest.approx(1.0)

    def test_zero_trials_is_an_error(self, league):
        with pytest.raises(SimulationError, match="at least one trial"):
            simulate.run(league, trials=0)


class TestReproducibility:
    def test_same_seed_same_answer(self, league):
        a = simulate.run(league, trials=QUICK, seed=5)
        b = simulate.run(league, trials=QUICK, seed=5)
        for team in league.teams:
            assert (a.finishes[team] == b.finishes[team]).all()
        assert a.bench_weight == pytest.approx(b.bench_weight)

    def test_different_seeds_differ(self, league):
        a = simulate.run(league, trials=QUICK, seed=5)
        b = simulate.run(league, trials=QUICK, seed=6)
        assert any((a.finishes[t] != b.finishes[t]).any() for t in league.teams)


class TestUncertaintyActuallyDoesSomething:
    def test_the_deterministic_winner_is_not_a_lock(self, result, league):
        """If the best team won 100% of trials the model would be claiming
        certainty it doesn't have."""
        best = result.ordered()[0]
        assert result.p_first(best) < 1.0

    def test_nobody_is_completely_written_off(self, result, league):
        # Every team should at least sometimes avoid finishing last.
        for team in league.teams:
            assert result.p_last(team) < 1.0

    def test_a_meaningful_share_of_matchups_are_coin_flips(self, league):
        """Section 2.1's premise, measured rather than quoted.

        If this came out near zero the whole probabilistic layer would be
        measuring nothing, and the deterministic report could be trusted as-is.
        """
        fraction = coin_flip_fraction(league)
        assert 0.15 < fraction < 0.6

    def test_expected_finish_broadly_tracks_the_deterministic_order(self, result, league):
        from fpr.core.rankings import run as run_rr

        standings = run_rr(league.lineups, league.cfg)
        deterministic_best = standings.results[0].team
        assert result.ordered()[0] == deterministic_best


class TestBenchWeight:
    def test_it_lands_near_a_tenth(self, result):
        """The spec's headline finding: an intuition-based 0.35 was roughly
        three times too generous. Wide bounds here -- the claim is the order of
        magnitude, not the third decimal."""
        assert 0.04 < result.bench_weight < 0.20

    def test_the_interval_brackets_the_estimate(self, result):
        low, high = result.bench_weight_ci
        assert low < result.bench_weight < high

    def test_it_is_far_below_a_naive_guess(self, result):
        assert result.bench_weight < 0.35 / 2

    def test_configured_weight_is_within_tolerance(self, result):
        # If this ever fails, league.yaml needs updating -- which is exactly
        # what the flag in the report is for.
        assert not result.bench_weight_disagrees

    def test_drift_is_flagged(self, league):
        result = simulate.run(league, trials=QUICK, seed=3)
        object.__setattr__(result, "configured_bench_weight", 0.35)
        assert result.bench_weight_disagrees

    def test_a_healthier_league_leans_on_the_bench_less(self, league, cfg):
        """Bench value exists only because starters miss games, so making
        everyone more available must shrink it."""
        healthy = simulate.run(league, trials=QUICK, seed=9, flat_availability=True)

        fragile_cfg = _with_base_rates(cfg, 0.5)
        fragile_league = _relaxed(league, fragile_cfg)
        fragile = simulate.run(fragile_league, trials=QUICK, seed=9)

        assert fragile.bench_weight > healthy.bench_weight


class TestPerPlayerAvailabilityMatters:
    def test_switching_to_a_flat_rate_moves_the_standings(self, league):
        """Section 2.4 is explicit: if nothing moves, something is broken.

        A flat rate can't tell a running-back-heavy roster from a
        quarterback-heavy one, so the two models have to disagree somewhere.
        """
        per_player = simulate.run(league, trials=STEADY, seed=11)
        flat = simulate.run(league, trials=STEADY, seed=11, flat_availability=True)

        moved = [
            t
            for t in league.teams
            if per_player.ordered().index(t) != flat.ordered().index(t)
        ]
        assert moved, "per-player availability changed nobody's ranking"

    def test_injuries_hit_the_team_that_has_them(self, league, config_path, csv_path,
                                                 rosters_path):
        """A team with several players on IR should drop relative to itself."""
        healthy = simulate.run(league, trials=STEADY, seed=13)

        victim = healthy.ordered()[3]
        hurt_players = league.rosters[victim].rb + league.rosters[victim].wr
        injured = pipeline.build(
            config_path=config_path,
            rankings_path=csv_path,
            rosters_path=rosters_path,
            injury_status={name: "INJURY_RESERVE" for name in hurt_players},
        )
        after = simulate.run(injured, trials=STEADY, seed=13)

        assert after.expected_finish(victim) > healthy.expected_finish(victim)

    def test_a_questionable_tag_costs_less_than_injured_reserve(self, league, config_path,
                                                               csv_path, rosters_path):
        victim = league.teams[0]
        players = league.rosters[victim].rb + league.rosters[victim].wr

        def finish(status):
            built = pipeline.build(
                config_path=config_path,
                rankings_path=csv_path,
                rosters_path=rosters_path,
                injury_status={name: status for name in players},
            )
            return simulate.run(built, trials=STEADY, seed=17).expected_finish(victim)

        assert finish("QUESTIONABLE") < finish("INJURY_RESERVE")


class TestSeasonLength:
    def test_weeks_are_configurable(self, league):
        short = simulate.run(league, trials=QUICK, weeks=2, seed=21)
        assert short.weeks == 2

    def test_a_longer_season_narrows_the_bench_weight_interval(self, league):
        """More weeks is more samples of the same process, so the per-trial
        estimate should steady down."""
        short = simulate.run(league, trials=STEADY, weeks=2, seed=23)
        long = simulate.run(league, trials=STEADY, weeks=20, seed=23)

        def width(res):
            low, high = res.bench_weight_ci
            return high - low

        assert width(long) < width(short)


def _with_base_rates(cfg, rate):
    from dataclasses import replace

    availability_cfg = replace(
        cfg.availability,
        position_base_rate={pos: rate for pos in cfg.availability.position_base_rate},
    )
    return replace(cfg, availability=availability_cfg)


def _relaxed(league, cfg):
    from dataclasses import replace

    return replace(league, cfg=cfg)
