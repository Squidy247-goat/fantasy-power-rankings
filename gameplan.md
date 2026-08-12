# Fantasy power rankings — complete build instructions for Claude Code

This is a self-contained spec. It assumes an empty repo and Claude Code has
none of our prior conversation. Hand this whole file to a Claude Code session
as the starting prompt, or split it at the milestone breaks and run one
milestone per session — that's the intended granularity.

A companion file, `raw_rankings.csv`, contains **raw, unprocessed** per-source
ranks for the 165 players on this league's rosters — one column per ranking
source, straight from each source's own list, nothing computed. It has no
consensus rank, no value score, and no derived column of any kind. Every
number that isn't a raw source rank — consensus, value, availability, bench
weight, simulation output, all of it — must be computed by the repo's own
code, every time it runs, from this raw input. Do not hardcode, cache, or
special-case any derived number anywhere in the codebase. If a number in this
spec (like the illustrative 1.75 mentioned in section 2.1) matches what your
code produces, that's a coincidence worth double-checking your math against,
not a value to copy in.

---

## 0. What this project is

A power-rankings tool for a 12-team ESPN fantasy football league (1 QB, 2 RB,
2 WR, 1 TE, 2 FLEX, K, D/ST — K and D/ST are never modeled). Instead of ranking
teams by total roster value, it plays every team against every other team at
every lineup slot — QB vs QB, RB1 vs RB1, and so on — and ranks by how many of
those 660 individual matchups a team wins. Point differential is tracked
separately as a secondary signal.

The methodology, in order of what was decided and why, matters more than the
code — a competent engineer could rebuild this from the reasoning below even
without the CSV.

---

## 1. Core methodology (build this first, milestone "M-1")

### 1.1 Consensus ranking

Each ranking source lists players in its own order, mixed in with kickers and
defenses in inconsistent ways. Before combining sources:

1. Filter every source to skill positions only (QB, RB, WR, TE).
2. Re-index 1..N within that filtered list — this is critical. Averaging raw
   ranks across sources that embed different amounts of K/DST noise silently
   biases the average. Only compare re-indexed dense ranks.
3. A player's consensus rank is the mean of the sources that list him. A
   player missing from a source is simply not counted for that source, not
   treated as "worst possible."
4. A player in **no** source gets treated as replacement level: rank = the
   deepest ranked player's rank + 40. Never silently drop an unranked rostered
   player — that produces a `KeyError` deep in the pipeline instead of a
   number.

### 1.2 Name normalization

Every source spells names differently: suffixes ("James Cook III" vs "James
Cook"), apostrophes ("Ja'Marr Chase"), hyphens ("Amon-Ra St. Brown"). Build one
`normalize()` function used everywhere a name is looked up:
- Lowercase, strip whitespace.
- Strip `'`, `.`
- Replace `-` with space.
- Strip trailing suffixes: Jr, Sr, II, III, IV, V.
- A small hardcoded alias table for cases normalization can't fix (e.g.
  "De'Von Achane" vs "Devon Achane", "Kenny Gainwell" vs "Kenneth Gainwell").

Test this module hardest of anything in the codebase — every downstream bug
in this kind of project turns out to be a name mismatch.

### 1.3 Rank-to-value curve

Margin of victory needs a numeric scale, and raw rank differential is wrong:
it would treat the gap between rank 200 and 320 as bigger than the gap between
rank 1 and rank 20. Real fantasy scoring decays steeply at the top and flattens
in the deep bench. Use a concave transform:

```
value(rank) = max(floor, ceiling - decay * ln(rank))
```

Defaults: `ceiling = 330.0`, `decay = 51.0`, `floor = 20.0`. These are tunable,
not sacred, but they're the values this league's rankings were built against.

### 1.4 Lineup slotting

Slots: `QB, RB1, RB2, WR1, WR2, TE, FLEX1, FLEX2, BN1, BN2`.

