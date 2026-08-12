import pytest

from fpr import cli
from fpr.core.rankings import run
from fpr.report import markdown


@pytest.fixture(scope="module")
def report(league):
    return markdown.render(league)


class TestStructure:
    def test_all_three_sections_are_present(self, report):
        assert "## Standings" in report
        assert "## Positional strength" in report
        assert "## Lineups" in report

    def test_every_team_appears(self, report, league):
        for team in league.teams:
            assert team in report

    def test_matchup_count_is_stated(self, report):
        assert "660 slot matchups" in report

    def test_sources_are_named(self, report):
        assert "FantasyPros ECR" in report
        assert "CBS Consensus" in report

    def test_tables_are_well_formed(self, report):
        """Within each table every row has the header's column count.

        Checked per block rather than across the whole document, since the
        three tables legitimately have different widths.
        """
        blocks, current = [], []
        for line in report.splitlines():
            if line.startswith("|"):
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)

        assert len(blocks) >= 3
        for block in blocks:
            assert len(block) >= 3  # header, separator, at least one row
            widths = {line.count("|") for line in block}
            assert len(widths) == 1, f"ragged table: {block[0]}"

            separator = block[1].replace("|", "").replace(" ", "")
            assert set(separator) <= set(":-"), f"missing separator row: {block[0]}"


class TestStandingsSection:
    def test_teams_are_listed_in_score_order(self, league):
        standings = run(league.lineups, league.cfg)
        table = markdown.standings_table(standings, league.cfg.starter_slots)
        order = [line.split("|")[2].strip() for line in table.splitlines()[2:]]
        assert order == [r.team for r in standings]

    def test_the_bench_weight_caveat_is_explained(self, report):
        # Overall record and score can disagree; a reader shouldn't have to
        # guess why.
        assert "weights bench slots at" in report

    def test_starters_column_tracks_score(self, league):
        """Score is mostly the starter record, so the two should broadly agree."""
        standings = run(league.lineups, league.cfg)
        starter_wins = [
            sum(r.slots[s].wins for s in league.cfg.starter_slots) for r in standings
        ]
        # Not strictly monotone -- ties and bench wins perturb it -- but the
        # top team must beat the bottom team on starters.
        assert starter_wins[0] > starter_wins[-1]

    def test_point_differential_is_signed(self, report):
        assert "+" in report


class TestPositionalSection:
    def test_all_slots_are_columns(self, league, report):
        header = next(line for line in report.splitlines() if line.startswith("| Team |"))
        for slot in league.cfg.slots:
            assert f"| {slot} " in header


class TestLineupSection:
    def test_each_team_gets_a_heading(self, report, league):
        for team in league.teams:
            assert f"### {team}" in report

    def test_slot_records_are_present(self, report):
        assert "| Slot | Player | Pos | Consensus | Value | Record |" in report

    def test_a_known_player_shows_his_computed_consensus(self, report):
        # Gibbs at 1.75, computed from the raw columns rather than read anywhere.
        assert "Jahmyr Gibbs" in report
        assert "1.75" in report


class TestCli:
    def test_rank_writes_a_file(self, tmp_path, config_path, csv_path, rosters_path):
        out = tmp_path / "report.md"
        code = cli.main(
            [
                "rank",
                "--config",
                str(config_path),
                "--rankings",
                str(csv_path),
                "--rosters",
                str(rosters_path),
                "--out",
                str(out),
            ]
        )
        assert code == 0
        assert "## Standings" in out.read_text(encoding="utf-8")

    def test_rank_prints_to_stdout(self, capsys, config_path, csv_path, rosters_path):
        code = cli.main(
            ["rank", "--config", str(config_path), "--rankings", str(csv_path),
             "--rosters", str(rosters_path)]
        )
        assert code == 0
        assert "# Power rankings" in capsys.readouterr().out

    def test_optimal_flag(self, capsys, config_path, csv_path, rosters_path):
        code = cli.main(
            ["rank", "--config", str(config_path), "--rankings", str(csv_path),
             "--rosters", str(rosters_path), "--optimal"]
        )
        assert code == 0
        assert "optimal lineups" in capsys.readouterr().out

    def test_missing_roster_file_exits_cleanly(self, capsys, tmp_path, config_path, csv_path):
        """A predictable failure gets one line, not a traceback."""
        code = cli.main(
            ["rank", "--config", str(config_path), "--rankings", str(csv_path),
             "--rosters", str(tmp_path / "nope.yaml")]
        )
        assert code == 1
        err = capsys.readouterr().err
        assert err.startswith("fpr: ")
        assert "Traceback" not in err

    def test_missing_rankings_file_exits_cleanly(self, capsys, tmp_path, config_path, rosters_path):
        code = cli.main(
            ["rank", "--config", str(config_path), "--rankings", str(tmp_path / "nope.csv"),
             "--rosters", str(rosters_path)]
        )
        assert code == 1
        assert "rankings file not found" in capsys.readouterr().err
