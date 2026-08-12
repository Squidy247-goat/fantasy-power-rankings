"""Reader for the wide raw-rankings CSV.

Format is one row per player, one column per ranking source:

    Name,Position,FantasyPros ECR,Expert composite,ESPN Field Yates,CBS Consensus
    Jahmyr Gibbs,RB,3,1,2,1
    Adonai Mitchell,WR,159,177,,166

A blank cell means that source didn't list the player. It emphatically does not
mean "ranked last" -- he's left out of that source's ordering entirely and out
of his own consensus average, rather than being assigned a penalty rank that
nobody published.

Nothing derived is read from or written to this file. The numbers here are the
raw positions each source published in its own list, which is why they run past
the row count.
"""

from __future__ import annotations

import csv
import pathlib
from dataclasses import dataclass

NAME_COLUMN = "Name"
POSITION_COLUMN = "Position"


class SourceDataError(ValueError):
    """Raised for a malformed or empty rankings file."""


@dataclass(frozen=True)
class RawPlayer:
    name: str
    position: str
    # Source column name -> the rank that source published. Sources that didn't
    # list the player are absent, never present with a placeholder.
    ranks: dict[str, float]


def load(path: pathlib.Path | str) -> list[RawPlayer]:
    path = pathlib.Path(path)
    if not path.exists():
        raise SourceDataError(f"rankings file not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise SourceDataError(f"{path} is empty")

        headers = [h.strip() for h in reader.fieldnames]
        for required in (NAME_COLUMN, POSITION_COLUMN):
            if required not in headers:
                raise SourceDataError(f"{path} has no {required!r} column (found {headers})")

        sources = [h for h in headers if h not in (NAME_COLUMN, POSITION_COLUMN)]
        if not sources:
            raise SourceDataError(f"{path} has no ranking source columns")

        players = []
        for lineno, row in enumerate(reader, start=2):
            name = (row.get(NAME_COLUMN) or "").strip()
            if not name:
                continue  # trailing blank line

            position = (row.get(POSITION_COLUMN) or "").strip().upper()
            if not position:
                raise SourceDataError(f"{path} line {lineno}: {name} has no position")

            ranks = {}
            for source in sources:
                cell = (row.get(source) or "").strip()
                if not cell:
                    continue
                try:
                    ranks[source] = float(cell)
                except ValueError as exc:
                    raise SourceDataError(
                        f"{path} line {lineno}: {name} has non-numeric rank {cell!r} "
                        f"in column {source!r}"
                    ) from exc

            players.append(RawPlayer(name=name, position=position, ranks=ranks))

    if not players:
        raise SourceDataError(f"{path} contained no player rows")
    return players


def source_columns(players: list[RawPlayer]) -> list[str]:
    """Every source column seen, in first-appearance order."""
    seen: dict[str, None] = {}
    for player in players:
        for source in player.ranks:
            seen.setdefault(source, None)
    return list(seen)
