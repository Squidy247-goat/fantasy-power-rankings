"""ESPN adapter tests.

All against hand-built fixtures. Nothing here touches the network or needs
credentials, so CI doesn't depend on ESPN being up or on cookies that expire.
"""

import pytest

from fpr.core.availability import ACTIVE, INJURY_RESERVE, OUT, QUESTIONABLE
from fpr.platforms import espn
from fpr.platforms.base import Credentials, MissingCredentials, PlatformError, normalize_status


class FakePlayer:
    def __init__(self, name, position, lineupSlot, injuryStatus=None, **extra):
        self.name = name
        self.position = position
        self.lineupSlot = lineupSlot
        self.injuryStatus = injuryStatus
        for key, value in extra.items():
            setattr(self, key, value)


class FakeTeam:
    def __init__(self, team_name, roster):
        self.team_name = team_name
        self.roster = roster


class FakeLeague:
    def __init__(self, teams):
        self.teams = teams


def full_roster(**overrides):
    players = [
        FakePlayer("Josh Allen", "QB", "QB"),
        FakePlayer("Jahmyr Gibbs", "RB", "RB"),
        FakePlayer("James Cook", "RB", "RB"),
        FakePlayer("Ja'Marr Chase", "WR", "WR"),
        FakePlayer("Drake London", "WR", "WR"),
        FakePlayer("Trey McBride", "TE", "TE"),
        FakePlayer("Chase Brown", "RB", "RB/WR/TE"),
        FakePlayer("Zay Flowers", "WR", "FLEX"),
        FakePlayer("Tucker Kraft", "TE", "BE"),
        FakePlayer("Rome Odunze", "WR", "BE"),
        FakePlayer("Harrison Butker", "K", "K"),
        FakePlayer("Ravens D/ST", "D/ST", "D/ST"),
    ]
    return FakeLeague([FakeTeam(overrides.get("team_name", "Test Team"), players)])


class TestSlotMapping:
    def test_starters_land_in_the_right_groups(self):
        rosters, _ = espn.extract(full_roster())
        roster = rosters["Test Team"]
        assert roster.qb == ["Josh Allen"]
        assert roster.rb == ["Jahmyr Gibbs", "James Cook"]
        assert roster.wr == ["Ja'Marr Chase", "Drake London"]
        assert roster.te == ["Trey McBride"]

    def test_every_flex_spelling_collapses_to_one_group(self):
        rosters, _ = espn.extract(full_roster())
        assert set(rosters["Test Team"].flex) == {"Chase Brown", "Zay Flowers"}

    @pytest.mark.parametrize("slot", ["RB/WR", "WR/TE", "RB/WR/TE", "FLEX", "OP"])
    def test_flex_variants(self, slot):
        league = FakeLeague([FakeTeam("T", [FakePlayer("Somebody", "RB", slot)])])
        rosters, _ = espn.extract(league)
        assert rosters["T"].flex == ["Somebody"]

    def test_bench_players(self):
        rosters, _ = espn.extract(full_roster())
        assert "Tucker Kraft" in rosters["Test Team"].bench
        assert "Rome Odunze" in rosters["Test Team"].bench

    def test_kickers_and_defenses_are_dropped(self):
        rosters, _ = espn.extract(full_roster())
        everyone = rosters["Test Team"].all_players()
        assert "Harrison Butker" not in everyone
        assert "Ravens D/ST" not in everyone

    def test_a_kicker_on_the_bench_is_still_dropped(self):
        league = FakeLeague([FakeTeam("T", [FakePlayer("Some Kicker", "K", "BE")])])
        rosters, _ = espn.extract(league)
        assert rosters["T"].all_players() == []

    def test_an_unknown_slot_is_loud(self):
        """Better to fail than to silently drop a starter."""
        league = FakeLeague([FakeTeam("T", [FakePlayer("Mystery Man", "RB", "WEIRD")])])
        with pytest.raises(PlatformError, match="unrecognized ESPN lineup slot"):
            espn.extract(league)

    def test_nameless_players_are_skipped(self):
        league = FakeLeague([FakeTeam("T", [FakePlayer("", "RB", "RB")])])
        rosters, _ = espn.extract(league)
        assert rosters["T"].all_players() == []


class TestInjuryStatus:
    def test_healthy_players_are_not_listed(self):
        _, statuses = espn.extract(full_roster())
        assert statuses == {}

    def test_designations_are_normalized(self):
        league = FakeLeague(
            [
                FakeTeam(
                    "T",
                    [
                        FakePlayer("A", "RB", "RB", injuryStatus="QUESTIONABLE"),
                        FakePlayer("B", "WR", "WR", injuryStatus="OUT"),
                    ],
                )
            ]
        )
        _, statuses = espn.extract(league)
        assert statuses == {"A": QUESTIONABLE, "B": OUT}

    def test_ir_slot_beats_injury_status(self):
        """The two genuinely disagree, and the slot reflects a roster move
        somebody actually made."""
        player = FakePlayer("Hurt Guy", "RB", "IR", injuryStatus="ACTIVE")
        league = FakeLeague([FakeTeam("T", [player])])
        _, statuses = espn.extract(league)
        assert statuses["Hurt Guy"] == INJURY_RESERVE

    def test_ir_players_go_to_the_bench_not_the_bin(self):
        """He's still rostered. Dropping him would hide the fact that a team is
        carrying dead weight."""
        player = FakePlayer("Hurt Guy", "RB", "IR", injuryStatus="OUT")
        league = FakeLeague([FakeTeam("T", [player])])
        rosters, _ = espn.extract(league)
        assert rosters["T"].bench == ["Hurt Guy"]

    def test_injured_flag_without_a_status(self):
        player = FakePlayer("Hurt Guy", "RB", "BE", injuryStatus=None, injured=True)
        league = FakeLeague([FakeTeam("T", [player])])
        _, statuses = espn.extract(league)
        assert statuses["Hurt Guy"] == INJURY_RESERVE


