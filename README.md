# Fantasy power rankings

Power rankings for a 12-team ESPN league that don't work the way most power
rankings work.

The usual approach adds up a roster's total value and sorts. That rewards
hoarding — a team with four good running backs looks great even though it can
only start two of them. This one instead plays every team against every other
team at every lineup slot: QB against QB, RB1 against RB1, and so on down the
lineup. Twelve teams, ten slots, every pairing once, so 660 individual
matchups. Teams are ranked by how many of those they win. Point differential
gets tracked separately as a tiebreaker and a sanity check.

Because it's slot-by-slot, every standing is explicable. "Why is this team
fourth" is always answerable by reading one row of the positional strength
table.

## The parts

**Consensus ranks.** Four sources (FantasyPros ECR, Flock Fantasy's expert
composite, ESPN's Field Yates, CBS consensus) each publish their own ordering.
They disagree, they spell names differently, and they mix kickers and defenses
into their lists in inconsistent amounts. Sources get filtered to skill
positions and re-indexed 1..N before anything is averaged — averaging raw ranks
across lists carrying different amounts of K/DST noise quietly biases the
result. A player nobody lists is treated as replacement level rather than
dropped.

**Value curve.** Rank differential is the wrong scale for margin of victory: it
implies the gap between 200 and 320 is bigger than the gap between 1 and 20.
Real scoring decays steeply at the top and flattens out deep. So rank gets run
through `max(floor, ceiling - decay * ln(rank))` before anything is compared.

**Monte Carlo.** A single deterministic answer is false confidence. The sources
disagree, sometimes wildly, and averaging that disagreement away doesn't make it
go away — on the league this was built for, about a third of the 660 slot
matchups were decided by a margin narrower than the sources' own spread. Those
are coin flips being reported as certainties. So the whole round robin gets
replayed a thousand-plus times with each player's rank redrawn from a normal
centered on his consensus and widened by how much the sources argue about him.
Output is a distribution over finish position, not a number.

**Bench weight, derived rather than guessed.** Bench players are worth
something for exactly one reason: starters miss games. So rather than picking a
bench weight by feel, the simulation runs a 14-week season where each starter
has a per-week chance of being available, weighted by position (RBs get hurt
most, QBs least) and by current injury designation. When a starter is out, the
best *healthy* bench player covers — a bench player who's himself unavailable
can't, which keeps this from being a free lunch. The fraction of realized
lineup value that actually came from bench coverage is the bench weight. It
came out near 0.10. The intuition-based guess it replaced was 0.35.

Availability priors are documented estimates, not fitted values. That's called
out in `config/league.yaml` too, and fitting them against real outcomes is what
the history/calibration work is for.

## Setup

```
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

Add `.[platforms]` if you want to sync rosters off ESPN/Sleeper/Yahoo instead
of writing them out by hand.

Rosters go in `config/rosters.yaml` (gitignored — it names real people). Copy
`config/rosters.example.yaml` to start. Ranking source exports go in
`data/sources/`, also gitignored; see `data/README.md` for what goes where.

## A rule worth stating outright

No derived number ever gets written to a data or config file. Input files hold
only what a ranking source itself published — names, positions, raw ranks.
Consensus rank, value score, availability rate, bench weight, simulation
output: all computed from raw input, every run, every time.

This is worth being explicit about because caching the answer is genuinely
tempting for speed, and doing it would make the repo untestable against the one
thing it exists to compute. If a number that a formula should have produced
ends up sitting in a YAML file, that's a bug.