- Within each position group (RB, WR, FLEX), sort **by value, best first** —
  never trust the platform's display order. RB1 must always be the team's
  better back.
- Bench: only the best 2 bench-eligible players count, and eligibility is
  RB/WR/TE only — **exclude QB from bench eligibility**. A backup QB in a
  single-QB league is nearly worthless and counting one rewards hoarding over
  usable depth.
- Raise a clear error if a roster doesn't have exactly the expected count per
  group (2 RB, 2 WR, 2 FLEX, 1 QB, 1 TE, ≥2 bench-eligible).

### 1.5 Round robin and weighting

Every pair of teams plays every slot once: 12 teams → 660 matchups. Higher
value wins the slot; margin accumulates as point differential (zero-sum across
the league). Ties (equal value) split 0.5/0.5.

**Starter slots weight 1.0. Bench slots weight less** — see 2.3 below for how
the bench weight is actually derived rather than guessed.

Track, per team: wins, losses, ties, score (sum of weighted wins), point
differential, and a per-slot win/loss/tie record for reporting.

### 1.6 Reporting

Markdown tables:
- Standings: rank, team, score, win%, W-L-T, point differential.
- Positional strength: for every team, its 1-12 rank at every slot (1 = best
  in the league at that slot). This table is what makes the standings
  explicable — "why is this team here" should always be answerable by reading
  one row.
- Full lineup detail per team: slot, player, position, consensus rank, slot
  rank, record at that slot.

---

## 2. Monte Carlo simulation (M0, after 1.x is solid and tested)

### 2.1 Why

A deterministic model gives one answer with false confidence. Check
`raw_rankings.csv`: several players have wildly different ranks across
sources — compute, for each player, the spread between their highest and
lowest listed rank across the four source columns, and look at the top of that
list. The deterministic model averages that
disagreement away and then treats the average as fact. On the real league this
was built for, **34% of the 660 slot matchups were decided by a margin smaller
than the sources' own disagreement** — meaning a third of "wins" in the
deterministic report were effectively coin flips being reported as certainties.

### 2.2 Ranking uncertainty

For each trial: for each player, draw a random rank from
`Normal(mean=consensus_rank, sd=spread)`, where `spread` is the standard
deviation of that player's rank across the sources that list him (minimum 6.0
even if sources agree exactly; 30.0 if only one source lists him — a lone
opinion deserves less confidence than a contested average, not more). Convert
the drawn rank to value via the same curve as 1.3. Clamp drawn rank to ≥1.
Run the full round robin with these drawn values. Repeat 1,000–2,000 times.
Aggregate: for each team, a distribution over finish position across all
trials — expected finish, P(finish 1st), P(top 4), P(last), etc.

### 2.3 Availability and the bench weight

Bench players have value for exactly one reason: starters miss games. Model
this explicitly instead of guessing a bench weight:

- Simulate a 14-week season per trial. Each starter has a per-week probability
  of being available (see 2.4 for how that probability is set).
- When a starter is simulated as unavailable in a given week, the best
  available (also probabilistically available, at its own rate) bench player
  at that slot covers instead. A bench player who is himself unavailable
  cannot cover — don't let this be a free lunch.
- Track, across all trials, what fraction of total realized lineup value came
  from bench coverage vs. starters playing as scheduled. **That fraction is
  the empirically derived bench weight** — use it in place of a guessed
  constant in the deterministic model (1.5). On the real league this measured
  to roughly 0.10-0.11, not the ~0.35 an initial guess produced — get this
  number from your own simulation, don't hardcode 0.11.
- Report the derived weight with a 95% interval across trials, and flag if the
  configured weight in `league.yaml` differs from the measured one by more
  than ~0.03.

### 2.4 Per-player availability, not one flat rate

Don't give every starter the same availability. Model two multiplicative
factors:
- **Position base rate**: RBs miss the most games, QBs the fewest. Reasonable
  starting priors: QB 0.88, RB 0.79, WR 0.84, TE 0.83. These are documented
  estimates, not fitted — say so in the code and the README, and make them
  overridable in config.
