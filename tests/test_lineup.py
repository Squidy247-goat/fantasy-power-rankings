import pytest

from fpr.adapters.raw_csv import RawPlayer
from fpr.core.consensus import build as build_consensus
from fpr.core.lineup import LineupError, Roster, build, build_optimal
from fpr.core.names import same_player


def mini_table(cfg, players):
    """Consensus table from (name, position, rank) triples.

    Ranks are contiguous so dense re-indexing leaves them alone and the numbers
    in these tests mean what they look like they mean.
    """
    raw = [
        RawPlayer(name=name, position=pos, ranks={"only": float(rank)})
        for name, pos, rank in players
    ]
    return build_consensus(raw, cfg)


ROSTER = [
    ("Starter QB", "QB", 1),
    ("Better RB", "RB", 2),
    ("Worse RB", "RB", 30),
    ("Better WR", "WR", 3),
    ("Worse WR", "WR", 40),
    ("Starter TE", "TE", 5),
    ("Better Flex", "RB", 6),
    ("Worse Flex", "WR", 50),
    ("Good Bench RB", "RB", 20),
    ("Bad Bench WR", "WR", 90),
    ("Third Bench TE", "TE", 95),
    ("Backup QB", "QB", 25),
]


@pytest.fixture
def table(cfg):
    return mini_table(cfg, ROSTER)


@pytest.fixture
def roster():
    return Roster(
        qb=["Starter QB"],
        rb=["Better RB", "Worse RB"],
        wr=["Better WR", "Worse WR"],
        te=["Starter TE"],
        flex=["Better Flex", "Worse Flex"],
        bench=["Good Bench RB", "Bad Bench WR", "Third Bench TE", "Backup QB"],
    )


class TestOrderingWithinGroups:
    def test_rb1_is_the_better_back(self, roster, table, cfg):
        lineup = build("Team", roster, table, cfg)
        assert lineup["RB1"].name == "Better RB"
        assert lineup["RB2"].name == "Worse RB"

    def test_input_order_is_ignored(self, table, cfg):
        """The platform's display order is sometimes alphabetical, sometimes
        stale. Reversing the input must not change who is RB1."""
        forward = Roster(
            qb=["Starter QB"],
            rb=["Better RB", "Worse RB"],
            wr=["Better WR", "Worse WR"],
            te=["Starter TE"],
            flex=["Better Flex", "Worse Flex"],
            bench=["Good Bench RB", "Bad Bench WR"],
        )
        backward = Roster(
            qb=["Starter QB"],
            rb=["Worse RB", "Better RB"],
            wr=["Worse WR", "Better WR"],
            te=["Starter TE"],
            flex=["Worse Flex", "Better Flex"],
            bench=["Bad Bench WR", "Good Bench RB"],
        )
        a = build("Team", forward, table, cfg)
        b = build("Team", backward, table, cfg)
        assert {s: p.name for s, p in a.players.items()} == {
            s: p.name for s, p in b.players.items()
        }

    def test_wr_and_flex_order_too(self, roster, table, cfg):
        lineup = build("Team", roster, table, cfg)
        assert lineup["WR1"].name == "Better WR"
        assert lineup["FLEX1"].name == "Better Flex"

    def test_higher_slot_always_has_at_least_as_much_value(self, roster, table, cfg):
        lineup = build("Team", roster, table, cfg)
        for better, worse in [("RB1", "RB2"), ("WR1", "WR2"), ("FLEX1", "FLEX2"), ("BN1", "BN2")]:
            assert lineup.value_at(better) >= lineup.value_at(worse)


