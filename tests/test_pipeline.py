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
        # The rankings file covers more players than the league rosters; the
        # remainder are free agents.
        assert set(drafted) <= {p.name for p in real_table}

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
        assert len(league.table) > 150
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

    def test_a_platform_can_supply_rosters(self, example_rosters, config_path, csv_path):
        """Sanity check that the platform path and the file path converge."""
        built = pipeline.build(
            config_path=config_path,
            rankings_path=csv_path,
            rosters_path="does/not/exist.yaml",
            rosters=example_rosters,
            injury_status={"Jahmyr Gibbs": "QUESTIONABLE"},
        )
        assert built.injury_status["Jahmyr Gibbs"] == "QUESTIONABLE"

    def test_consensus_is_derived_not_read(self, league):
        """Nothing in the input file is a consensus rank, so every one of these
        had to be computed."""
        gibbs = league.table["Jahmyr Gibbs"]
        assert gibbs.rank == pytest.approx(1.75)
        assert gibbs.source_count == 4


class TestMissingPlayers:
    """Rosters drift from a rankings snapshot. Waiver claims and backup
    quarterbacks show up on a roster long before they show up on a list."""

    def _roster_with(self, example_rosters, *extra):
        from dataclasses import replace

        team, roster = next(iter(example_rosters.items()))
        return {team: replace(roster, bench=[*roster.bench, *extra])}

    def test_all_missing_players_are_reported_at_once(
        self, example_rosters, config_path, csv_path
    ):
        """One error listing everyone beats one error per player, since the
        fix is a single edit to the CSV either way."""
        rosters = self._roster_with(example_rosters, "Nobody Atall", "Alsonot Here")

        with pytest.raises(pipeline.MissingPlayers) as excinfo:
            pipeline.build(
                config_path=config_path,
                rankings_path=csv_path,
                rosters_path="unused.yaml",
                rosters=rosters,
            )

        message = str(excinfo.value)
        assert "Nobody Atall" in message
        assert "Alsonot Here" in message
        assert "2 rostered player(s)" in message

    def test_the_error_names_the_team(self, example_rosters, config_path, csv_path):
        rosters = self._roster_with(example_rosters, "Nobody At All")
        team = next(iter(rosters))
        with pytest.raises(pipeline.MissingPlayers, match=team):
            pipeline.build(
                config_path=config_path,
                rankings_path=csv_path,
                rosters_path="unused.yaml",
                rosters=rosters,
            )

    def test_it_says_how_to_fix_it(self, example_rosters, config_path, csv_path):
        rosters = self._roster_with(example_rosters, "Nobody At All")
        with pytest.raises(pipeline.MissingPlayers, match="Add a row for each"):
            pipeline.build(
                config_path=config_path,
                rankings_path=csv_path,
                rosters_path="unused.yaml",
                rosters=rosters,
            )

    def test_suffix_spellings_do_not_count_as_missing(
        self, example_rosters, config_path, csv_path
    ):
        """A different spelling of a ranked player isn't a missing player."""
        from dataclasses import replace

        team, roster = next(iter(example_rosters.items()))
        renamed = replace(roster, rb=[f"{roster.rb[0]} Jr.", roster.rb[1]])
        built = pipeline.build(
            config_path=config_path,
            rankings_path=csv_path,
            rosters_path="unused.yaml",
            rosters={team: renamed},
        )
        assert built.lineups[team]
