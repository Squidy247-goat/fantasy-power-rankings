"""FantasyPros API loader tests.

All against a recorded payload. There's no live call here and there shouldn't
be -- CI has no API key, and a test that needs one fails for reasons unrelated
to the code being tested.
"""

import json

import pytest

from fpr.adapters import fantasypros, sources
from fpr.adapters.fantasypros import FantasyProsError, load, parse, url
from fpr.adapters.raw_csv import RawPlayer

PAYLOAD = {
    "sport": "NFL",
    "year": 2026,
    "week": 0,
    "position_id": "ALL",
    "total_experts": 137,
    "count": 6,
    "players": [
        {
            "player_id": 1,
            "player_name": "Jahmyr Gibbs",
            "player_position_id": "RB",
            "rank_ecr": 1,
            "rank_min": 1,
            "rank_max": 4,
            "rank_ave": 1.8,
        },
        {
            "player_id": 2,
            "player_name": "Ja'Marr Chase",
            "player_position_id": "WR",
            "rank_ecr": 2,
        },
        {
            "player_id": 3,
            "player_name": "Josh Allen",
            "player_position_id": "QB",
            "rank_ecr": 25,
        },
        {
            "player_id": 4,
            "player_name": "Trey McBride",
            "player_position_id": "TE",
            "rank_ecr": 15,
        },
        # Never modeled, must be filtered out.
        {"player_id": 5, "player_name": "Some Kicker", "player_position_id": "K", "rank_ecr": 200},
        {
            "player_id": 6,
            "player_name": "Ravens D/ST",
            "player_position_id": "DST",
            "rank_ecr": 180,
        },
    ],
}


class TestParsing:
    def test_it_reads_name_position_and_ecr(self):
        players = parse(PAYLOAD)
        gibbs = next(p for p in players if p.name == "Jahmyr Gibbs")
        assert gibbs.position == "RB"
        assert gibbs.ranks == {"FantasyPros ECR": 1.0}

    def test_kickers_and_defenses_are_dropped(self):
        names = {p.name for p in parse(PAYLOAD)}
        assert "Some Kicker" not in names
        assert "Ravens D/ST" not in names
        assert len(names) == 4

    def test_the_column_name_matches_the_csv_header(self):
        """So a run with the API on and a run off the committed file produce
        the same column rather than two half-populated ones."""
        assert fantasypros.SOURCE_NAME == "FantasyPros ECR"

    def test_expert_spread_fields_are_deliberately_ignored(self):
        """rank_min/max describe disagreement *within* FantasyPros' panel.

        The spread this project models is disagreement *between* sources.
        Folding one source's internal spread in would double-count its
        uncertainty while leaving the other three sources' unmeasured.
        """
        gibbs = next(p for p in parse(PAYLOAD) if p.name == "Jahmyr Gibbs")
        assert gibbs.ranks == {"FantasyPros ECR": 1.0}

    def test_players_without_a_rank_are_skipped(self):
        payload = {"players": [{"player_name": "A", "player_position_id": "RB", "rank_ecr": None}]}
        with pytest.raises(FantasyProsError, match="none at QB/RB/WR/TE"):
            parse(payload)

    def test_non_numeric_rank_is_loud(self):
        payload = {
            "players": [{"player_name": "A", "player_position_id": "RB", "rank_ecr": "abc"}]
        }
        with pytest.raises(FantasyProsError, match="non-numeric rank_ecr"):
            parse(payload)

    def test_empty_payload(self):
        with pytest.raises(FantasyProsError, match="returned no players"):
            parse({"players": []})

    def test_wrong_type(self):
        with pytest.raises(FantasyProsError, match="expected a JSON object"):
            parse([1, 2, 3])

    def test_url_shape(self):
        assert url(2026) == "https://api.fantasypros.com/public/v2/json/nfl/2026/consensus-rankings"


class TestFetching:
    def test_it_sends_the_key_and_caches_the_payload(self, tmp_path):
        seen = {}

        def fake_fetch(endpoint, api_key, params):
            seen["endpoint"] = endpoint
            seen["api_key"] = api_key
            seen["params"] = params
            return PAYLOAD

        cache = tmp_path / "fp.json"
        players, warnings = load("secret-key", 2026, cache=cache, fetch=fake_fetch)

        assert seen["api_key"] == "secret-key"
        assert seen["params"]["position"] == "ALL"
        assert warnings == []
        assert len(players) == 4
        assert json.loads(cache.read_text())["year"] == 2026

    def test_optional_parameters(self, tmp_path):
        seen = {}

        def fake_fetch(endpoint, api_key, params):
            seen.update(params)
            return PAYLOAD

        load("k", 2026, scoring="PPR", week=3, cache=tmp_path / "c.json", fetch=fake_fetch)
        assert seen["scoring"] == "PPR"
        assert seen["week"] == 3


