"""Snapshot tests.

These matter more than they look. A snapshot that's missing a field can't be
backfilled -- once a day has passed, whatever wasn't recorded is gone. So the
tests are mostly about completeness rather than formatting.
"""

import datetime
import json

import pytest

from fpr.core import simulate
from fpr.report import json as json_report


@pytest.fixture(scope="module")
def result(league):
    return simulate.run(league, trials=80, seed=55)


@pytest.fixture(scope="module")
def snapshot(league, result):
    return json_report.build(league, result)


class TestCompleteness:
    def test_the_three_things_section_5_asks_for(self, snapshot):
        # Standings, simulation probabilities, and the consensus used that day.
        assert snapshot["standings"]
        assert snapshot["simulation"]
        assert snapshot["consensus"]

    def test_consensus_covers_every_player(self, snapshot, league):
        assert len(snapshot["consensus"]) == len(league.table)

    def test_consensus_records_spread_and_source_count(self, snapshot):
        """Needed to tell later whether a team moved because its roster changed
        or because the sources changed their minds."""
        entry = snapshot["consensus"][0]
        assert {"player", "position", "rank", "spread", "sources", "ranked"} <= set(entry)

    def test_standings_cover_every_team(self, snapshot, league):
        assert len(snapshot["standings"]) == len(league.teams)

    def test_standings_include_the_lineup_that_produced_them(self, snapshot, league):
        lineup = snapshot["standings"][0]["lineup"]
        assert len(lineup) == len(league.cfg.slots)
        assert {"slot", "player", "position", "consensus_rank", "value"} <= set(lineup[0])

    def test_positional_strength_is_recorded(self, snapshot, league):
        strength = snapshot["standings"][0]["positional_strength"]
        assert set(strength) == set(league.cfg.slots)

    def test_the_config_that_produced_it_is_recorded(self, snapshot):
        """Curve constants and bench weight are tunable, so a snapshot without
        them can't be compared against one taken after a tuning change."""
        assert snapshot["config"]["value_curve"]["decay"] == 51.0
        assert snapshot["config"]["bench_weight"] == 0.11

    def test_full_finish_distribution_not_just_summaries(self, snapshot, result):
        """Recording only P(1st) and P(top 4) would lock a future calibration
        pass into whichever summaries seemed interesting today."""
        counts = snapshot["simulation"]["teams"][0]["finish_counts"]
        assert len(counts) == len(result.teams)
        assert sum(counts) == result.trials

    def test_bench_weight_with_its_interval(self, snapshot):
        bench = snapshot["simulation"]["bench_weight"]
        assert bench["ci_low"] < bench["measured"] < bench["ci_high"]
        assert "configured" in bench
        assert "disagrees" in bench

    def test_coin_flip_fraction_is_recorded(self, snapshot):
        assert 0.0 < snapshot["coin_flip_fraction"] < 1.0

    def test_schema_is_versioned(self, snapshot):
        """The shape will change. A version makes old files readable anyway."""
        assert snapshot["schema_version"] == 1


class TestMetadata:
    def test_date_defaults_to_today(self, snapshot):
        assert snapshot["date"] == datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    def test_date_can_be_supplied(self, league):
        when = datetime.date(2026, 1, 15)
        assert json_report.build(league, None, when)["date"] == "2026-01-15"

    def test_it_records_where_the_rosters_came_from(self, snapshot):
        assert snapshot["source"] == "from the roster file"

    def test_it_records_the_lineup_mode(self, snapshot, optimal_league):
        assert snapshot["lineups"] == "as_set"
        assert json_report.build(optimal_league)["lineups"] == "optimal"


class TestSerialization:
    def test_it_is_valid_json(self, league, result):
        parsed = json.loads(json_report.render(league, result))
        assert parsed["matchups"] == 660

    def test_numpy_integers_survive(self, league, result):
        """finish_counts comes out of numpy, and json can't serialize those
        without help."""
        text = json_report.render(league, result)
        counts = json.loads(text)["simulation"]["teams"][0]["finish_counts"]
        assert all(isinstance(n, int) for n in counts)

    def test_it_works_without_a_simulation(self, league):
        parsed = json.loads(json_report.render(league))
        assert "simulation" not in parsed
        assert parsed["standings"]

    def test_it_ends_with_a_newline(self, league):
        assert json_report.render(league).endswith("\n")


class TestCli:
    def test_snapshot_flag_writes_a_file(self, tmp_path, config_path, csv_path, rosters_path):
        from fpr import cli

        out = tmp_path / "history" / "2026-01-01.json"
        code = cli.main(
            [
                "simulate",
                "--config", str(config_path),
                "--rankings", str(csv_path),
                "--rosters", str(rosters_path),
                "--trials", "20",
                "--seed", "1",
                "--snapshot", str(out),
                "-o", str(tmp_path / "report.md"),
            ]
        )
        assert code == 0
        assert json.loads(out.read_text())["standings"]

    def test_nothing_is_written_when_the_run_fails(self, tmp_path, config_path, csv_path):
        """A snapshot with no report beside it would be a lie about what ran."""
        from fpr import cli

        out = tmp_path / "history" / "nope.json"
        code = cli.main(
            [
                "simulate",
                "--config", str(config_path),
                "--rankings", str(csv_path),
                "--rosters", str(tmp_path / "missing.yaml"),
                "--snapshot", str(out),
            ]
        )
        assert code == 1
        assert not out.exists()
