"""Dated JSON snapshots, for checking later whether the model was any good.

Stating a 70% chance of a top-four finish is only meaningful if somebody
eventually checks whether those teams finished top four about 70% of the time.
That check needs history, and history can only be collected forwards -- which
is why this exists well before the calibration work that will consume it.
Starting to record months later just means waiting months longer.

Writing a snapshot is opt-in, via --snapshot. Nothing in this repo writes them
on a schedule: a snapshot names real league members and their rosters, so where
and how often it gets written is the operator's call, not a default.

Each snapshot holds the standings, the simulation's probabilities, and the
consensus ranks used that day. The last of those matters more than it looks:
without it there's no way to tell later whether a team's fortunes changed
because the roster changed or because the sources changed their minds.

Note this file is `json.py` inside `fpr.report` and still does `import json`.
Python 3 resolves that to the standard library, because absolute imports are
the default.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from fpr.core.rankings import positional_strength, run
from fpr.core.simulate import SimulationResult, coin_flip_fraction
from fpr.pipeline import League

SCHEMA_VERSION = 1


def build(league: League, result: SimulationResult | None = None, when: date | None = None) -> dict:
    """Everything worth being able to look back at."""
    cfg = league.cfg
    standings = run(league.lineups, cfg)
    strength = positional_strength(league.lineups, cfg)

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "date": (when or datetime.now(timezone.utc).date()).isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": league.source_description,
        "lineups": "optimal" if league.optimal else "as_set",
        "matchups": standings.matchups,
        "coin_flip_fraction": round(coin_flip_fraction(league), 4),
        "config": {
            "value_curve": {
                "ceiling": cfg.value_curve.ceiling,
                "decay": cfg.value_curve.decay,
                "floor": cfg.value_curve.floor,
            },
            "bench_weight": cfg.configured_bench_weight,
            "slots": list(cfg.slots),
        },
        "standings": [
            {
                "place": place,
                "team": team.team,
                "score": round(team.score, 4),
                "wins": team.wins,
                "losses": team.losses,
                "ties": team.ties,
                "win_pct": round(team.win_pct, 4),
                "point_diff": round(team.point_diff, 4),
                "starter_record": team.record_at(cfg.starter_slots),
                "positional_strength": strength[team.team],
                "lineup": [
                    {
                        "slot": slot,
                        "player": league.lineups[team.team][slot].name,
                        "position": league.lineups[team.team][slot].position,
                        "consensus_rank": round(league.lineups[team.team][slot].rank, 4),
                        "value": round(league.lineups[team.team][slot].value, 4),
                    }
                    for slot in cfg.slots
                ],
            }
            for place, team in enumerate(standings, start=1)
        ],
        # The ranks that produced the above. Without these there's no telling
        # later whether a team moved because its roster changed or because the
        # sources changed their minds.
        "consensus": [
            {
                "player": player.name,
                "position": player.position,
                "rank": round(player.rank, 4),
                "spread": round(player.spread, 4),
                "sources": player.source_count,
                "ranked": player.ranked,
            }
            for player in league.table.ordered()
        ],
        "injury_status": dict(sorted(league.injury_status.items())),
    }

    if result is not None:
        snapshot["simulation"] = _simulation(result)

    return snapshot


def _simulation(result: SimulationResult) -> dict:
    top_n = min(4, len(result.teams))
    return {
        "trials": result.trials,
        "weeks": result.weeks,
        "flat_availability": result.flat_availability,
        "bench_weight": {
            "measured": round(result.bench_weight, 4),
            "ci_low": round(result.bench_weight_ci[0], 4),
            "ci_high": round(result.bench_weight_ci[1], 4),
            "configured": result.configured_bench_weight,
            "disagrees": result.bench_weight_disagrees,
        },
        "teams": [
            {
                "team": team,
                "expected_finish": round(result.expected_finish(team), 4),
                "p_first": round(result.p_first(team), 4),
                f"p_top_{top_n}": round(result.p_top(team, top_n), 4),
                "p_last": round(result.p_last(team), 4),
                # The whole distribution, so a later calibration pass isn't
                # limited to whatever summary statistics seemed useful today.
                "finish_counts": [int(n) for n in result.finishes[team]],
            }
            for team in result.ordered()
        ],
    }


def render(league: League, result: SimulationResult | None = None, when: date | None = None) -> str:
    return json.dumps(build(league, result, when), indent=2) + "\n"