class TestGracefulFailure:
    """Section 4.4. A stale column with a warning is recoverable; a silently
    missing one is not."""

    def _cache(self, tmp_path):
        path = tmp_path / "fp.json"
        path.write_text(json.dumps(PAYLOAD), encoding="utf-8")
        return path

    def test_it_falls_back_to_the_cached_copy(self, tmp_path):
        def broken(endpoint, api_key, params):
            raise RuntimeError("502 Bad Gateway")

        players, warnings = load("k", 2026, cache=self._cache(tmp_path), fetch=broken)
        assert len(players) == 4
        assert warnings

    def test_the_fallback_is_loud(self, tmp_path):
        def broken(endpoint, api_key, params):
            raise RuntimeError("502 Bad Gateway")

        _, warnings = load("k", 2026, cache=self._cache(tmp_path), fetch=broken)
        assert "stale" in warnings[0].lower()
        assert "502" in warnings[0]

    def test_a_malformed_response_also_falls_back(self, tmp_path):
        """Not just network errors. An empty or half-parsed table is exactly
        the garbage the fallback exists to keep out of the average."""
        players, warnings = load(
            "k", 2026, cache=self._cache(tmp_path), fetch=lambda *a: {"players": []}
        )
        assert len(players) == 4
        assert warnings

    def test_no_cache_and_a_failure_raises(self, tmp_path):
        """With nothing to fall back on, failing loudly beats dropping a source
        and still reporting four."""

        def broken(endpoint, api_key, params):
            raise RuntimeError("down")

        with pytest.raises(FantasyProsError, match="no cached copy"):
            load("k", 2026, cache=tmp_path / "missing.json", fetch=broken)

    def test_a_corrupt_cache_is_not_trusted(self, tmp_path):
        cache = tmp_path / "fp.json"
        cache.write_text("{not json", encoding="utf-8")

        def broken(endpoint, api_key, params):
            raise RuntimeError("down")

        with pytest.raises(FantasyProsError, match="no cached copy"):
            load("k", 2026, cache=cache, fetch=broken)


class TestMerging:
    def csv_rows(self):
        return [
            RawPlayer("James Cook III", "RB", {"CBS Consensus": 11.0, "FantasyPros ECR": 17.0}),
            RawPlayer("Jahmyr Gibbs", "RB", {"CBS Consensus": 1.0}),
            RawPlayer("Only In CSV", "WR", {"CBS Consensus": 50.0}),
        ]

    def test_columns_union_rather_than_overwrite(self):
        api = [RawPlayer("Jahmyr Gibbs", "RB", {"FantasyPros ECR": 2.0})]
        merged = sources.combine(self.csv_rows(), api)
        gibbs = next(p for p in merged.players if p.name == "Jahmyr Gibbs")
        assert gibbs.ranks == {"CBS Consensus": 1.0, "FantasyPros ECR": 2.0}

    def test_merging_is_by_normalized_name(self):
        """The CSV spells him with a suffix and the API doesn't. One row, not
        two half-ranked players."""
        api = [RawPlayer("James Cook", "RB", {"Expert composite": 9.0})]
        merged = sources.combine(self.csv_rows(), api)
        assert len([p for p in merged.players if "Cook" in p.name]) == 1
        cook = next(p for p in merged.players if "Cook" in p.name)
        assert set(cook.ranks) == {"CBS Consensus", "FantasyPros ECR", "Expert composite"}

    def test_the_first_group_owns_the_display_name(self):
        api = [RawPlayer("James Cook", "RB", {"Expert composite": 9.0})]
        merged = sources.combine(self.csv_rows(), api)
        assert any(p.name == "James Cook III" for p in merged.players)

    def test_a_clash_on_the_same_column_keeps_the_first_and_warns(self):
        api = [RawPlayer("James Cook", "RB", {"FantasyPros ECR": 99.0})]
        merged = sources.combine(self.csv_rows(), api)
        cook = next(p for p in merged.players if "Cook" in p.name)
        assert cook.ranks["FantasyPros ECR"] == 17.0
        assert any("two values" in w for w in merged.warnings)

    def test_a_position_disagreement_warns(self):
        api = [RawPlayer("Jahmyr Gibbs", "WR", {"FantasyPros ECR": 2.0})]
        merged = sources.combine(self.csv_rows(), api)
        assert any("RB" in w and "WR" in w for w in merged.warnings)

    def test_players_only_in_one_source_survive(self):
        api = [RawPlayer("Only In API", "TE", {"FantasyPros ECR": 30.0})]
        merged = sources.combine(self.csv_rows(), api)
        names = {p.name for p in merged.players}
        assert "Only In CSV" in names
        assert "Only In API" in names

    def test_combining_nothing_is_fine(self):
        assert sources.combine().players == []

    def test_merged_output_feeds_consensus_unchanged(self, cfg):
        """The point of the whole exercise: consensus never learns that one of
        these columns arrived over HTTP."""
        from fpr.core.consensus import build

        api = parse(PAYLOAD)
        merged = sources.combine(self.csv_rows(), api)
        table = build(merged.players, cfg)
        assert table["Jahmyr Gibbs"].source_count == 2


class TestPipelineIntegration:
    def test_missing_api_key_says_what_to_do(self, monkeypatch, config_path, csv_path,
                                             rosters_path):
        from fpr import pipeline

        monkeypatch.delenv("FANTASYPROS_API_KEY", raising=False)
        with pytest.raises(FantasyProsError, match="secure.fantasypros.com"):
            pipeline.build(
                config_path=config_path,
                rankings_path=csv_path,
                rosters_path=rosters_path,
                refresh_rankings=True,
                env_path="does-not-exist.env",
            )

    def test_warnings_reach_the_report(self, league):
        from dataclasses import replace

        from fpr.report import markdown

        noisy = replace(league, warnings=("FantasyPros request failed. Column is stale.",))
        report = markdown.render(noisy)
        assert "Warnings" in report
        assert "stale" in report

    def test_a_clean_run_has_no_warning_banner(self, league):
        from fpr.report import markdown

        assert "**Warnings**" not in markdown.render(league)
