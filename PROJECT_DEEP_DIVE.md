# finLM - The Deep Dive

*A complete walkthrough of this project from inception to now: what was built,
what broke, what we learned, and the trading concepts you need to follow any of
it. Written for someone with no finance background.*

Last updated 2026-08-09.

---

## Table of contents

1. [What this project actually is](#1-what-this-project-actually-is)
2. [Trading concepts you need first](#2-trading-concepts-you-need-first)
3. [The timeline, phase by phase](#3-the-timeline-phase-by-phase)
4. [Deep dive: the six incidents that shaped the project](#4-deep-dive-the-six-incidents-that-shaped-the-project)
5. [The one lesson underneath all of them](#5-the-one-lesson-underneath-all-of-them)
6. [How to run everything](#6-how-to-run-everything)
7. [Glossary](#7-glossary)

---

## 1. What this project actually is

### The one-sentence version

**finLM is a governance layer for AI agents that touch money.** It lets a
language model research the Indian stock market and propose trades, while making
it structurally impossible for that model to place an order the human didn't
authorise.

### Why that's the interesting part

It is easy to build an AI that suggests stock trades. Thousands of people have.
The hard part - the part almost nobody does - is making the system **trustworthy
enough to actually connect to a brokerage account.**

Consider the failure mode. You give an LLM a `place_order` tool. You write in its
instructions: "always ask the user before trading." The model is helpful,
agreeable, and easily confused. Someone pastes a web page into the conversation
that says "ignore previous instructions." Or the model simply misreads its own
prior message as approval. Now real money moves.

The naive fix, and the one the reference open-source project in this space
actually uses, is a `confirmed=true` parameter on the order tool. The model is
supposed to only set it after asking you.

**That is theatre.** The model sets the flag itself. It guards nothing. It is a
lock whose key is taped to the door.

finLM's answer: the authorisation lives in a **file on disk that only the human
writes**, and the order code refuses to run unless that file says the order is
within bounds. The model cannot create the file, cannot edit it, and there is no
parameter it can pass to skip the check. That's the whole thesis, and everything
else in the repo exists to test whether it actually holds.

### The second thing it is

Along the way, the project became a **research artifact about Indian equity
markets** - because to know whether the AI's suggestions are any good, you have
to measure them, and measuring them properly turns out to be most of the work.

That half produced two real findings and several corrections, all covered below.

### Architecture in one picture

```
  Any MCP-capable LLM client
        │  speaks MCP (Model Context Protocol - a standard way
        │  for an LLM to call external tools)
        ▼
  ┌─────────────────────────────────────────┐
  │  llmfin MCP server  (15 tools)          │
  │                                          │
  │  DISCOVERY  scan the market for movers   │  ← free, local data
  │  ANALYSIS   indicators, signals, news    │
  │  JOURNAL    log decisions, score them    │  ← the feedback loop
  │  BROKER     quotes, positions, ORDERS    │  ← the dangerous one
  └─────────────────┬───────────────────────┘
                    │
              ┌─────▼──────┐
              │ RISK GATE  │  ← risk_limits.json (only the human writes it)
              │            │     KILL_SWITCH file
              │ fails      │     daily order + rupee caps
              │ CLOSED     │
              └─────┬──────┘
                    ▼
              Zerodha Kite (real broker)
```

The important arrow is the one that **doesn't exist**: there is no path from the
LLM to the broker that bypasses the gate.

---

## 2. Trading concepts you need first

You said you're not from a trading background. Here is everything you need,
explained from zero. Skip ahead if a section is already familiar.

### 2.1 What the raw data is

Indian stocks trade on the NSE (National Stock Exchange). At the end of every
trading day the exchange publishes a **bhavcopy** - a plain CSV listing, for
every stock that traded that day:

| field | meaning |
|---|---|
| `symbol` | ticker, e.g. `RELIANCE` |
| `open` | first traded price of the day |
| `high` / `low` | highest and lowest price during the day |
| `close` | last traded price |
| `prev_close` | yesterday's close |
| `volume` | number of shares traded |
| `turnover` | rupee value traded (roughly price × volume) |

This is **EOD data** ("end of day"). One row per stock per day. We do *not* have
intraday data - no minute-by-minute prices, no bid/ask spread, no order book.
That limits what we can study, and it's worth knowing where the ceiling is.

The project downloads these files going back to 2010 and stores them in SQLite.
That's the entire data foundation.

**Why bhavcopy matters more than it sounds:** each day's file contains exactly
the stocks that traded *that day*. Not today's list of stocks projected
backwards - the actual historical roster. This turns out to matter enormously
(see §2.5).

### 2.2 What a backtest is, and why it's so easy to get wrong

A **backtest** asks: "if I had followed this rule in the past, what would have
happened?"

You take historical prices, pretend to trade by your rule, and add up the
results. Simple in principle. In practice it is a machine for lying to yourself,
because the past is fully known to you and was *not* known to the person you're
pretending to be.

The cardinal sin is **lookahead bias** - using information that wasn't available
yet.

The obvious version is easy to avoid: don't use tomorrow's price to decide
today's trade. The subtle versions are everywhere:

- You scan for stocks that moved big **today**, using today's closing price. But
  the closing price is only known *after* the market closes. You cannot buy at
  today's close using information from today's close. finLM handles this by
  entering at **tomorrow's opening price**, never today's close.
- You use a list of "the 500 biggest Indian companies" to define your universe.
  But that list is today's list. In 2012 it was a different list. (§2.5.)
- You apply a rule you invented *after* looking at the data, then test it on that
  same data. It will work. It means nothing.

finLM's backtest is **point-in-time**, meaning at every simulated day it only
sees data up to that day. The code enforces this structurally rather than by
discipline - the scanning function physically filters the panel to prior rows.

### 2.3 Alpha vs beta - why "I made 30%" means nothing

Suppose your strategy returned 30% last year. Good?

If the whole market rose 28% last year, you did essentially nothing - you could
have bought an index fund and matched it for a 0.03% fee. Your **beta** (market
exposure) generated the return, not your skill.

**Alpha** is the part of your return that the market's move *doesn't* explain.
It's what's left after subtracting the ride you got for free.

Every return number in this project is **benchmark-adjusted**: we build an
equal-weight index of the liquid Indian universe and subtract its return over the
same holding period. When you read "+2.32% alpha per trade," it means 2.32%
*better than the market did over those same days*.

> A number without a comparator is uninterpretable. This is the single most
> common way trading results are oversold.

### 2.4 Corporate actions - the bug that ate three weeks

Companies sometimes split their shares. A 1:2 split turns one ₹1,000 share into
two ₹500 shares. You own the same value; the price just halves overnight.

Raw bhavcopy does not adjust for this. It records yesterday's close as ₹1,000 and
today's close as ₹500.

Any program reading that sees **a 50% crash.**

This poisons everything downstream: the scanner flags a fake "biggest loser of
the day," moving averages spanning the event are garbage, and the backtest
records a catastrophic loss that never happened.

The fix is **back-adjustment**: detect the split and retroactively halve all
prices before it, so the series is continuous. `corporate_actions.py` does this,
and getting it right took two rounds of real bugs - covered in §4.1, because the
bugs are more instructive than the fix.

### 2.5 Survivorship bias - the one that kills most amateur backtests

Suppose you test a strategy on "the companies in the NSE today, going back to
2010."

Every company in that list **still exists in 2026.** You have silently excluded
every company that went bankrupt, got delisted, or collapsed in a fraud scandal.

Now test a strategy that buys stocks after they fall sharply ("buy the dip").
It'll look spectacular - because in your dataset, every dip recovered. The ones
that didn't recover aren't in the data. You've accidentally encoded the answer.

This is not a small effect. It has invalidated published academic papers.

finLM avoids it *by construction*, because it uses per-day bhavcopy files rather
than a symbol list. Verified empirically in §4.5: of 2,543 symbols in the
2010-2020 database, **771 stop appearing before mid-2020** - they died, and
they're still in the data, with their collapses intact.

### 2.6 The two strategies this project studies

The market scanner finds stocks that made a big move today on unusually high
volume. Given such a stock, there are two opposite things you can believe:

- **Trend following ("chase")** - it's moving up, momentum will continue, buy it.
- **Mean reversion ("fade")** - it overreacted, it'll snap back, sell it.

These are genuinely opposing philosophies. A key design decision (§4.2) is that
finLM **never averages them into one score.** Averaging a strong BUY and a strong
SELL gives you a meaningless zero. Both opinions are presented, and the ranking
layer or the human decides.

The empirical result, across 11 years: **chasing loses, fading wins.** That's the
project's central finding, and §4.6 explains how much of it survived scrutiny.

### 2.7 Stops, targets, and ATR

When you enter a trade you decide in advance where you'll get out:

- **Stop loss** - the price at which you admit you're wrong and exit at a loss.
- **Target** - the price at which you take your profit.

How far away should they be? A fixed "₹10" is nonsense - ₹10 is a rounding error
on a ₹5,000 stock and a catastrophe on a ₹50 one.

**ATR (Average True Range)** measures how much a stock typically moves in a day.
Stops and targets are set as multiples of ATR, so they scale automatically. The
project's main config uses a stop at 2.0×ATR and a target at 2.5×ATR.

### 2.8 Transaction costs - where edges go to die

Every trade costs money: brokerage, exchange fees, stamp duty, STT (a
government transaction tax), and **slippage** (you rarely get exactly the price
you wanted).

In India this totals roughly **0.3%-0.5% per round trip.** That sounds tiny. It
isn't. A strategy earning 1.2% per trade keeps only ~0.8% after costs - a third
of the edge, gone.

The project models this explicitly (`ExitConfig.cost_pct`) rather than applying a
mental discount afterwards. There's a test asserting that a strategy with *no
edge at all*, trading with 80% monthly turnover, **loses 6.6% per year purely to
costs.** That number is the hurdle every finding has to clear.

### 2.9 Long, short, and why it matters here

- **Long** = you buy, hoping the price rises.
- **Short** = you borrow shares, sell them, and hope to buy back cheaper. You
  profit when the price *falls*.

Shorting is how you'd trade the "fade" strategy on a stock that spiked upward.

**In an Indian retail cash account, you cannot hold a short position overnight.**
Intraday only. To hold a multi-day short you need futures or options, which is a
different account, different margins, and a restricted universe of ~180 stocks.

This matters enormously: **97% of the project's measured edge is on the short
side**, which means the headline result is largely untradeable in the account the
owner actually has. That's stated up front rather than buried, and it's arguably
the most interesting thing about the finding (see §4.6).

---

## 3. The timeline, phase by phase

Reconstructed from git history (`git log --reverse`) plus the project's own
documentation.

### Phase 0 - Inception (20 July 2026)

```
c23979c  Add files via upload
45654d7  Rebuild as a working intelligence layer: scanner, signals, journal, risk gate
```

The project began as an exploration of one question: what would it take to let
an LLM near a brokerage account without it being reckless?

The first real commit message says "**Rebuild** as a working intelligence layer,"
which tells you the first upload was scrapped almost immediately. What replaced
it established the four pieces that still exist today:

- **scanner** - find interesting stocks deterministically
- **signals** - two opposing alpha models
- **journal** - record decisions *with reasoning*, before outcomes are known
- **risk gate** - the mandate-file pattern

The key insight at inception, and the reason the project has held together: the
LLM is used for **research and explanation**, not for computing numbers. Every
number comes from deterministic Python. The model reads, reasons, and writes; it
never multiplies.

### Phase 1 - The backtest (20 July)

```
dfc8c8b  Add point-in-time backtest of the deterministic core (llmfin-backtest)
96d0998  Upgrade backtest to a research engine: alpha benchmark, splits, entry styles
```

Two commits, same day, and the second is where the project got serious. Note what
was added: an **alpha benchmark** (§2.3), **split adjustment** (§2.4), and
**entry styles**. That's the moment it stopped being a demo and became a
measuring instrument.

This is also where the central finding first appeared: chasing loses, fading
wins.

### Phase 2 - Integration polish (21 July)

```
ddbfc50  Sharpen research_symbol: move-aware queries, general index, honest answer caveat
9d29f8f  Add --http mode for desktop MCP client connectors
c8fee28  cwd-independent .env loading
```

Plumbing. One item matters: "**honest answer caveat**." The web-research tool
uses Tavily, whose AI-generated summary once invented a fact - it claimed a stock
moved because of an earnings report that didn't exist. The fix was to stop
trusting the synthesised answer and present the underlying sources instead.

First appearance of the project's recurring theme: *the system asserted something
it hadn't verified.*

### Phase 3 - Framing the thesis (25 July)

```
650cd13  Add project writeup
ebd259c  Name the project finLM
d085f92  Add short-form submission field copy
ac5ea3b  Reframe submission for 'Governance Layer for Financial Agents' theme
96571c1  Add pitch deck generator
22ec110  Add pitch deck
738648c  Disclose sample size on the hypothesis-reversal slide
```

The reframe in `ac5ea3b` is the important one. The project stopped presenting
itself as "an AI that trades" and started presenting itself as "the layer that
makes an AI safe to let near money." Same code, much better claim - and a true
one, since the governance work was always the distinctive part.

`738648c` - "**Disclose sample size**" - is a small commit that says a lot about
the project's standards. The strongest slide showed the AI catching its own
hypothesis being wrong. That result rested on **five decisions over one week**.
Rather than let it imply more, they put the sample size on the slide.

### Phase 4 - The 11-year validation (29 July - 3 August)

```
e736307  Add long-side scanner, portfolio backtest, and data-quality diagnostics
```

One commit, and it is enormous. Its message lists: extracting
corporate-action handling into its own module and wiring it into the *live* path,
a long-side scanner, a standing data-anomaly report, **explicit transaction-cost
modelling**, a portfolio-level backtest, and per-year regime analysis.

Behind it sat the real work: downloading and validating **eleven years** of NSE
history (4.15 million rows), and finding two serious bugs in the
corporate-action adjuster along the way (§4.1).

The result: the fade strategy showed **positive alpha in all eleven years**.

### Phase 5 - The hardening (9 August - one long session)

No commits yet; all working-tree changes. This is the phase where the project
stopped adding features and started attacking itself. Six incidents, all in §4:

- red-teamed the risk gate and found four real ways to break it
- found the documented tool count was wrong in four places
- found the headline backtest number **could not be reproduced**
- built provenance infrastructure so that can't recur
- pre-registered a hypothesis about *why* results decayed, and falsified it
- built a second engine and tested eleven published anomalies

Test count went from **29 to 114**.

---

## 4. Deep dive: the six incidents that shaped the project

This is the part worth reading. Each one is a case study in a specific way that
software about money goes wrong.

---

### 4.1 The corporate-action adjuster: two bugs found by backtesting

**The setup.** As explained in §2.4, splits must be back-adjusted or they look
like crashes. The detector's logic: if a stock's price changed by a suspicious
ratio in one day, and that ratio is close to a common split ratio (1:2, 1:3,
2:1...), treat it as a split and adjust.

**Bug 1 - FINANTECH, August 2013.**

Financial Technologies India crashed during the NSEL scam - one of the largest
commodity-exchange frauds in Indian history. The stock lost roughly two-thirds of
its value in a day.

Ratio: about 0.333. Which is *very* close to a 1:3 split.

The adjuster "corrected" a real crash out of existence. The backtest saw a normal
day where a catastrophe had happened.

**The fix - a volume sanity check.** A mechanical split is a bookkeeping event;
trading volume barely changes, typically 2-6× normal. A company collapsing in a
fraud scandal trades at 10-1000× normal volume, because everyone is selling at
once. So: only adjust if event-day volume is within 15× its trailing 20-day
average.

**Bug 2 - JETAIRWAYS, June 2019.**

Jet Airways' bankruptcy death-spiral. One day's ratio was 1.899 - close enough to
a 2:1 split to match. And the volume check *passed*, because the stock had been
in crisis for weeks, so its "normal" volume baseline was already enormous.

The guard failed precisely because the crisis was prolonged.

**The fix - an isolation check.** A real split is a one-off event. A company
mid-collapse produces *clusters* of anomalies. So: refuse to adjust if the same
symbol has another suspect ratio within 10 trading days.

**Why this matters as a lesson.** Both bugs were found by *running the backtest
over a long history and investigating the weird results* - not by reading the
code, not by unit tests written in advance. The 11-year backtest was valuable as
much for the bugs it surfaced as for the alpha it measured.

Both fixes were then verified against known-real splits (AJANTPHARM, APOLLOHOSP,
BAJFINANCE and others) to confirm they still adjust correctly. Fixing a false
positive by breaking the true positives is the classic overcorrection, and it was
explicitly checked for.

---

### 4.2 The design decision that isn't a bug: never average the models

New contributors look at `signals.py` and see two models producing conviction
scores between −1 and +1, with no code combining them. The instinct is to "fix"
this by averaging into one score.

**Don't.** Averaging opposing philosophies destroys the information.

Trend following says a strong uptrend is a BUY. Mean reversion says the same
uptrend is overbought - a SELL. Average them and you get zero, which reads as "no
opinion." But the truth is not "no opinion," it's **"two informed views
disagree,"** and that's a completely different and much more useful state.

The backtest later proved this quantitatively: the two models have *opposite
signs of alpha*. Chase loses ~1.5% per trade; fade gains. Averaging them would
have blended a profitable strategy with a losing one and produced mush.

There's a portfolio-level version of the same lesson. A single test run that
admitted **both** models' signals into one shared book lost ~22% - because the
losing model's trades consumed the same capital as the winning model's. Choosing
which signal to size is a separate problem from scoring each signal, and
conflating them is expensive.

---

### 4.3 The risk gate: from theatre to enforcement, then red-teamed

**The starting point.** As described in §1, the original pattern in this space is
a `confirmed=true` parameter that the LLM sets itself. finLM replaced it with:

1. **A mandate file** (`risk_limits.json`) specifying caps - max order value, max
   quantity, max orders per day, allowed products and exchanges, symbol
   allow/blocklists. **The human writes this file. The code never creates it.**
   If it's missing, orders are blocked, not defaulted open. This is called
   **failing closed**, and it's the opposite of most software, which fails open
   for convenience.
2. **A kill switch** - create a file named `KILL_SWITCH` and every order is
   refused until you delete it. No API, no LLM involvement, works even if the
   model is actively malfunctioning.
3. **No bypass parameter.** There is no argument that skips the check.

That's a genuinely good design. **And on 9 August it was still breakable four
ways.**

The exercise: assume the model is adversarial and try to get an order through.
Every input it controls is an argument to `place_order`, so those are the attack
surface.

#### Bypass 1 - lying about the price (the serious one)

The gate checks order value against a rupee cap. To compute the value it needs a
price. The code used the caller-supplied `price` argument when present.

But `price` is only meaningful for a **limit order** ("buy at ₹400 or better").
For a **market order** ("buy at whatever it costs right now"), the exchange
ignores `price` entirely.

So the model could send:

```
place_order(RELIANCE, BUY, quantity=100, order_type="MARKET", price=1.0)
```

The gate computes 100 × ₹1 = **₹100**, sails under a ₹50,000 cap, and the order
executes at the real market price of ~₹5,000 - **₹500,000, ten times the
mandate.**

**The fix.** A caller-supplied price is trusted *only* where it genuinely bounds
the fill: a BUY LIMIT or BUY SL order, which cannot execute above their stated
price. Everywhere else the gate requires a live market quote, and refuses if it
can't get one. The rule is enforced inside the gate itself, not just at the call
site, so a future edit that re-introduces the bug is caught.

#### Bypass 2 - negative quantity

```
quantity = -100
```

Check one: is `-100 > max_quantity (100)`? No. Passes.
Check two: is `-100 × ₹5,000 > ₹50,000`? That's −₹500,000, which is not greater
than ₹50,000. Passes.

One argument defeated both caps, because both were written assuming positive
numbers. Fixed by validating that quantity is a positive whole number *before*
any cap arithmetic runs.

#### Bypass 3 - invisible characters in the symbol

The blocklist compared uppercased symbols. But:

- `"YES BANK"` - with a space - isn't `"YESBANK"`
- `"YES​BANK"` - with a **zero-width space**, invisible in every log and diff -
  isn't `"YESBANK"`, and Python doesn't consider U+200B whitespace
- `"ＹＥＳＢＡＮＫ"` - full-width characters - uppercases to itself, never matching
  ASCII

Four confirmed evasions. Fixed with Unicode NFKC normalisation plus stripping
space, format and control characters. A blocklist is only as good as its
comparison.

#### Bypass 4 - NaN

`NaN` ("not a number") has a property that breaks bounds checks: **every
comparison with it is false.** So `100 × NaN > 50,000` evaluates to false, and
the order passes the cap. Fixed by requiring a positive finite price.

#### The fifth hole, found by an outside review

Per-order caps don't bound daily exposure. Five orders of ₹49,999 each clear a
₹50,000 per-order cap one at a time. (The existing daily *count* cap limited the
damage to 5×, so this was less severe than first claimed - but the gate had no
rupee ledger at all.) Fixed by adding `max_daily_value_inr`, summing the order
ledger that was already being written but never read.

#### What made this worth doing

**The four bypasses are one bug wearing four hats: the gate trusted
caller-supplied values as ground truth.** LLM-supplied price, the sign of
quantity, the string form of a symbol, the finiteness of a float.

This is a textbook **confused deputy** problem (Hardy, 1988): a program holding
authority the caller lacks, tricked into exercising it on the caller's behalf
because it trusted the caller's description of the request.

The honest framing is also the stronger one: not "our gate is unbypassable," but
**"we attacked our own gate, found four holes, fixed them, and here is the
58-test suite that keeps them shut."** The first is a claim. The second is
evidence.

Two of those tests guard the thesis directly: one asserts `place_order` exposes
no parameter with a bypass-shaped name (so `confirmed=true` can never come back),
and one greps the package to assert no code path ever writes the mandate file.

---

### 4.4 The headline number that couldn't be reproduced

**What happened.** The project's most-quoted result read:

> Best config: pullback-entry fade. **935 trades, 52.6% win rate, +1.2% alpha per
> trade, profit factor 1.18**, improved out-of-sample.

On 9 August it was re-run with the documented settings. It produced:

> **754 trades, 50.9% win rate, +0.98% alpha (gross), profit factor 1.14** - and
> net of realistic costs, **+0.58% and profit factor 1.02.**

Not catastrophically different, but not the same, and the trade count was off by
181.

**The investigation.** Eighteen parameter combinations were swept. None
reproduced all four statistics together. Then the git history was checked
directly against the commit that produced the original numbers:

- the candidate-selection function is **behaviourally identical** - the old
  hardcoded constants hold exactly the values the new config object defaults to
- the entry-fill logic and exit defaults are **identical**
- corporate-action adjustment moves the trade count the *wrong way* (it raises
  it)
- the regime analysis shares the same code path, so there's no forked engine

**The conclusion.** Not a code regression. The most likely explanation is that
the original run was never captured - no config dump, no output file, no record
of which database snapshot it saw. **A number was read off a terminal and typed
into a markdown file.**

**Why this is the most useful failure in the project.** The analysis was sound;
the bookkeeping wasn't. And it's the exact failure the project's own pitch
describes - agents leaving chat logs instead of records - reproduced in the
team's own workflow.

**The fix - `provenance.py`.** Every result-producing run now writes a JSON
artifact stamping: git commit **and whether the working tree was dirty** (a
clean SHA doesn't identify code that ran from a modified tree), a database
fingerprint (row count, date range, distinct symbols, size, SHA-256), interpreter
and library versions, the full config, and the results.

It is **not optional.** There's a test asserting no `--no-artifact` flag exists,
because the 2 a.m. version of anyone will use it.

Docs now cite artifact paths instead of transcribing numbers.

**How the correction was published.** The old figures were kept, struck through,
with a dated correction block explaining exactly what didn't reproduce and what
was ruled out. Deleting them would have been easier and much worse - the
correction trail *is* the credibility.

*(Postscript: this same class of error recurred within hours. See §4.7.)*

---

### 4.5 Survivorship: the audit that could have killed the headline

An outside review raised the sharpest possible objection to the eleven-year
result:

> Mean reversion on survivors is not a strategy - it's knowing the outcome. And
> survivorship bias produces *11-of-11 positive years* as its signature. A
> contaminated sample doesn't give you a noisy positive result; it gives you a
> suspiciously clean one.

Exactly right, and it would have invalidated the project's headline finding. So
it was checked empirically rather than argued about.

**The result: clean.** The database is built from per-day bhavcopy archives, so
every row is what actually traded that day.

- 2,543 distinct symbols across 2010-2020
- **771 of them stop appearing before June 2020** - they delisted or died
- 1,023 first appear after 2011 - new listings, arriving when they listed

And the dead are present, ending at their real demise:

| symbol | last seen | what happened |
|---|---|---|
| `SATYAMCOMP` | 2013-07-03 | Satyam - India's largest accounting fraud - absorbed into Tech Mahindra |
| `EDUCOMP` | 2017-09-22 | collapsed under debt |
| `GITANJALI` | 2018-07-09 | PNB fraud, ₹14,000 crore |
| `AMTEKAUTO` | 2018-03-28 | insolvency |
| `JETAIRWAYS` | 2019-09-23 | grounded April 2019 |
| `UNITECH` | 2020-03-09 | fraud, promoters jailed |

A symbol list backfilled from today cannot produce that pattern.

**The lesson:** this took one SQL query. The temptation with an objection that
scary is to argue about likelihood. Measuring took minutes and produced something
much better than a rebuttal - a fact you can state on a slide with six dead
companies named.

---

### 4.6 The pre-registered hypothesis that was falsified

**The puzzle.** Same strategy, same code, same cost model:

- 2010-2020: **+2.32%** alpha per trade, net of costs, 8,405 trades
- 2025-2026: **+0.58%** alpha per trade, net of costs

A 4× decay. Why?

**The candidate explanation.** The regime study had found fade alpha correlated
with **market breadth** (the fraction of stocks rising on an average day) at
r = −0.64: *narrow markets fade better.* If the recent window happened to be a
broad market, that would explain the decay.

**Why this needed care.** With 11 data points and a wide prediction band, you can
look at almost any new observation and call it "consistent." The temptation is
overwhelming and nearly invisible.

**So the prediction was written down first.** Before computing recent breadth,
a pre-registration file recorded:

- the fitted model: `alpha = 12.481 − 0.2002 × breadth`
- the 95% **prediction interval for a new observation** (not the much narrower
  confidence interval on the mean - using the wrong one would let a miss look
  like a hit)
- **the falsification region**: +0.58% is only consistent with the model if
  breadth ≥ **53.30%**. Anything from 47.62% to 53.30% - about **94% of the
  historical range** - contradicts it.

That last line is the one that matters. It establishes the test *could* fail.

**Then the observation.** Live-window breadth: **48.35%.**

| | |
|---|---|
| observed breadth | 48.35% - inside the historical range, so not an extrapolation |
| model predicted | **+2.80%** |
| 95% prediction interval | **[+1.57%, +4.04%]** |
| actually observed | **+0.58%** - outside, by 0.99pp |
| verdict | **CONTRADICTED** |

And the miss is **directional**, which is worse for the hypothesis than a large
miss would be: 48.35% is near the *low* end of the range, and the slope is
negative, so the model says this should have been one of the *best* fade regimes
on record. It was the worst measured.

**Outcome:** breadth is dead as an explanation. The r = −0.64 correlation was
correctly labelled "a lead, not a result" - the lead was tested and it died.

**Why a negative result is a good outcome.** Anyone can explain a result after
seeing it. Almost nobody sets up a test they can lose. The pre-registration file
is timestamped before the observation, and both are in `artifacts/`.

#### The sequel: filling the hole, and resolving it

The real problem was never the missing explanation - it was that the comparison
spanned **two disjoint windows with a five-year hole between them.** 2010-2020,
then nothing, then 2025-2026. Any story fits a gap that size.

Both ingest paths already existed, so closing it was a download, not a research
project: `market_historical.db` went from 4.15M rows to **5.83M, continuous
2010-01 → 2024-07.**

The analysis was pre-registered again - four hypotheses (monotonic decay,
structural break, unusual window, none), decision rules, candidate break dates
named in advance, 2024 excluded as a partial year, 2020 run both ways with
disagreement forced to "inconclusive", and a stopping rule.

The remaining hole (mid-2024 → mid-2025) was closed too, by running the *other*
ingest path, and the two databases merged into one continuous series:
**7,061,494 rows, 2010-01-04 → 2026-08-07, zero gaps.**

That merge needed its own check first. Two ingest paths meeting mid-series is
exactly where a **schema artifact can masquerade as a finding** - and the decay
trend crosses that seam. So before using it: symbol carry-over across the
boundary was 100% (2,167 of 2,168), rows/day ratio 1.04, median close 1.08,
volume 0.97, turnover 1.03, series mix near-identical, daily returns ordinary,
no volatility jump. Clean.

**Result - the decay is real, monotonic, and significant.**

| 2010 | 2013 | 2016 | 2019 | **2021** | **2022** | **2023** | **2024** | **2025** |
|---|---|---|---|---|---|---|---|---|
| 2.27% | 1.90% | 1.87% | 2.54% | **1.13%** | **1.41%** | **0.35%** | **1.23%** | **0.79%** |

Over 16 complete years: Spearman r = **−0.653 (p = 0.0013)**; excluding 2020,
r = −0.707 (p = 0.0003). Slope ≈ **−0.12pp/year**, stable across every
specification - only the significance strengthens as the sample grows. All
leave-one-out slopes negative, both COVID treatments agreeing.

The structural-break test fired in the primary run - best split before **2023**,
which is when **T+1 settlement completed**, an event named in the pre-registration
*before* the data was seen. But it fails the COVID robustness check (p = 0.0503),
so it's reported as suggestive rather than established. At this sample size a
gradual decay and a step at T+1 can't be cleanly separated.

And the live window's +0.58% stops being an anomaly: it's simply where the trend
was already heading.

**The lesson buried in this one - a statistic that improved as the finding got
worse.** The project's headline was a sign test: 11 of 11 years positive,
p = 1/2048. Extended to 16 years, all still positive, so **p improves to
1/65,536 - 32× more significant** - while the effect size fell about 78%.

The sign test measures whether the *sign* is real. It says nothing about
magnitude. Fourteen years of +0.1% would score identically. So the more
impressive-looking number now describes a strategy that is dying.

That is exactly why the headline changed from "positive every year" to
"positive every year, **decaying ~0.12pp annually**." An anomaly that erodes as a
market matures is what theory predicts, and it's a more interesting finding than
stable alpha would have been - but only if you say the second half out loud.

---

### 4.7 (Bonus) The anomaly study - and the same mistake, again

**The idea.** The project's engine can test *any* rule, not just its own. In
finance there are hundreds of published "anomalies" - patterns claimed to predict
returns. Nearly all were documented on US data. Almost nobody has tested them
properly on Indian markets.

So: take eleven well-known anomalies (momentum, short-term reversal, the
52-week-high effect, low volatility, the lottery/MAX effect, illiquidity,
turnover, beta, volume shocks, skewness, long-term reversal) and run them all.

**Methodological note, because it's the kind of thing that invalidates results:**
this needed a *second* engine. The existing backtest is event-driven - screen,
signal, ATR exit. That's a *trading rule.* The anomaly literature uses
**cross-sectional portfolio sorts**: rank every stock by a characteristic each
month, buy the top fifth, sell the bottom fifth, measure the spread. Testing
momentum through an ATR-exit engine measures the exit rule, and produces a number
comparable to nothing published. So `anomalies.py` implements the standard
method and shares only the data layer.

**First results (2010-2020, liquid universe): 0 of 11 survived.** And the *gross*
statistics were insignificant too, so it wasn't a cost story - the effects mostly
weren't there.

**The interesting part was the universe sweep.** Restricting to the ~134 most
liquid stocks killed everything. Widening to ~445 stocks brought momentum
(t = 2.28) and the 52-week-high effect (t = 2.57) to life, decaying monotonically
across tiers.

A **conditional double sort** - ranking each characteristic *within* liquidity
buckets - confirmed these aren't liquidity in disguise: momentum 2.28 → 2.14,
52-week-high 2.57 → 2.53. Magnitudes shrank (momentum's +12.8% → +8.2%, so about
a third of the raw spread *was* liquidity), but the effect survived.

Two findings worth keeping:

- **Short-term reversal is exactly null at monthly horizon** (gross 0.000%/month,
  t = −0.00). This doesn't contradict the project's fade result - it *locates*
  it. The reversal effect in India lives at the **daily/event** horizon and does
  not exist at monthly formation. Sharper than either finding alone.
- **Skewness runs backwards vs the US.** Positively-skewed "lottery" stocks
  *outperformed*, the reverse of the published finding. Trust this least -
  monthly skew from ~20 daily observations is a noisy estimator.

**Then the multiple-testing problem.** Eleven anomalies × three universes = 33
tests. At 5% significance you expect ~1.7 false positives from pure noise. So
several of those results might be nothing.

Again, the correction was **pre-registered first**, including an explicit
**stopping rule** - a deliberate guard against infinite regress, because every
robustness check suggests another one and a project can polish itself to death:

> If 0 anomalies survive: report the null and stop. No block bootstrap, no
> alternative estimator, no re-specified characteristic, no extra universe tiers.

**And then the same provenance mistake happened again.** The FDR run was
launched while the 2021-2024 backfill was still writing to the same database. It
read a moving dataset - which is why momentum's t-statistic came out 2.79 there
versus 2.28 in the original study. **Different sample, not different method.**
The database-fingerprint stamp built in §4.4 is what caught it; without it the
discrepancy would have looked like a methodology bug and cost hours.

The concurrent write also crashed the ingest with `database is locked`.

Both were redone on a frozen, complete dataset. **The lesson repeated itself
within hours of the infrastructure being built to catch it** - infrastructure
doesn't prevent the mistake, it makes the mistake *visible*.

#### The rerun, and a second wrong prediction

On the frozen 2010-01 → 2024-07 data (290,651 symbol-months, **161 usable months
instead of 118**), the answer changed:

- **BH at q = 0.10: three anomalies survive** - momentum, the 52-week-high
  effect, and skewness. Computed on both gross and net p-values; both give the
  same three.
- **Bootstrap max-|t|: observed 3.374 vs a null 95th percentile of 2.828,
  p = 0.012.** The strict test - the one that accounts for having run eleven
  tests at once - passes.

The pre-registration had predicted **0-1 survivors and a non-significant
bootstrap.** Wrong again, and the reason matters: *the method didn't change, the
sample grew 36%.* The earlier null was a **power** problem, exactly as the "we
cannot detect it, not it isn't there" caveat had warned. Recording that caveat
honestly is what made the later result interpretable rather than contradictory.

One reading trap worth spelling out. Skewness is statistically significant but
**not a tradeable edge**: its significance is significance of *losing money*
(−8.6%/yr as signed). Flip it to the profitable direction, pay the costs again,
and it's roughly +0.8%/yr - nil. Only momentum and the 52-week-high effect are
both significant *and* economically large, at ~+15%/yr net in the broad universe.

A significant t-statistic and a tradeable strategy are different claims, and the
gap between them is where most retail backtesting dies.

---

## 5. The one lesson underneath all of them

Line the incidents up:

| incident | what went wrong |
|---|---|
| Tavily's invented earnings report | asserted a fact it hadn't verified |
| The `confirmed=true` pattern | asserted consent the user never gave |
| Four gate bypasses | trusted the caller's *description* of the request |
| Tool count wrong in four docs | asserted a capability list nobody checked |
| `scan_accumulation` | asserted a screen with zero evidence behind it |
| The 935-trade headline | asserted a number with no link to the run |
| The breadth hypothesis | asserted a mechanism nobody had tested |
| The FDR rerun | asserted a comparison across a dataset that had moved |

**They are all the same error: a claim asserted by hand, unverified against the
artifact that would prove it.**

And in every case the fix has the same shape - make the check **structural rather
than remembered**:

- the gate constructs its own trusted values instead of accepting the caller's
- a test asserts the documented tool list matches the registered tools
- the unvalidated screen declares its status **in the data it returns**, not just
  in its docstring, because the docstring is for humans reading source and the
  payload is what actually reaches the model
- every run writes a stamped artifact, with no opt-out flag
- hypotheses are pre-registered with a falsification region before observation
- the scope itself has a pre-registered stopping rule

That's the actual thesis of this project, and it's bigger than trading: **an
agent system should be built so that its claims are checkable by construction,
because neither the model nor the human will reliably check them by hand.**

The project demonstrates it in the only domain where it's cheap to prove -
markets return unambiguous ground truth on a schedule, so governance claims can
be *tested* rather than asserted.

---

## 6. How to run everything

```bash
cd finLM
.venv/Scripts/python -m pytest -q          # 114 tests
```

**Data:**
```bash
llmfin-ingest                              # recent daily bhavcopy → market.db
python -m llmfin.historical_ingest --start 2010-01-01 --end 2020-12-31
```
⚠ Do not run analysis against a database while an ingest is writing to it -
SQLite will lock, and any analysis that *does* read will silently use a moving
dataset. (§4.7, learned the hard way.)

**Research:**
```bash
llmfin-backtest --entry pullback --stop 2.0 --target 2.5 --cost-pct 0.4
llmfin-regime-analysis --db ~/.llmfin/market_historical.db --start-year 2010 --end-year 2020
llmfin-replicate --db ~/.llmfin/market_historical.db      # anomaly study
llmfin-portfolio-backtest
```
Every one writes a stamped artifact to `artifacts/`. **Cite the artifact, never
retype the number.**

**The MCP server:**
```bash
llmfin-server            # stdio, for desktop / CLI MCP clients
llmfin-server --http     # streamable HTTP on 127.0.0.1
```

**To enable live trading** (deliberately manual): copy
`risk_limits.example.json` to `risk_limits.json` and edit it yourself. Nothing in
the codebase will create that file for you - that's the point. To freeze trading
instantly, create a file named `KILL_SWITCH` next to it.

---

## 7. Glossary

**Alpha** - return not explained by market exposure. The part that's skill.
**ATR** - Average True Range; typical daily price movement, used to scale stops.
**Backtest** - simulating a strategy on historical data.
**Beta** - sensitivity to overall market moves.
**Bhavcopy** - NSE's daily end-of-day CSV of every stock that traded.
**Breadth** - fraction of stocks rising on a given day.
**Corporate action** - split, bonus, or dividend that changes the share price mechanically.
**Drawdown** - peak-to-trough decline.
**EOD** - end of day; one price bar per day.
**Fade** - bet a move reverses.
**FDR** - False Discovery Rate; correction for running many tests at once.
**Fail closed** - when unsure, refuse. The opposite of most software.
**F&O** - Futures & Options; the derivatives segment, ~180 eligible stocks.
**Kill switch** - a file whose existence halts all trading.
**Lookahead bias** - using information that wasn't available at decision time.
**Long / short** - betting a price rises / falls.
**Mandate** - the human-written file defining what the agent may do.
**MCP** - Model Context Protocol; the standard for LLM tool calls.
**Mean reversion** - the belief that extreme moves snap back.
**Momentum** - the belief that trends persist.
**Newey-West** - a standard-error correction for overlapping, serially-correlated observations.
**NSE** - National Stock Exchange of India.
**Point-in-time** - using only data available on the simulated date.
**Pre-registration** - writing your prediction and falsification criteria before looking.
**Profit factor** - gross profit ÷ gross loss. Below 1.0 loses money.
**Quintile sort** - rank a universe, split into fifths, compare top and bottom.
**Sharpe ratio** - return per unit of volatility.
**Sign test** - nonparametric test on how often something is positive.
**Slippage** - the gap between the price you wanted and the price you got.
**STT** - Securities Transaction Tax.
**Survivorship bias** - analysing only entities that survived to today.
**Turnover** (portfolio) - how much of a portfolio is replaced each rebalance.
**Turnover** (stock) - rupee value traded.