class TestStatusVocabulary:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("ACTIVE", ACTIVE),
            ("active", ACTIVE),
            (None, ACTIVE),
            ("", ACTIVE),
            ("QUESTIONABLE", QUESTIONABLE),
            ("Q", QUESTIONABLE),
            ("O", OUT),
            ("SUSPENSION", OUT),
            ("IR", INJURY_RESERVE),
            ("INJURY_RESERVE", INJURY_RESERVE),
            ("PUP", INJURY_RESERVE),
            ("injured-reserve", INJURY_RESERVE),
        ],
    )
    def test_mapping(self, raw, expected):
        assert normalize_status(raw) == expected

    def test_unknown_designations_do_not_write_a_player_off(self):
        """Platforms invent tags. Ignoring one is visible in the report;
        quietly zeroing a player out isn't."""
        assert normalize_status("SOME_NEW_ESPN_TAG") == ACTIVE


class TestTeamNames:
    def test_team_name_attribute(self):
        assert espn.team_name(FakeTeam("Real Name", [])) == "Real Name"

    def test_falls_back_across_versions(self):
        class OldStyle:
            name = "Older Name"

        assert espn.team_name(OldStyle()) == "Older Name"

    def test_last_resort_uses_the_id(self):
        class Nameless:
            team_id = 7

        assert espn.team_name(Nameless()) == "Team 7"


class TestProjectedPoints:
    def test_it_prefers_the_attribute_that_actually_exists(self):
        """The documented name is a trap: it's projected_avg_points."""
        player = FakePlayer("A", "RB", "RB", projected_avg_points=12.5)
        assert espn.projected_points(player) == pytest.approx(12.5)

    def test_missing_attribute_is_none_not_a_crash(self):
        assert espn.projected_points(FakePlayer("A", "RB", "RB")) is None

    def test_none_valued_attribute_falls_through(self):
        player = FakePlayer("A", "RB", "RB", projected_avg_points=None, projected_points=9.0)
        assert espn.projected_points(player) == pytest.approx(9.0)


class TestCredentials:
    def test_prefixed_names_win(self, monkeypatch):
        monkeypatch.setenv("ESPN_LEAGUE_ID", "111")
        monkeypatch.setenv("LEAGUE_ID", "222")
        creds = Credentials.from_env("ESPN", espn.ENV_KEYS)
        assert creds.get("league_id") == "111"

    def test_unprefixed_names_still_work(self, monkeypatch):
        """The env file predates the per-platform prefixes."""
        monkeypatch.delenv("ESPN_LEAGUE_ID", raising=False)
        monkeypatch.setenv("LEAGUE_ID", "222")
        monkeypatch.setenv("SWID", "{abc}")
        creds = Credentials.from_env("ESPN", espn.ENV_KEYS)
        assert creds.get("league_id") == "222"
        assert creds.get("swid") == "{abc}"

    def test_missing_credentials_say_what_to_do(self):
        with pytest.raises(MissingCredentials, match="Copy .env.example"):
            Credentials().require("league_id")

    def test_cookies_are_optional(self, monkeypatch):
        monkeypatch.delenv("ESPN_S2", raising=False)
        monkeypatch.delenv("SWID", raising=False)
        monkeypatch.setenv("LEAGUE_ID", "222")
        creds = Credentials.from_env("ESPN", espn.ENV_KEYS)
        assert creds.require("league_id") == ("222",)
        assert creds.get("espn_s2") is None


class TestEmptyLeague:
    def test_no_teams_is_an_error(self):
        with pytest.raises(PlatformError, match="returned no teams"):
            espn.extract(FakeLeague([]))


class TestEndToEnd:
    def test_a_synced_roster_slots_into_a_lineup(self, real_table, cfg):
        """The whole point of the shared shape: what ESPN returns goes straight
        into the same lineup builder the YAML path uses."""
        from fpr.core.lineup import build

        rosters, _ = espn.extract(full_roster())
        lineup = build("Test Team", rosters["Test Team"], real_table, cfg)
        assert lineup["QB"].name == "Josh Allen"
        assert lineup["RB1"].name == "Jahmyr Gibbs"
        assert len(lineup.slots) == len(cfg.slots)

    def test_the_platform_class_satisfies_the_protocol(self):
        platform = espn.ESPNPlatform()
        assert platform.name == "espn"
        assert hasattr(platform, "sync")
        assert hasattr(platform, "sync_with_status")