class TestBench:
    def test_backup_qb_is_not_bench_eligible(self, roster, table, cfg):
        """A backup QB in a single-QB league is close to worthless. Counting
        one would reward hoarding a position you can't start."""
        lineup = build("Team", roster, table, cfg)
        benched = {lineup["BN1"].name, lineup["BN2"].name}
        assert "Backup QB" not in benched

    def test_a_good_backup_qb_still_does_not_count(self, table, cfg):
        # Backup QB at rank 25 is better than the third bench TE at 95 and
        # still doesn't make the scored bench.
        roster = Roster(
            qb=["Starter QB"],
            rb=["Better RB", "Worse RB"],
            wr=["Better WR", "Worse WR"],
            te=["Starter TE"],
            flex=["Better Flex", "Worse Flex"],
            bench=["Backup QB", "Good Bench RB", "Third Bench TE"],
        )
        lineup = build("Team", roster, table, cfg)
        assert {lineup["BN1"].name, lineup["BN2"].name} == {"Good Bench RB", "Third Bench TE"}

    def test_only_the_best_two_bench_players_count(self, roster, table, cfg):
        lineup = build("Team", roster, table, cfg)
        assert len(lineup.slots) == len(cfg.slots)
        assert lineup["BN1"].name == "Good Bench RB"
        assert lineup["BN2"].name == "Bad Bench WR"

    def test_bench_is_ordered_by_value(self, roster, table, cfg):
        lineup = build("Team", roster, table, cfg)
        assert lineup.value_at("BN1") > lineup.value_at("BN2")

    def test_too_few_eligible_bench_players_raises(self, table, cfg):
        roster = Roster(
            qb=["Starter QB"],
            rb=["Better RB", "Worse RB"],
            wr=["Better WR", "Worse WR"],
            te=["Starter TE"],
            flex=["Better Flex", "Worse Flex"],
            bench=["Backup QB", "Good Bench RB"],  # only one eligible
        )
        with pytest.raises(LineupError, match="Backup QBs don't count"):
            build("Team", roster, table, cfg)


class TestValidation:
    @pytest.mark.parametrize(
        "group,value,expected",
        [
            ("rb", ["Better RB"], 2),
            ("wr", ["Better WR", "Worse WR", "Bad Bench WR"], 2),
            ("qb", [], 1),
            ("te", [], 1),
            ("flex", ["Better Flex"], 2),
        ],
    )
    def test_wrong_group_size_raises_clearly(self, roster, table, cfg, group, value, expected):
        broken = Roster(**{**vars(roster), group: value})
        with pytest.raises(LineupError, match=f"expected {expected} player"):
            build("Team", broken, table, cfg)

    def test_error_names_the_team(self, roster, table, cfg):
        broken = Roster(**{**vars(roster), "rb": []})
        with pytest.raises(LineupError, match="Sad Team:"):
            build("Sad Team", broken, table, cfg)

    def test_unknown_roster_group(self):
        with pytest.raises(LineupError, match="unknown roster groups"):
            Roster.from_dict({"qb": ["x"], "kicker": ["y"]})

    def test_player_missing_from_rankings(self, roster, table, cfg):
        broken = Roster(**{**vars(roster), "qb": ["Nobody At All"]})
        with pytest.raises(KeyError, match="needs a row in the rankings input"):
            build("Team", broken, table, cfg)


