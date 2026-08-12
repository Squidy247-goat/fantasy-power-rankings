import dataclasses

import pytest

from fpr.adapters.raw_csv import RawPlayer
from fpr.core import consensus
from fpr.core.consensus import ConsensusError, build, reindex


def make(name, position="RB", **ranks):
    return RawPlayer(name=name, position=position, ranks=dict(ranks))


class TestReindex:
    def test_dense_renumbering(self):
        # A source whose list is padded with kickers leaves gaps. Only the
        # ordering survives; the published numbers don't.
        assert reindex({"a": 3.0, "b": 12.0, "c": 40.0}) == {"a": 1, "b": 2, "c": 3}

    def test_already_dense_is_unchanged(self):
        assert reindex({"a": 1.0, "b": 2.0, "c": 3.0}) == {"a": 1, "b": 2, "c": 3}

    def test_ties_break_deterministically(self):
        first = reindex({"b": 5.0, "a": 5.0})
        second = reindex({"a": 5.0, "b": 5.0})
        assert first == second

    def test_empty(self):
        assert reindex({}) == {}


class TestReindexingMatters:
    def test_padding_does_not_bias_the_average(self, cfg):
        """The reason re-indexing exists.

        Two sources rank three players in the same order. One pads its list
        with kickers and defenses so its numbers run much higher. The consensus
        must be identical either way -- if it isn't, whichever source pads
        least is quietly dominating the average.
        """
        unpadded = [
            make("Player One", **{"tight": 1.0, "padded": 2.0}),
            make("Player Two", **{"tight": 2.0, "padded": 9.0}),
            make("Player Three", **{"tight": 3.0, "padded": 31.0}),
        ]
        table = build(unpadded, cfg)
        assert [p.name for p in table.ordered()] == ["Player One", "Player Two", "Player Three"]
        # Both sources agree on order, so every player sits at his dense rank
        # exactly and the sources contribute equally.
        assert [p.rank for p in table.ordered()] == [1.0, 2.0, 3.0]


class TestConsensusAveraging:
    def test_mean_of_listing_sources(self, cfg):
        players = [
            make("Alpha", a=1.0, b=1.0),
            make("Bravo", a=2.0, b=3.0),
            make("Charlie", a=3.0, b=2.0),
        ]
        table = build(players, cfg)
        assert table["Bravo"].rank == pytest.approx(2.5)
        assert table["Charlie"].rank == pytest.approx(2.5)

    def test_missing_source_is_skipped_not_penalized(self, cfg):
        """A source with no opinion must not count as ranking him last.

        Bravo is 2nd in both sources that list him. He must land at 2.0, not
        get dragged down by the source that never mentioned him.
        """
        players = [
            make("Alpha", a=1.0, b=1.0, c=1.0),
            make("Bravo", a=2.0, b=2.0),  # source c has no opinion
            make("Charlie", a=3.0, b=3.0, c=2.0),
        ]
        table = build(players, cfg)
        assert table["Bravo"].rank == pytest.approx(2.0)
        assert table["Bravo"].source_count == 2

    def test_source_ranks_are_dense_not_raw(self, cfg):
        players = [
            make("Alpha", a=7.0),
            make("Bravo", a=88.0),
        ]
        table = build(players, cfg)
        assert table["Alpha"].source_ranks == {"a": 1.0}
        assert table["Bravo"].source_ranks == {"a": 2.0}


class TestUnrankedPlayers:
    def test_unranked_gets_replacement_level(self, cfg):
        players = [
            make("Alpha", a=1.0),
            make("Bravo", a=2.0),
            make("Nobody"),  # listed by no source
        ]
        table = build(players, cfg)
        deepest = max(p.rank for p in table if p.ranked)
        assert table["Nobody"].rank == pytest.approx(deepest + cfg.consensus.unranked_offset)
        assert table["Nobody"].ranked is False

    def test_unranked_is_never_dropped(self, cfg):
        """The failure this prevents is a KeyError deep in the pipeline."""
        players = [make("Alpha", a=1.0), make("Nobody")]
        table = build(players, cfg)
        assert len(table) == 2
        assert table["Nobody"] is not None

    def test_unranked_sorts_last(self, cfg):
        players = [make("Alpha", a=1.0), make("Nobody"), make("Bravo", a=2.0)]
        table = build(players, cfg)
        assert table.ordered()[-1].name == "Nobody"

    def test_all_unranked_is_an_error(self, cfg):
        with pytest.raises(ConsensusError, match="no player was listed"):
            build([make("Nobody"), make("Alsonobody")], cfg)


class TestSpread:
    def test_agreement_still_gets_the_floor(self, cfg):
        """Four sources agreeing exactly doesn't make a rank certain, it just
        means four people read the same depth chart."""
        players = [
            make("Alpha", a=1.0, b=1.0, c=1.0, d=1.0),
            make("Bravo", a=2.0, b=2.0, c=2.0, d=2.0),
        ]
        table = build(players, cfg)
        assert table["Alpha"].spread == pytest.approx(cfg.simulation.min_spread)

    def test_disagreement_widens_the_spread(self, cfg):
        players = [make(f"Filler{i}", a=float(i), b=float(i)) for i in range(1, 40)]
        players.append(make("Contested", a=1.0, b=39.0))
        table = build(players, cfg)
        assert table["Contested"].spread > cfg.simulation.min_spread

    def test_single_source_is_wide_not_narrow(self, cfg):
        """One unchallenged opinion deserves less confidence than a contested
        average, not more."""
        players = [make("Alpha", a=1.0), make("Lonely", b=1.0)]
        table = build(players, cfg)
        assert table["Lonely"].spread == pytest.approx(cfg.simulation.single_source_spread)
        assert table["Lonely"].spread > cfg.simulation.min_spread


