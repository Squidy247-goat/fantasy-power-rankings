# Can the ranking sources be refreshed automatically?

Investigated 12 August 2026. This is the research ticket from section 4.2 of
the gameplan, and it was allowed to conclude "don't automate it."

It does, mostly.

**Summary: don't build scrapers.** Three of the four sources either prohibit
automated access outright or put their rankings behind a login. The fourth
sells an API that covers exactly the data we want, which makes scraping it both
unnecessary and against its own robots.txt. The headless-browser work in
section 4.3 should not be started.

This is a point-in-time reading of published terms, not legal advice, and terms
change. Anyone acting on it should re-read the linked pages.

---

## FantasyPros — use the API

**Verdict: automate, via the official API. Do not scrape.**

They sell exactly what we need. The `/consensus-rankings` endpoint returns
expert consensus rankings aggregated across 130+ experts, which is the ECR
column in `raw_rankings.csv`. Tiers:

| Tier | Cost | What you get |
| :--- | :--- | :--- |
| Free | £0 | All endpoints, sample data, generous daily limit, non-production |
| Premium | $8.99/mo, bundled with a HOF subscription | Production keys for personal apps, higher rate limits |
| Commercial | Custom | Highest limits, historical data, redistribution rights |

Keys are requested at `secure.fantasypros.com/api-keys/request/` and passed in
an `x-api-key` header.

The premium tier is the right one here — this is a personal app, and it's
already bundled with a subscription rather than being an additional line item.

Scraping instead would be worse on every axis. Their `robots.txt` disallows
`/ajax/`, `/api/`, `/json/`, `/xml/` and `/nfl/ranker/` with a 5-second
crawl-delay, and those are precisely the paths a scraper would need. Their
terms also state: *"Except for a single copy made for personal use only, you
may not copy, reproduce, modify, republish, upload, post, transmit, or
distribute any documents or information from this site in any form or by any
means without prior written permission."*

Note that even with an API key, the free and premium tiers don't grant
redistribution rights — those are a commercial-tier feature. That's consistent
with what this repo already does: `data/sources/` is gitignored and the
rankings are never republished.

## CBS Sports — don't automate

**Verdict: manual export only.**

CBS Sports falls under the Paramount terms of use, whose acceptable-use section
prohibits, verbatim:

> Engage in unauthorized spidering, "scraping," data mining or harvesting of
> Content, or use any other unauthorized automated means to gather data from or
> about the Services.

That's about as direct as it gets, and it names scraping specifically rather
than leaving it to be inferred from a general prohibition. The same terms
separately forbid storing content in a database or archiving it.

No public API was found. `robots.txt` disallows `/data/*` and blocks GPTBot
entirely.

Daily automated collection here is a materially different thing from a person
exporting a ranking list once, and the terms cover the former explicitly.

## ESPN (Field Yates) — don't automate

**Verdict: manual export only.**

ESPN is a Disney property and falls under the Disney terms of use, which
prohibit accessing, monitoring, copying or extracting via *"a robot, spider,
script, or other automated means, including for purposes of creating or
developing AI tools, data mining or web scraping or otherwise compiling,
building, creating or contributing to any collection of data, data set or
database."*

The only carve-out is public search engine indexing. Building a dataset is
named directly, and a daily job assembling rankings into a CSV is squarely
that. Their `robots.txt` separately blocks `anthropic-ai`, `GPTBot`, `CCBot`
and `Google-Extended`.

**A caveat worth stating plainly rather than glossing over.** This repo already
talks to ESPN through the `espn-api` package to sync rosters, and that is the
same site under the same terms. The distinction being relied on is that roster
sync reads *your own league* using *your own credentials* — the same data the
site would show you when you log in — rather than harvesting editorial content
into a dataset. That's a genuinely different activity, and it's why the roster
sync is treated differently from the rankings scrape. It is not obviously
exempt under a literal reading of the clause above, and anyone uncomfortable
with that should use the Sleeper adapter, which has a public unauthenticated
API and no such problem.

## Flock Fantasy — needs a manual terms check; assume no

**Verdict: manual export until someone reads their terms properly.**

The most permissive `robots.txt` of the four — `Allow: /` with no disallows and
no crawl-delay. But that's the only thing in its favour:

- The site is a client-rendered Next.js app, so the rankings aren't in the
  served HTML. A scraper would need a real browser, which is the section 4.3
  path.
- Rankings sit behind account creation. Automating an authenticated session is
  the exact category the gameplan flags as "needs credentials, and likely
  against ToS to automate."
- Their Terms of Service is a footer link that didn't render in a plain fetch,
  so its text is genuinely unread. That's an unknown, not a green light.

A permissive `robots.txt` is not permission. It governs crawler politeness, not
contractual terms, and the login wall makes the terms the binding document.

---

## What this means for the build

**Section 4.3 (headless-browser scrapers) is cancelled.** It was conditional on
this investigation finding sources that needed a browser. The one source worth
automating has an API instead, and the sources that would need a browser are
the ones whose terms say not to.

**Do build:** a FantasyPros API loader satisfying the same interface as the
CSV loader in `adapters/`, so the consensus code never learns where a source's
numbers came from. Blocked on someone requesting an API key.

**Don't build:** anything using Playwright, and definitely not the always-on
agent the gameplan warns against. That warning holds for an additional reason
now — an agent is for sources needing judgement calls about changing page
structure, and we've concluded we shouldn't be parsing those pages at all.

**Keep:** the manual export workflow for CBS, ESPN and Flock. `data/README.md`
already documents the filename and format each one needs, `data/sources/` is
gitignored, and the repo works for anyone supplying their own exports. Three
sources refreshed by hand occasionally and one refreshed nightly is a perfectly
reasonable end state, and it's the one the terms actually permit.

**Still relevant:** section 4.4's graceful-failure requirement. With one
automated source it matters more, not less — if the FantasyPros call fails, the
run should fall back to the last known-good snapshot of that source and warn
loudly, rather than building a consensus from three sources while reporting it
as four.
