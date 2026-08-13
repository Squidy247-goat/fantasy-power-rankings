# Fantasy Power Rankings

Power rankings for a fantasy football league that don't rank teams by total
roster value. Instead, every team plays every other team head-to-head at
every lineup slot — QB vs QB, RB1 vs RB1, and so on — and the standings are
how many of those matchups a team actually wins. Point differential is
tracked as a secondary signal, since it tells a different story: a team can
own the most talent in the league and still finish mid-table if that talent
is concentrated in a couple of slots instead of spread across the lineup.

On top of the deterministic standings, a Monte Carlo simulation redraws every
player's rank from how much the sources actually disagree on them, and models
week-to-week injury availability, producing finish probabilities instead of
one fixed answer — plus an empirically *derived* bench-slot weight rather
than a guessed one.

## Install

```bash
pip install "fpr[platforms] @ git+https://github.com/YOUR_USERNAME/fantasy-power-rankings.git"
```

Drop `[platforms]` if you're not syncing rosters live from ESPN/Yahoo/Sleeper
and are fine typing them into `config/rosters.yaml` by hand.

## Quick start

1. Copy `config/rosters.example.yaml` to `config/rosters.yaml` and fill in
   your league — or skip this and sync live instead (see Platforms below).
2. Get ranking data. This tool needs raw per-source ranks, never a
   precomputed consensus — see `raw_rankings.csv`'s header for the exact
   column format. FantasyPros, ESPN, and CBS all publish rankings; export
   whatever you can and drop it in.
3. Run it:

```bash
fpr rank                 # deterministic standings, lineups as set
fpr rank --optimal        # re-slotted into each team's best legal lineup
fpr simulate               # adds finish probabilities + derived bench weight
```

## Config

Everything tunable lives in `config/league.yaml`: slot weights, the
rank-to-value curve, roster shape, availability priors, simulation trial
count. Nothing in the core logic hardcodes any of these — if a number in a
report doesn't look right, it's a config change, not a code change.

`raw_rankings.csv` should be the **full list** each source ranks, not
filtered down to your specific rosters. Roster membership changes constantly
— trades, waivers, a different league entirely — and filtering the data to
match it just means it silently goes stale the next time something changes.

## Platforms

```bash
fpr sync --platform espn        # pull rosters without ranking, useful for debugging
fpr simulate --platform espn    # sync, then rank and simulate in one step
```

ESPN needs `LEAGUE_ID` always, and `ESPN_S2` + `SWID` (browser cookies) for
private leagues — copy `.env.example` to `.env` and fill them in locally.
Sleeper needs no auth at all. Yahoo needs an app registered at
developer.yahoo.com first — that step can't be scripted, everything after it
can.

## Automating it daily

**Keep your real league data out of this repo, in a second, private one.**
This repo is the general-purpose tool; your rosters, secrets, and generated
reports belong somewhere only you can see, because reports on the default
schedule commit your league members' real team names and rosters, and a
public repo publishes every commit.

```bash
gh repo create your-league-name --private --clone
cd your-league-name
mkdir -p .github/workflows config
```

Copy a daily workflow into `.github/workflows/`, installing this package
from GitHub rather than assuming the code lives in the same repo:

```yaml
- name: Install
  run: pip install "fpr[platforms] @ git+https://github.com/YOUR_USERNAME/fantasy-power-rankings.git"
```

Then, in the private repo: set your three ESPN secrets (`gh secret set
LEAGUE_ID`, etc., run one at a time — pasting several `gh secret set`
commands as one block queues input into the wrong prompt), add your real
`config/league.yaml` and `raw_rankings.csv`, commit, push, and trigger it by
hand once (`gh workflow run "Daily rankings"`) before trusting the schedule.

## Optional: FantasyPros API refresh

`fpr simulate --refresh-rankings` pulls a live FantasyPros ECR column instead
of using whatever's committed. Needs `FANTASYPROS_API_KEY` — request one at
secure.fantasypros.com/api-keys/request. Without a key the tool just uses the
committed CSV; nothing breaks.

## Contributing

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

Core logic (`fpr/core/`) has no file or network I/O and is where tests should
be heaviest — a subtle bug there silently produces wrong rankings for every
team at once. Platform adapters are tested against recorded fixtures, not
live API calls.