class TestLookup:
    def test_lookup_normalizes(self, cfg):
        table = build([make("Marvin Harrison", position="WR", a=1.0)], cfg)
        assert table["Marvin Harrison Jr."].name == "Marvin Harrison"
        assert "MARVIN HARRISON" in table

    def test_missing_player_message_is_actionable(self, cfg):
        table = build([make("Alpha", a=1.0)], cfg)
        with pytest.raises(KeyError, match="needs a row in the rankings input"):
            table["Nonexistent Guy"]

    def test_get_returns_default(self, cfg):
        table = build([make("Alpha", a=1.0)], cfg)
        assert table.get("Nonexistent Guy") is None


class TestFiltering:
    def test_kickers_and_defenses_are_excluded(self, cfg):
        players = [
            make("Alpha", position="RB", a=1.0),
            make("Some Kicker", position="K", a=2.0),
            make("Some Defense", position="D/ST", a=3.0),
            make("Bravo", position="WR", a=4.0),
        ]
        table = build(players, cfg)
        assert len(table) == 2
        assert table["Bravo"].rank == pytest.approx(2.0)  # renumbered, not 4th

    def test_no_skill_players_is_an_error(self, cfg):
        with pytest.raises(ConsensusError, match="no players at skill positions"):
            build([make("Some Kicker", position="K", a=1.0)], cfg)

    def test_colliding_names_are_rejected_loudly(self, cfg):
        with pytest.raises(ConsensusError, match="normalize to the same key"):
            build([make("Brian Robinson", a=1.0), make("Brian Robinson Jr.", a=2.0)], cfg)


@pytest.fixture
def table(real_table):
    return real_table


class TestAgainstRealData:
    """Section 7's sanity check, as a test rather than an eyeball."""

    def test_every_rostered_player_survives(self, table, raw_players):
        # Nothing may be dropped between the file and the table.
        assert len(table) == len(raw_players)

    def test_the_rb_leading_most_columns_lands_at_the_top(self, table, raw_players):
        """Gibbs is 1st in two of the four source columns and 2nd/3rd in the
        others. If he isn't at or near the top of the computed consensus,
        something in re-indexing or averaging is wrong and nothing built on top
        of this is worth looking at."""
        leaders = _column_leaders(raw_players, position="RB")
        assert leaders[0] == "Jahmyr Gibbs"

        top_five = [p.name for p in table.ordered()[:5]]
        assert "Jahmyr Gibbs" in top_five

    def test_top_of_the_board_is_who_you_would_expect(self, table):
        # Every one of these is top-10 in all four columns by inspection.
        top_five = {p.name for p in table.ordered()[:5]}
        assert top_five <= {
            "Jahmyr Gibbs",
            "Ja'Marr Chase",
            "Bijan Robinson",
            "Puka Nacua",
            "Jaxon Smith-Njigba",
            "Christian McCaffrey",
            "Jonathan Taylor",
        }

    def test_dense_ranks_never_exceed_the_player_count(self, table):
        # Raw ranks in the CSV run to 287. After re-indexing nothing may sit
        # above the number of players, or the filter/renumber step didn't run.
        for player in table:
            for rank in player.source_ranks.values():
                assert 1 <= rank <= len(table)

    def test_consensus_is_bounded_by_the_dense_range(self, table):
        ranked = [p for p in table if p.ranked]
        assert min(p.rank for p in ranked) >= 1.0
        assert max(p.rank for p in ranked) <= len(table)

    def test_players_missing_from_some_sources_are_still_ranked(self, table):
        # Adonai Mitchell is listed by three of four sources.
        mitchell = table["Adonai Mitchell"]
        assert mitchell.source_count == 3
        assert mitchell.ranked

    def test_spreads_are_all_positive(self, table):
        assert all(p.spread > 0 for p in table)

    def test_most_contested_players_have_wide_spreads(self, table):
        """Section 2.1's premise: some players genuinely split the sources.

        If the widest spread in the league were near the floor, the whole
        Monte Carlo layer would be measuring nothing.
        """
        widest = max(p.spread for p in table if p.source_count >= 2)
        assert widest > 2 * cfg_min_spread(table)


def cfg_min_spread(table) -> float:
    """The narrowest spread present, which is the configured floor."""
    return min(p.spread for p in table)


def _column_leaders(raw_players, position: str) -> list[str]:
    """Players ordered by how many source columns they lead, at one position."""
    sources = set()
    for player in raw_players:
        sources.update(player.ranks)

    wins: dict[str, int] = {}
    for source in sources:
        listed = [p for p in raw_players if source in p.ranks and p.position == position]
        if not listed:
            continue
        best = min(listed, key=lambda p: p.ranks[source])
        wins[best.name] = wins.get(best.name, 0) + 1

    return sorted(wins, key=lambda name: -wins[name])


def test_player_consensus_is_immutable():
    from fpr.core.consensus import PlayerConsensus

    player = PlayerConsensus(
        name="A", key="a", position="RB", source_ranks={}, rank=1.0, spread=6.0, ranked=True
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        player.rank = 2.0


def test_module_exposes_expected_surface():
    assert hasattr(consensus, "build")
    assert hasattr(consensus, "ConsensusTable")