- **Injury designation multiplier**: a player's *current* status matters.
  QUESTIONABLE ≈ ×0.85, DOUBTFUL ≈ ×0.65, OUT ≈ ×0.55, injured reserve ≈ ×0.08.
  A season-long factor for a weekly designation should be gentler than the
  literal weekly sit-out probability, since a QUESTIONABLE tag in week 1 is
  usually resolved by week 4.
- Multiply the two, clamp to a sane range (e.g. [0.02, 0.97] — never model a
  player as fully certain or fully absent).
- This should visibly change team rankings relative to a flat rate, because it
  hits teams unevenly — a team with several questionable players should drop
  relative to a healthy team, which a flat rate can never show. If your
  implementation doesn't move any team's ranking when you switch from flat to
  per-player availability, something is wrong — go find the bug.

---

## 3. Platform sync (M1)

### 3.1 Design a shared interface first

Before writing any platform-specific code, define a `Platform` protocol/ABC
that every platform adapter implements:
```
sync(credentials) -> {team_name: roster}
sync_with_status(credentials) -> ({team_name: roster}, {player_name: injury_status})
```
Every adapter returns the same roster shape (a dict with `qb`, `rb`, `wr`,
`te`, `flex`, `bench` keys, each a list of player display names) regardless of
platform. This is the single most important design decision in this
milestone — get it right before writing ESPN, Yahoo, or Sleeper code, or all
three will need rework later.

### 3.2 ESPN (build first — it's the one with an existing working reference)

Use the `espn-api` PyPI package. Credentials: `LEAGUE_ID` always; `ESPN_S2`
and `SWID` (browser cookies) only for private leagues. Read from a `.env`
file via `python-dotenv` — **`load_dotenv()` must run before any
`os.getenv()` call and before constructing the League object**, or values come
back silently `None`.

Map ESPN's `lineupSlot` values to our slot groups: `QB→qb`, `TE→te`, `RB→rb`,
`WR→wr`, anything flex-like (`RB/WR`, `WR/TE`, `FLEX`, `OP`) → `flex`, `BE`
→ bench, `IR`/`K`/`D/ST` → skip entirely.

Extract injury status from `player.injuryStatus`; treat `lineupSlot == "IR"`
as an `INJURY_RESERVE` designation regardless of what `injuryStatus` says.

Support an `--optimal` mode that recomputes the best legal lineup by position
rank instead of trusting whatever ESPN has currently slotted — useful for
"how good could this roster be" vs. "how good is it as actually set." This
matters in practice: managers forget to set their lineup and bench their best
player.

Known gotcha from prior work on this exact library: the correct player
attribute is `projected_avg_points`, not `projected_points` — verify against
`dir(player)` rather than trusting memory or documentation.

### 3.3 Sleeper (build second — validates the interface cheaply)

Sleeper's API (`https://api.sleeper.app/v1/`) is public and needs **no
authentication** — just a league ID. Endpoints: `/league/{id}/rosters` and
`/league/{id}/users` to map roster IDs to team names, plus
`/players/nfl` (large, cache it) to map Sleeper's numeric player IDs to
names and positions. There is no live lineup-slot concept the way ESPN has
one — Sleeper rosters just list "starters" and "players"; you'll need to
infer QB/RB/WR/TE/FLEX assignment from position eligibility and the starters
list order, or ask the user which slot format their league uses.

Because there's no auth, this is the right platform to prove the shared
interface against before tackling Yahoo. Write a test using a recorded fixture
response rather than hitting the live API in CI.

### 3.4 Yahoo (build third — genuinely harder, needs a manual setup step)

Yahoo Fantasy uses OAuth2. This needs, before any code:
1. A Yahoo Developer app registered at developer.yahoo.com (manual, can't be
   automated).
2. A stored refresh token so the tool doesn't need re-authorization on every
   run — `yahoo_fantasy_api` (PyPI) handles most of this if you use it, or a
   raw OAuth2 flow if not.

