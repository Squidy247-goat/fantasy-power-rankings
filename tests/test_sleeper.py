"""Sleeper adapter tests, against a recorded fixture rather than the live API.

Sleeper needs no auth, which makes it tempting to just hit it in CI. Not doing
that: a test that depends on someone else's uptime fails for reasons that have
nothing to do with the change being tested.

These also double as the check that the shared interface from base.py is real.
Sleeper models rosters completely differently from ESPN -- no per-player slot
attribute, just a starters array positionally matched to league settings -- and
if the abstraction only fit ESPN, it would show up here.
"""

import json

import pytest

from fpr.core.availability import INJURY_RESERVE, OUT, QUESTIONABLE
from fpr.platforms import get
from fpr.platforms.base import Credentials, PlatformError
from fpr.platforms.sleeper import SleeperPlatform, extract, starter_slots, team_names

LEAGUE = {
    "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "BN", "BN", "BN"]
}

PLAYERS = {
    "1": {"full_name": "Josh Allen", "position": "QB"},
    "2": {"full_name": "Jahmyr Gibbs", "position": "RB"},
    "3": {"full_name": "James Cook", "position": "RB"},
    "4": {"full_name": "Ja'Marr Chase", "position": "WR"},
    "5": {"full_name": "Drake London", "position": "WR"},
    "6": {"full_name": "Trey McBride", "position": "TE"},
    "7": {"full_name": "Chase Brown", "position": "RB"},
    "8": {"full_name": "Zay Flowers", "position": "WR"},
    "9": {"full_name": "Tucker Kraft", "position": "TE", "injury_status": "Questionable"},
    "10": {"full_name": "Rome Odunze", "position": "WR"},
    "11": {"full_name": "Javonte Williams", "position": "RB", "injury_status": "IR"},
    "99": {"full_name": "Some Kicker", "position": "K"},
    "98": {"full_name": "Some Defense", "position": "DEF"},
    "97": {"first_name": "No", "last_name": "Fullname", "position": "WR"},
}

ROSTERS = [
    {
        "roster_id": 1,
        "owner_id": "u1",
        "starters": ["1", "2", "3", "4", "5", "6", "7", "8"],
        "players": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "99", "98"],
    },
    {
        "roster_id": 2,
        "owner_id": "u2",
        "starters": ["1", "2", "3", "4", "5", "6", "7", "8"],
        "players": ["9", "10", "97"],
    },
]

USERS = [
    {"user_id": "u1", "display_name": "manager_one", "metadata": {"team_name": "Real Team Name"}},
    {"user_id": "u2", "display_name": "manager_two", "metadata": {}},
]


@pytest.fixture
def synced():
    return extract(LEAGUE, ROSTERS, USERS, PLAYERS)


class TestSlotReconstruction:
    def test_bench_slots_are_not_part_of_the_starters_array(self):
        assert starter_slots(LEAGUE) == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"]

    def test_starters_map_positionally(self, synced):
        rosters, _ = synced
        roster = rosters["Real Team Name"]
        assert roster.qb == ["Josh Allen"]
        assert roster.rb == ["Jahmyr Gibbs", "James Cook"]
        assert roster.wr == ["Ja'Marr Chase", "Drake London"]
        assert roster.te == ["Trey McBride"]
        assert roster.flex == ["Chase Brown", "Zay Flowers"]

    def test_everyone_not_starting_is_on_the_bench(self, synced):
        rosters, _ = synced
        bench = rosters["Real Team Name"].bench
        assert "Tucker Kraft" in bench
        assert "Rome Odunze" in bench
        assert "Javonte Williams" in bench

    def test_kickers_and_defenses_never_appear(self, synced):
        rosters, _ = synced
        everyone = rosters["Real Team Name"].all_players()
        assert "Some Kicker" not in everyone
        assert "Some Defense" not in everyone

    def test_a_league_without_roster_positions_is_an_error(self):
        with pytest.raises(PlatformError, match="no roster_positions"):
            starter_slots({})

    def test_empty_starter_ids_are_skipped(self):
        rosters, _ = extract(
            LEAGUE,
            [{"roster_id": 1, "owner_id": "u1", "starters": ["0", "1"], "players": ["1"]}],
            USERS,
            PLAYERS,
        )
        # "0" is Sleeper's empty slot. Dropping it shifts Josh Allen into QB.
        assert rosters["Real Team Name"].qb == ["Josh Allen"]

    def test_unknown_player_ids_are_skipped(self):
        rosters, _ = extract(
            LEAGUE,
            [{"roster_id": 1, "owner_id": "u1", "starters": ["1", "nope"], "players": ["1"]}],
            USERS,
            PLAYERS,
        )
        assert rosters["Real Team Name"].qb == ["Josh Allen"]

    def test_no_rosters_is_an_error(self):
        with pytest.raises(PlatformError, match="returned no rosters"):
            extract(LEAGUE, [], USERS, PLAYERS)


