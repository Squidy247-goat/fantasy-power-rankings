"""FantasyPros expert consensus rankings, over their official API.

Section 4.2 concluded this is the one ranking source worth automating, and that
it should be done through the API rather than by scraping. Their robots.txt
disallows exactly the paths a scraper would need, and they sell the data
directly -- see docs/source-investigation.md.

Requires an API key. Free keys are for prototyping; a production key comes
bundled with a FantasyPros HOF subscription. Neither grants redistribution
rights, which is why the fetched payload lands in the gitignored cache
directory and never in a commit.

**What this returns is a raw source column and nothing more.** The ECR number
FantasyPros publishes is itself a consensus across their experts, but as far as
this repo is concerned it's one opinion among four, re-indexed and averaged
exactly like the CSV columns. The API also returns rank_min, rank_max and
rank_ave describing how much their own panel disagreed. That's genuinely
interesting and deliberately unused: the spread this project models is
disagreement *between* sources, and folding in one source's internal spread
would double-count FantasyPros' uncertainty while leaving the other three
sources' unmeasured.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

from fpr.adapters.raw_csv import RawPlayer, SourceDataError
from fpr.config import SKILL_POSITIONS

API_ROOT = "https://api.fantasypros.com/public/v2/json"

# The column name this source contributes. Matches the CSV header so a run with
# the API enabled and a run off the committed file produce the same column.
SOURCE_NAME = "FantasyPros ECR"

CACHE = pathlib.Path(".cache/fantasypros.json")


class FantasyProsError(SourceDataError):
    pass


def url(season: int, sport: str = "nfl") -> str:
    return f"{API_ROOT}/{sport}/{season}/consensus-rankings"


def parse(payload: dict, source_name: str = SOURCE_NAME) -> list[RawPlayer]:
    """Turn an API response into raw per-source ranks.

    Pure, so it's testable against a recorded fixture rather than a live key.
    """
    if not isinstance(payload, dict):
        raise FantasyProsError(f"expected a JSON object from FantasyPros, got {type(payload)}")

    players = payload.get("players")
    if not players:
        raise FantasyProsError(
            "FantasyPros returned no players. Check the season and position parameters."
        )

    skill = set(SKILL_POSITIONS)
    out = []
    for entry in players:
        name = (entry.get("player_name") or "").strip()
        position = (entry.get("player_position_id") or "").strip().upper()
        if not name or position not in skill:
            # Kickers and defenses are never modeled, and a nameless row is
            # nothing we can match on.
            continue

        rank = entry.get("rank_ecr")
        if rank in (None, ""):
            continue
        try:
            rank = float(rank)
        except (TypeError, ValueError) as exc:
            raise FantasyProsError(
                f"non-numeric rank_ecr {rank!r} for {name}"
            ) from exc

        out.append(RawPlayer(name=name, position=position, ranks={source_name: rank}))

    if not out:
        raise FantasyProsError(
            "FantasyPros returned players but none at QB/RB/WR/TE with a rank."
        )
    return out


def load(
    api_key: str,
    season: int,
    *,
    sport: str = "nfl",
    position: str = "ALL",
    scoring: str | None = None,
    week: int | None = None,
    cache: pathlib.Path | str | None = CACHE,
    fetch: Callable[..., dict] | None = None,
) -> tuple[list[RawPlayer], list[str]]:
    """Fetch the rankings, falling back to the last good copy if the call fails.

    Returns the players and any warnings worth surfacing.

    The fallback is section 4.4's requirement, and the distinction it draws
    matters: a stale column with a loud warning is recoverable, whereas
    averaging in an empty or half-parsed table silently produces a consensus
    that looks fine and is wrong. So a failed call reuses yesterday's copy and
    says so; it never degrades to three sources while still reporting four.
    """
    cache_path = pathlib.Path(cache) if cache else None
    caller = fetch or _http_get

    params = {"position": position}
    if scoring:
        params["scoring"] = scoring
    if week is not None:
        params["week"] = week

    try:
        payload = caller(url(season, sport), api_key, params)
        players = parse(payload)
    except Exception as exc:
        stale = _from_cache(cache_path)
        if stale is None:
            raise FantasyProsError(
                f"FantasyPros request failed and there's no cached copy to fall "
                f"back on: {exc}"
            ) from exc
        return stale, [
            f"FantasyPros request failed ({exc}). Using the cached copy from "
            f"{_cache_age(cache_path)}. The ECR column is stale."
        ]

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

    return players, []


def _from_cache(cache_path: pathlib.Path | None) -> list[RawPlayer] | None:
    if not cache_path or not cache_path.exists():
        return None
    try:
        with cache_path.open(encoding="utf-8") as fh:
            return parse(json.load(fh))
    except Exception:
        # A corrupt cache is no better than no cache, and pretending otherwise
        # would turn a recoverable failure into a silent one.
        return None


def _cache_age(cache_path: pathlib.Path | None) -> str:
    if not cache_path or not cache_path.exists():
        return "an unknown time ago"
    from datetime import datetime, timezone

    when = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
    return when.isoformat(timespec="minutes")


def _http_get(endpoint: str, api_key: str, params: dict):  # pragma: no cover - network
    try:
        import requests
    except ImportError as exc:
        raise FantasyProsError(
            'the requests package isn\'t installed. Install the platform extra '
            'with: pip install -e ".[platforms]"'
        ) from exc

    response = requests.get(
        endpoint, headers={"x-api-key": api_key}, params=params, timeout=30
    )
    if response.status_code == 401:
        raise FantasyProsError(
            "FantasyPros rejected the API key (401). Check FANTASYPROS_API_KEY."
        )
    if response.status_code == 429:
        raise FantasyProsError("FantasyPros rate limit hit (429).")
    if response.status_code != 200:
        raise FantasyProsError(f"FantasyPros returned {response.status_code} for {endpoint}")
    return response.json()
