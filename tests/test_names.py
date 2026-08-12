"""Name normalization tests.

Deliberately the heaviest test file in the repo. A name mismatch here doesn't
raise -- it silently drops one source from one player's average and quietly
reorders the standings, so it has to be caught by tests or not at all.
"""

import csv
import pathlib

import pytest

from fpr.core.names import normalize, same_player

CSV_PATH = pathlib.Path(__file__).resolve().parents[1] / "raw_rankings.csv"


@pytest.fixture(scope="module")
def csv_names():
    with CSV_PATH.open(newline="", encoding="utf-8") as fh:
        return [row["Name"] for row in csv.DictReader(fh)]


class TestCasingAndWhitespace:
    def test_case_is_ignored(self):
        assert same_player("JAHMYR GIBBS", "jahmyr gibbs")

    def test_surrounding_whitespace_is_ignored(self):
        assert same_player("  Bijan Robinson  ", "Bijan Robinson")

    def test_internal_whitespace_collapses(self):
        assert same_player("Bijan   Robinson", "Bijan Robinson")

    def test_tabs_and_newlines_collapse(self):
        assert same_player("Bijan\tRobinson\n", "Bijan Robinson")


class TestPunctuation:
    def test_apostrophes_are_stripped(self):
        assert same_player("Ja'Marr Chase", "JaMarr Chase")

    def test_curly_apostrophe_matches_straight(self):
        assert same_player("Ja’Marr Chase", "Ja'Marr Chase")

    def test_periods_are_stripped(self):
        assert same_player("A.J. Brown", "AJ Brown")
        assert same_player("T.J. Hockenson", "TJ Hockenson")
        assert same_player("J.K. Dobbins", "JK Dobbins")

    def test_hyphen_becomes_space(self):
        assert same_player("Jaxon Smith-Njigba", "Jaxon Smith Njigba")

    def test_apostrophe_case_variants_collapse(self):
        # The one that bites: sources disagree on both the apostrophe and the
        # capital V.
        assert same_player("De'Von Achane", "Devon Achane")

    def test_combined_punctuation(self):
        # Hyphen, period and a two-part surname in one name.
        assert normalize("Amon-Ra St. Brown") == "amon ra st brown"

    def test_apostrophe_mid_name(self):
        assert same_player("Wan'Dale Robinson", "Wandale Robinson")


class TestSuffixes:
    @pytest.mark.parametrize("suffix", ["Jr", "Jr.", "JR", "Sr", "Sr.", "II", "III", "IV", "V"])
    def test_suffix_stripped(self, suffix):
        assert same_player(f"Brian Robinson {suffix}", "Brian Robinson")

    def test_real_suffix_cases(self):
        assert same_player("Marvin Harrison Jr.", "Marvin Harrison")
        assert same_player("Brian Thomas Jr.", "Brian Thomas")
        assert same_player("Travis Etienne Jr.", "Travis Etienne")
        assert same_player("Kenneth Walker III", "Kenneth Walker")
        assert same_player("Luther Burden III", "Luther Burden")
        assert same_player("Deebo Samuel Sr.", "Deebo Samuel")

    def test_stacked_suffixes(self):
        assert same_player("Some Player Jr II", "Some Player")

    def test_suffix_word_inside_name_is_kept(self):
        # Only trailing tokens get peeled. A middle token that happens to look
        # like a suffix stays put.
        assert normalize("Robert V Smith") == "robert v smith"

    def test_single_token_name_survives(self):
        # A one-word name that is itself a suffix token must not normalize to
        # the empty string.
        assert normalize("V") == "v"
        assert normalize("Jr") == "jr"

    def test_suffix_does_not_merge_distinct_players(self):
        assert not same_player("Michael Pittman", "Michael Wilson")


class TestAliases:
    def test_nickname_alias(self):
        assert same_player("Kenny Gainwell", "Kenneth Gainwell")

    def test_alias_is_symmetric_through_canonical_form(self):
        assert normalize("Kenny Gainwell") == normalize("Kenneth Gainwell")
        assert normalize("Cam Skattebo") == normalize("Cameron Skattebo")
        assert normalize("Josh Downs") == normalize("Joshua Downs")

    def test_alias_survives_punctuation_differences(self):
        # Alias lookup happens after base normalization, so a differently
        # punctuated spelling of an aliased name still resolves.
        assert same_player("Bill Croskey Merritt", "Jacory Croskey-Merritt")
        assert same_player("Woody Marks", "Jo'Quavious Marks")

    def test_alias_with_suffix(self):
        assert same_player("Chris Rodriguez Jr.", "Christopher Rodriguez")

    def test_unaliased_name_passes_through(self):
        assert normalize("Puka Nacua") == "puka nacua"


class TestIdempotence:
    """normalize() gets applied at several layers; applying it twice must not
    change the answer or the whole thing is order-dependent."""

    @pytest.mark.parametrize(
        "name",
        [
            "Amon-Ra St. Brown",
            "Marvin Harrison Jr.",
            "Ja'Marr Chase",
            "Kenny Gainwell",
            "A.J. Brown",
            "Jaxon Smith-Njigba",
        ],
    )
    def test_normalize_twice_is_normalize_once(self, name):
        once = normalize(name)
        assert normalize(once) == once


class TestAgainstRealData:
    """The CSV is the actual input, so it gets to be a test fixture too."""

    def test_csv_has_expected_size(self, csv_names):
        assert len(csv_names) == 165

    def test_no_name_normalizes_to_empty(self, csv_names):
        assert [n for n in csv_names if not normalize(n)] == []

    def test_no_two_players_collide(self, csv_names):
        # If two distinct rostered players share a normalized key, one of them
        # silently overwrites the other in every lookup table downstream.
        seen: dict[str, str] = {}
        collisions = []
        for name in csv_names:
            key = normalize(name)
            if key in seen and seen[key] != name:
                collisions.append((seen[key], name))
            seen[key] = name
        assert collisions == []

    def test_every_name_is_stable_under_suffix_noise(self, csv_names):
        # Sources add and drop suffixes freely; adding one back must not change
        # which player a name resolves to.
        for name in csv_names:
            assert same_player(name, f"{name} Jr.")