Map Yahoo's roster/position data into the same shared roster shape as 3.1.
Yahoo's injury designations use different strings than ESPN's — normalize both
into one shared vocabulary before either reaches the availability model in
section 2.4 (`QUESTIONABLE`, `DOUBTFUL`, `OUT`, `INJURY_RESERVE`, etc. — pick
one vocabulary and map every platform into it).

---

## 4. Automated daily refresh (M2)

### 4.1 Ship the deterministic half first, independent of everything below

A GitHub Actions workflow, scheduled (`cron:`) plus manually triggerable
(`workflow_dispatch:`), that runs, in order: platform sync (ESPN and/or
Sleeper — whichever needs no manual OAuth step) → build consensus → run the
deterministic rankings → run the simulation → commit the Markdown output with
a dated filename. This has zero dependency on the scraping work below and
should be running before that work even starts.

Handle failure explicitly: a sync failure should stop the job loudly, not
commit a report built from stale or partial data silently.

### 4.2 Investigate before scraping — this ticket can conclude "don't automate it"

For each of FantasyPros, CBS, and any composite/"Flock"-style ranking source
you use: determine whether it's a plain server-rendered page (fetchable),
JavaScript-rendered (needs a real browser), or behind a login wall
(needs credentials, and likely against ToS to automate). Also check whether
the source sells API access — that's strictly better than scraping when
available. Write down the finding per source before writing any scraper code.
Read each source's terms of service regarding automated/programmatic access
specifically — daily automated scraping is a materially different thing from
a one-off manual export, and this is worth resolving before committing to a
recurring job, not after.

### 4.3 Headless-browser adapters, only for sources that need one

For any source confirmed in 4.2 to require JS rendering, use Playwright inside
the same GitHub Actions job (it can run a headless browser for free — no
external hosting required). Implement each as a loader satisfying the same
interface as the plain CSV/list loaders in section 1, so the consensus-building
code never needs to know where a source's data came from.

Do not reach for an always-on agent (Hermes or similar) for this. Those
require self-hosting a persistent process — real ongoing infrastructure and
cost for what should be a fixed, repeatable scraping script. Only reconsider
this if 4.2 finds a source that genuinely requires judgment calls (a
frequently-changing page structure, CAPTCHA-gated access) rather than a fixed
selector — that's a real signal an agent might be warranted, but don't assume
it going in.

### 4.4 Fail gracefully

If a scraper breaks — site redesign, rate limiting, CAPTCHA — the daily job
should fall back to the last known-good snapshot of that specific source and
log a visible warning, rather than either crashing the whole run or silently
averaging in garbage data (e.g. an empty or malformed source table).

---

## 5. History and calibration (M3, after M2 has run for a few weeks)

Write a dated JSON snapshot each day the automated job runs: standings,
simulation probabilities, and the consensus rankings used that day. Once
several weeks of history exist, check whether the model's stated probabilities
held up — did the team given a 70% chance of finishing top-4 actually do so
close to 70% of the time across the weeks it was tracked. This is also the
long-term path to replacing the documented-but-unfitted availability priors in
2.4 with values fitted against real outcomes, instead of estimates.

---

## 6. Project conventions

- **Language/tooling**: Python 3.10+, `pyproject.toml` with a `src/` layout,
  `pytest` for tests, `ruff` for lint, GitHub Actions CI running both on
  every push.
