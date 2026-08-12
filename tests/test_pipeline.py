import pytest

from fpr import pipeline
from fpr.adapters.rosters import RosterFileError, load
from fpr.core.lineup import Roster


class TestRosterFile:
    def test_example_file_loads(self, example_rosters):
        assert len(example_rosters) == 12
        assert all(isinstance(r, Roster) for r in example_rosters.values())

    def test_every_player_is_on_exactly_one_roster(self, example_rosters, real_table):
        drafted = [n for roster in example_rosters.values() for n in roster.all_players()]
        assert len(drafted) == len(set(drafted))
        assert len(drafted) == len(real_table)

    def test_a_player_on_two_rosters_is_rejected(self, tmp_path):
        path = tmp_path / "rosters.yaml"
        path.write_text(
            "teams:\n"
            "  A:\n    qb: [Josh Allen]\n    rb: [Jahmyr Gibbs]\n"
            "  B:\n    qb: [Joe Burrow]\n    rb: [Jahmyr Gibbs]\n",
            encoding="utf-8",
        )
        with pytest.raises(RosterFileError, match="Jahmyr Gibbs is on both"):
            load(path)

    def test_duplicate_detection_normalizes(self, tmp_path):
        # Same player, two spellings, two teams. Still a duplicate.
        path = tmp_path / "rosters.yaml"
        path.write_text(
            "teams:\n"
            "  A:\n    rb: [Marvin Harrison Jr.]\n"
            "  B:\n    rb: [Marvin Harrison]\n",
            encoding="utf-8",
        )
        with pytest.raises(RosterFileError, match="is on both"):
            load(path)

    def test_missing_file_says_what_to_do(self, tmp_path):
        with pytest.raises(RosterFileError, match="Copy config/rosters.example.yaml"):
            load(tmp_path / "nope.yaml")

    def test_file_without_teams_key(self, tmp_path):
        path = tmp_path / "rosters.yaml"
        path.write_text("something: else\n", encoding="utf-8")
        with pytest.raises(RosterFileError, match="top-level 'teams:'"):
            load(path)

    def test_unknown_group_names_the_team(self, tmp_path):
        path = tmp_path / "rosters.yaml"
        path.write_text("teams:\n  A:\n    kicker: [Somebody]\n", encoding="utf-8")
        with pytest.raises(RosterFileError, match="A: unknown roster groups"):
            load(path)


class TestBuild:
    def test_it_produces_everything_downstream_needs(self, league):
        assert len(league.teams) == 12
        assert len(league.table) == 165
        assert len(league.lineups) == 12
        assert league.optimal is False

    def test_every_lineup_is_complete(self, league):
        for lineup in league.lineups.values():
            assert set(lineup.players) == set(league.cfg.slots)

    def test_optimal_mode(self, optimal_league):
        assert optimal_league.optimal is True
        assert len(optimal_league.lineups) == 12

    def test_optimal_never_scores_a_worse_starting_lineup(self, league, optimal_league):
        optimal = optimal_league
        starters = league.cfg.starter_slots
        for team in league.teams:
            as_set = sum(league.lineups[team].value_at(s) for s in starters)
            best = sum(optimal.lineups[team].value_at(s) for s in starters)
            assert best >= as_set - 1e-9

    def test_supplied_rosters_bypass_the_file(
        self, example_rosters, config_path, csv_path
    ):
        """The path a platform adapter will take -- same shape, no YAML."""
        built = pipeline.build(
            config_path=config_path,
            rankings_path=csv_path,
            rosters_path="does/not/exist.yaml",
            rosters=example_rosters,
        )
        assert len(built.teams) == 12

    def test_consensus_is_derived_not_read(self, league):
        """Nothing in the input file is a consensus rank, so every one of these
        had to be computed."""
        gibbs = league.table["Jahmyr Gibbs"]
        assert gibbs.rank == pytest.approx(1.75)
        assert gibbs.source_count == 4
