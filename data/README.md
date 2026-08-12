# Data inputs

## raw_rankings.csv (repo root)

The committed input. One row per rostered player, one column per ranking
source, holding that source's own published rank and nothing else:

```
Name,Position,FantasyPros ECR,Expert composite,ESPN Field Yates,CBS Consensus
Jahmyr Gibbs,RB,3,1,2,1
Adonai Mitchell,WR,159,177,,166
```

Blank means that source didn't list the player. It does not mean "ranked last"
and must not be filled in with a placeholder — a player missing from a source
is simply not counted for that source.

Ranks are raw positions in each source's own full list, which is why they run
past the number of rows in the file (FantasyPros has a player at 287 here).
Re-indexing to 1..N within the filtered list happens in code, not here.

Columns map to:

| Column | Source |
| --- | --- |
| FantasyPros ECR | FantasyPros expert consensus ranking |
| Expert composite | Flock Fantasy expert composite |
| ESPN Field Yates | ESPN, Field Yates' list |
| CBS Consensus | CBS Sports consensus |

## data/sources/ (gitignored)

Where per-source exports live if you're pulling fresh rankings rather than
using the committed CSV. Gitignored on purpose — these lists are someone
else's editorial product and redistributing them isn't ours to do. The repo
works for anyone who supplies their own.

Expected filenames, one CSV per source, each with at least `Name`, `Position`,
and `Rank`:

```
data/sources/fantasypros.csv
data/sources/flock.csv
data/sources/espn_yates.csv
data/sources/cbs.csv
```

Same rule applies as above: raw published ranks only. No consensus column, no
value score, no averages. Anything derived is computed at runtime.