- **Package structure** — organize by dependency direction from the start,
  don't let it accrete flat:
  ```
  fpr/
    core/       # names, consensus, lineup, rankings, availability, simulate
                # — pure functions, no file or network I/O, easiest to test
    platforms/  # espn.py, sleeper.py, yahoo.py — the only files allowed
                # to touch a network or an OAuth flow
    adapters/   # CSV/list source loaders; scrapers/ subpackage for M2
    config.py   # typed config (dataclasses), parsed once, fails loudly on
                # a bad config file instead of deep inside business logic
    pipeline.py # ONE function tying config + sources + platforms into
                # consensus + lineups. Every CLI command calls this once —
                # never let two commands independently re-derive the same
                # setup, that duplication is the thing to avoid above all
                # else in this codebase.
    report/     # markdown.py; leaves room for a future json.py
    cli.py      # thin: parse args, call pipeline.build(), call report,
                # print or write. If this file ever becomes the largest
                # file in the project again, that's the signal something
                # got duplicated instead of shared.
  ```
- **Config** (`config/league.yaml`): slots, per-slot weights, the value curve
  constants, position base rates and status multipliers for availability,
  simulation trial count — all overridable, none hardcoded into the logic
  modules.
- **Rosters** (`config/rosters.yaml`): gitignored — this identifies real
  people. Ship an `example.yaml` with fake teams instead.
- **Ranking source exports** (`data/sources/`): gitignored entirely. These are
  someone else's copyrighted editorial product — never commit them, never
  redistribute them. `data/README.md` should explain what filename/format each
  source needs and where to get it, so the repo works for anyone who supplies
  their own exports.
- **No precomputed derived data, ever.** `raw_rankings.csv` (or any future
  input file) may only contain what a ranking source itself published: names,
  positions, raw per-source ranks. Consensus rank, value score, availability
  rate, bench weight, simulation output — none of that belongs in a data file.
  All of it is computed by code, every run, from raw input. This is worth
  stating explicitly because it's tempting to "just cache the answer" for
  convenience, and doing so would make the repo untestable against the very
  thing it's supposed to compute. If you ever find yourself writing a number
  into a config or data file that a formula in this doc should have produced,
  stop and write the formula instead.
- **Credentials**: `.env`, gitignored, with `.env.example` showing the shape.
  Once three platforms exist, prefix by platform: `ESPN_*`, `YAHOO_*`,
  `SLEEPER_*` — don't let this get ambiguous once it's not just one platform.
- **Testing philosophy**: pure `core/` modules get the heaviest unit testing —
  they're cheap to test exhaustively and they're where a subtle bug (wrong
  curve, wrong tie-breaking, wrong bench eligibility) silently produces wrong
  rankings for every team at once. Platform adapters get tested against
  recorded fixtures, not live API calls, so CI doesn't depend on external
  services or credentials.

---

## 7. Build order

1. **Section 1** (core methodology) — pure logic, hardest-tested, no I/O.
   Nothing else starts until this passes tests against `raw_rankings.csv`.
   Compute the consensus rank for every player from the raw source columns and
   sanity-check the result by eye: whichever running back leads most of the
   four source columns should end up at or near the top of your computed
   consensus. If your code's top-ranked player looks obviously wrong against
   the raw columns, something in 1.1–1.3 is wrong before you build anything on
   top of it. Write this as an automated test, not a one-off manual check.
2. **Section 2** (Monte Carlo + availability) — built on top of 1, still no
   platform I/O needed to test it; use the CSV as a fixed input.
3. **Section 6's package structure and `pipeline.py`** — do this refactor
   *before* adding platforms, not after. Adding three platform adapters on top
   of duplicated setup logic triples the duplication instead of fixing it once.
4. **Section 3.1 + 3.2** (platform protocol + ESPN) — ESPN because there's a
   known-working reference to validate against.
5. **Section 3.3** (Sleeper) — cheapest possible test of the protocol design
   before Yahoo's real complexity.
6. **Section 4.1** (the deterministic half of the cron job) — ships value
   immediately, blocks on nothing scraping-related.
7. **Section 4.2** (source investigation) — short, research-only, determines
   whether 4.3 is even needed.
8. **Section 3.4** (Yahoo) and **4.3** (scrapers) — can happen in either
   order after 7, since they don't depend on each other.
9. **Section 5** (history/calibration) — only once 6's cron job has actually
   been running for a few weeks and produced something to calibrate against.