class TestNames:
    def test_team_name_metadata_wins(self):
        assert team_names(USERS, ROSTERS)[1] == "Real Team Name"

    def test_falls_back_to_the_username(self):
        assert team_names(USERS, ROSTERS)[2] == "manager_two"

    def test_falls_back_to_the_roster_id(self):
        names = team_names([], [{"roster_id": 9, "owner_id": "nobody"}])
        assert names[9] == "Roster 9"

    def test_first_and_last_name_when_there_is_no_full_name(self, synced):
        rosters, _ = synced
        assert "No Fullname" in rosters["manager_two"].bench


class TestInjuryStatus:
    def test_designations_are_normalized_into_the_shared_vocabulary(self, synced):
        _, statuses = synced
        assert statuses["Tucker Kraft"] == QUESTIONABLE
        assert statuses["Javonte Williams"] == INJURY_RESERVE

    def test_healthy_players_are_absent(self, synced):
        _, statuses = synced
        assert "Josh Allen" not in statuses

    def test_sleeper_spellings_match_espn_spellings(self):
        """Both platforms feed one vocabulary. If they didn't, the availability
        model would treat the same injury differently per platform."""
        from fpr.platforms.base import normalize_status

        assert normalize_status("Out") == normalize_status("OUT") == OUT
        assert normalize_status("Questionable") == QUESTIONABLE


class TestPlayerCache:
    def test_it_writes_and_reuses_the_cache(self, tmp_path):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            if url.endswith("/players/nfl"):
                return PLAYERS
            if url.endswith("/rosters"):
                return ROSTERS
            if url.endswith("/users"):
                return USERS
            return LEAGUE

        cache = tmp_path / "players.json"
        creds = Credentials(values={"league_id": "123"})

        SleeperPlatform(fetch=fake_fetch, cache=cache).sync_with_status(creds)
        assert cache.exists()
        assert json.loads(cache.read_text())["1"]["full_name"] == "Josh Allen"

        first_call_count = calls.count(f"{'https://api.sleeper.app/v1'}/players/nfl")
        SleeperPlatform(fetch=fake_fetch, cache=cache).sync_with_status(creds)
        # Second run reuses the file rather than re-downloading five megabytes.
        assert calls.count(f"{'https://api.sleeper.app/v1'}/players/nfl") == first_call_count


class TestProtocolConformance:
    """The reason Sleeper is built second: it proves base.py isn't just ESPN
    with extra steps."""

    def test_it_is_registered(self):
        assert get("sleeper").name == "sleeper"

    def test_sync_drops_the_status_dict(self, tmp_path):
        responses = {"/players/nfl": PLAYERS, "/rosters": ROSTERS, "/users": USERS}

        def fake_fetch(url):
            for suffix, payload in responses.items():
                if url.endswith(suffix):
                    return payload
            return LEAGUE

        platform = SleeperPlatform(fetch=fake_fetch, cache=tmp_path / "c.json")
        rosters = platform.sync(Credentials(values={"league_id": "123"}))
        assert isinstance(rosters, dict)
        assert "Real Team Name" in rosters

    def test_both_adapters_return_the_same_shape(self, synced):
        """A Roster from Sleeper is indistinguishable from one from ESPN."""
        from fpr.platforms import espn
        from test_espn import full_roster

        sleeper_rosters, _ = synced
        espn_rosters, _ = espn.extract(full_roster())

        a = sleeper_rosters["Real Team Name"]
        b = espn_rosters["Test Team"]
        assert type(a) is type(b)
        assert a.qb == b.qb
        assert sorted(a.flex) == sorted(b.flex)

    def test_missing_league_id(self):
        with pytest.raises(Exception, match="missing credentials"):
            SleeperPlatform(fetch=lambda url: {}).sync_with_status(Credentials())