class TestOptimal:
    def test_it_starts_a_benched_star(self, table, cfg):
        """The case this exists for: a manager left his best back on the bench.

        As-set, the bench player scores at a bench slot. Optimal moves him into
        RB1 and demotes the back he was behind.
        """
        misset = Roster(
            qb=["Starter QB"],
            rb=["Worse RB", "Good Bench RB"],
            wr=["Better WR", "Worse WR"],
            te=["Starter TE"],
            flex=["Worse Flex", "Bad Bench WR"],
            bench=["Better RB", "Better Flex", "Third Bench TE"],
        )
        as_set = build("Team", misset, table, cfg)
        optimal = build_optimal("Team", misset, table, cfg)

        assert as_set["RB1"].name == "Good Bench RB"
        assert optimal["RB1"].name == "Better RB"

    def test_optimal_never_scores_worse_than_as_set(self, table, cfg):
        misset = Roster(
            qb=["Starter QB"],
            rb=["Worse RB", "Good Bench RB"],
            wr=["Better WR", "Worse WR"],
            te=["Starter TE"],
            flex=["Worse Flex", "Bad Bench WR"],
            bench=["Better RB", "Better Flex", "Third Bench TE"],
        )
        as_set = build("Team", misset, table, cfg)
        optimal = build_optimal("Team", misset, table, cfg)

        starters = cfg.starter_slots
        assert sum(optimal.value_at(s) for s in starters) >= sum(
            as_set.value_at(s) for s in starters
        )

    def test_it_benches_a_starter_who_is_worse_than_a_bench_player(self, roster, table, cfg):
        """The default roster fixture is quietly misset, which is realistic.

        Worse Flex is starting at rank 50 while Good Bench RB sits at 20. As-set
        scores the worse player; optimal swaps them. This is the difference
        between "how good is this roster" and "how good is it as actually set".
        """
        as_set = build("Team", roster, table, cfg)
        optimal = build_optimal("Team", roster, table, cfg)
        starters = cfg.starter_slots

        assert "Worse Flex" in {as_set[s].name for s in starters}
        assert "Good Bench RB" not in {as_set[s].name for s in starters}

        assert "Good Bench RB" in {optimal[s].name for s in starters}
        assert "Worse Flex" not in {optimal[s].name for s in starters}

        assert sum(optimal.value_at(s) for s in starters) > sum(
            as_set.value_at(s) for s in starters
        )

    def test_optimal_puts_the_best_backs_in_the_rb_slots(self, roster, table, cfg):
        # Where it does differ from as-set: the dedicated position slots get
        # the best players at that position, and flex takes the leftovers.
        optimal = build_optimal("Team", roster, table, cfg)
        assert [optimal["RB1"].name, optimal["RB2"].name] == ["Better RB", "Better Flex"]
        assert optimal.value_at("RB2") >= optimal.value_at("FLEX1")

    def test_a_canonical_lineup_is_untouched(self, table, cfg):
        # Flex players ranked below every dedicated starter, so there's nothing
        # for optimal to rearrange.
        canonical = Roster(
            qb=["Starter QB"],
            rb=["Better RB", "Better Flex"],
            wr=["Better WR", "Worse WR"],
            te=["Starter TE"],
            flex=["Good Bench RB", "Worse RB"],
            bench=["Worse Flex", "Bad Bench WR", "Third Bench TE"],
        )
        as_set = build("Team", canonical, table, cfg)
        optimal = build_optimal("Team", canonical, table, cfg)
        assert {s: p.name for s, p in as_set.players.items()} == {
            s: p.name for s, p in optimal.players.items()
        }

    def test_optimal_still_wont_start_a_qb_at_flex(self, table, cfg):
        roster = Roster(
            qb=["Starter QB"],
            rb=["Better RB", "Worse RB"],
            wr=["Better WR", "Worse WR"],
            te=["Starter TE"],
            flex=["Better Flex", "Worse Flex"],
            bench=["Backup QB", "Good Bench RB", "Bad Bench WR"],
        )
        optimal = build_optimal("Team", roster, table, cfg)
        assert all(p.position != "QB" for s, p in optimal.players.items() if s != "QB")

    def test_optimal_reports_an_illegal_roster(self, cfg):
        table = mini_table(cfg, [("Only Guy", "RB", 1)])
        with pytest.raises(LineupError, match="needs 1 player\\(s\\) at QB"):
            build_optimal("Team", Roster(rb=["Only Guy"]), table, cfg)


class TestAgainstRealData:
    def test_a_real_roster_slots_cleanly(self, real_table, cfg):
        roster = Roster(
            qb=["Josh Allen"],
            rb=["James Cook", "Jahmyr Gibbs"],
            wr=["Ja'Marr Chase", "Drake London"],
            te=["Trey McBride"],
            flex=["Chase Brown", "Zay Flowers"],
            bench=["Tucker Kraft", "Rome Odunze", "Javonte Williams"],
        )
        lineup = build("Real Team", roster, real_table, cfg)
        # Gibbs (consensus 1.75) outranks Cook (12.25), whatever order the
        # platform listed them in.
        assert same_player(lineup["RB1"].name, "Jahmyr Gibbs")
        assert same_player(lineup["RB2"].name, "James Cook")
        assert same_player(lineup["QB"].name, "Josh Allen")
        assert len(lineup.slots) == 10

    def test_suffix_spellings_resolve(self, real_table, cfg):
        roster = Roster(
            qb=["Josh Allen"],
            rb=["James Cook III", "Jahmyr Gibbs"],  # source spells it with a suffix
            wr=["Ja'Marr Chase", "Marvin Harrison Jr."],
            te=["Trey McBride"],
            flex=["Chase Brown", "Zay Flowers"],
            bench=["Tucker Kraft", "Rome Odunze"],
        )
        lineup = build("Real Team", roster, real_table, cfg)
        # The display name is whatever the CSV spells it; identity is what
        # matters, and that survives either spelling.
        assert same_player(lineup["RB2"].name, "James Cook")
        assert same_player(lineup["WR2"].name, "Marvin Harrison")
