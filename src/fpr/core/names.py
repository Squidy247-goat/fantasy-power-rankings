"""Player name matching.

Every source spells names its own way. FantasyPros writes "Marvin Harrison
Jr.", ESPN drops the suffix, CBS punctuates "A.J. Brown" and Sleeper doesn't.
One rostered player failing to match one source's spelling doesn't crash
anything -- it quietly drops a source from his consensus average, which moves
his rank a few spots, which reorders a slot, which changes the standings. That
failure mode is invisible, so everything that looks up a name goes through
normalize() and nothing looks up a raw string.
"""

import re

# Suffixes to drop off the end. Roman numerals only go as high as anyone
# actually gets numbered.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Nicknames and legal-name differences, which no amount of punctuation
# stripping will reconcile. Keys are what a source might print, values are what
# we standardize on. Written in display form for readability and normalized at
# import, so entries here don't have to be pre-lowercased.
#
# Note that apostrophe and period cases ("De'Von Achane" vs "Devon Achane",
# "D.K. Metcalf" vs "DK Metcalf") do NOT belong here -- base normalization
# already collapses those. This table is only for genuinely different names.
_ALIAS_SOURCE = {
    "Kenny Gainwell": "Kenneth Gainwell",
    "Cam Skattebo": "Cameron Skattebo",
    "Josh Downs": "Joshua Downs",
    "Chig Okonkwo": "Chigoziem Okonkwo",
    "Chris Rodriguez": "Christopher Rodriguez",
    "Gabe Davis": "Gabriel Davis",
    "Hollywood Brown": "Marquise Brown",
    "Tank Dell": "Nathaniel Dell",
    # Both of these go by a name unrelated to what's on the birth certificate,
    # and sources split on which one they print.
    "Woody Marks": "Jo'Quavious Marks",
    "Bill Croskey-Merritt": "Jacory Croskey-Merritt",
}


def _base(name: str) -> str:
    """Punctuation and suffix stripping, before alias resolution."""
    s = name.casefold().strip()
    s = s.replace("'", "").replace("’", "")  # straight and curly
    s = s.replace(".", "")
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()

    # Peel suffixes off the end, more than one if someone writes "Jr II".
    # Guarded on len > 1 so a single-token name can't be erased entirely.
    parts = s.split(" ")
    while len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


_ALIASES = {_base(k): _base(v) for k, v in _ALIAS_SOURCE.items()}


def normalize(name: str) -> str:
    """Return the canonical lookup key for a player name.

    Case, punctuation, hyphens, generational suffixes and known nicknames all
    collapse to one form:

        >>> normalize("Amon-Ra St. Brown")
        'amon ra st brown'
        >>> normalize("Marvin Harrison Jr.") == normalize("Marvin Harrison")
        True
        >>> normalize("Kenny Gainwell") == normalize("Kenneth Gainwell")
        True
    """
    key = _base(name)
    return _ALIASES.get(key, key)


def same_player(a: str, b: str) -> bool:
    """Whether two spellings refer to the same player."""
    return normalize(a) == normalize(b)
