"""Markdown output.

Three tables, in the order you'd want to read them: where everyone finished,
why they finished there, and then the underlying detail if you don't believe
it. The positional table is the important one -- it's what makes a standing
explicable, and "why is this team fourth" should be answerable by reading a
single row of it.
"""

from __future__ import annotations

from fpr.core.rankings import Standings, positional_strength, run
from fpr.pipeline import League


def _table(headers: list[str], rows: list[list[str]], align: list[str] | None = None) -> str:
    align = align or ["left"] * len(headers)
    sep = {"left": ":---", "right": "---:", "center": ":---:"}
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep[a] for a in align) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def standings_table(standings: Standings, starter_slots) -> str:
    rows = []
    for place, result in enumerate(standings, start=1):
        rows.append(
            [
                str(place),
                result.team,
                f"{result.score:.1f}",
                f"{result.win_pct:.3f}",
                result.record,
                result.record_at(starter_slots),
                f"{result.point_diff:+.1f}",
            ]
        )
    return _table(
        ["#", "Team", "Score", "Win%", "W-L-T", "Starters", "Point diff"],
        rows,
        ["right", "left", "right", "right", "center", "center", "right"],
    )


def positional_table(league: League, standings: Standings) -> str:
    """Each team's placing at every slot, best team first.

    Reading across a row tells you where a team is strong and where it's
    getting beaten, which is the whole explanation for its standing.
    """
    cfg = league.cfg
    strength = positional_strength(league.lineups, cfg)
    rows = []
    for result in standings:
        places = strength[result.team]
        rows.append([result.team] + [str(places[slot]) for slot in cfg.slots])
    return _table(
        ["Team"] + list(cfg.slots),
        rows,
        ["left"] + ["right"] * len(cfg.slots),
    )


def lineup_tables(league: League, standings: Standings) -> str:
    cfg = league.cfg
    chunks = []
    for result in standings:
        lineup = league.lineups[result.team]
        rows = []
        for slot in cfg.slots:
            player = lineup[slot]
            record = result.slots[slot]
            rows.append(
                [
                    slot,
                    player.name,
                    player.position,
                    f"{player.rank:.2f}",
                    f"{player.value:.1f}",
                    f"{record.wins}-{record.losses}-{record.ties}",
                ]
            )
        chunks.append(
            f"### {result.team}\n\n"
            + _table(
                ["Slot", "Player", "Pos", "Consensus", "Value", "Record"],
                rows,
                ["left", "left", "left", "right", "right", "center"],
            )
        )
    return "\n\n".join(chunks)


def render(league: League) -> str:
    """The full deterministic report."""
    cfg = league.cfg
    standings = run(league.lineups, cfg)

    mode = "optimal lineups" if league.optimal else "lineups as set"
    sources = _source_summary(league)

    parts = [
        "# Power rankings",
        "",
        f"{len(league.teams)} teams, {standings.matchups} slot matchups, {mode}. "
        f"Consensus built from {sources} over {len(league.table)} players.",
        "",
        "Teams are ranked by how many individual slot matchups they win against "
        "the rest of the league, not by total roster value. Point differential "
        "is a secondary signal.",
        "",
        "## Standings",
        "",
        f"Sorted by score. W-L-T counts every slot equally, but score weights "
        f"bench slots at {cfg.configured_bench_weight:g}, so the two can disagree "
        f"-- a team can pile up bench wins worth almost nothing. The starters "
        f"column is the record across the {len(cfg.starter_slots)} slots that "
        f"carry full weight, and it's what score mostly tracks.",
        "",
        standings_table(standings, cfg.starter_slots),
        "",
        "## Positional strength",
        "",
        "Each team's placing at every slot, 1 being the best in the league. "
        "A team's standing should be readable off its row.",
        "",
        positional_table(league, standings),
        "",
        "## Lineups",
        "",
        lineup_tables(league, standings),
        "",
    ]
    return "\n".join(parts)


def _source_summary(league: League) -> str:
    sources: dict[str, None] = {}
    for player in league.table:
        for source in player.source_ranks:
            sources.setdefault(source, None)
    names = list(sources)
    if not names:
        return "no sources"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"
