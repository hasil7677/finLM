# finLM

**Point-in-time equity research infrastructure for Indian markets** — 16 years of NSE
data, a deterministic analysis layer exposed to LLMs over the Model Context Protocol,
and a risk gate that makes execution auditable.

```
llmfin-replicate --db ~/.llmfin/market_full.db      # anomaly replication study
llmfin-backtest  --entry pullback --cost-pct 0.4    # point-in-time backtest
llmfin-server                                       # MCP server, 15 tools
```

---

## Read this first — what has and has not been run

This project is **research infrastructure and a governance layer**, not a live trading
system. Being precise about that:

| | |
|---|---|
| Backtests over 2010–2026 NSE data | ✅ real, point-in-time, survivorship-verified |
| Decisions journaled with reasoning, scored against real NSE closes | ✅ real — 13 logged |
| Broker integration (Zerodha Kite) implemented | ✅ code exists, unit-tested against a mock |
| **Orders actually placed** | ❌ **zero. Ever.** |
| **Broker account authenticated** | ❌ never — no session token has existed |
| **Capital at risk** | ❌ none |

**No order has ever been placed, because the mandate file that would authorise one was
deliberately never created.** The gate refused every time. That is the design working,
not a missing feature — and it is the most direct demonstration of the thesis in the
repo.

Where you see a return figure (e.g. "+10.31% on the top call"), it is a **paper
decision scored against real closing prices**. Real data, real forward scoring, no
money.

---

## What the data says

Everything below is reproducible from this repo. Every run writes a stamped artifact to
`artifacts/` recording the git commit, the database fingerprint, library versions and
full config — **cite the artifact, don't retype the number.**

### 1. Chasing breakouts destroys value; fading them worked, and is dying

Across **2010–2025, 16 complete years**, a "fade the mover" strategy (short a stock
that just spiked on volume, ATR-based stop and target, 10-day horizon) produced
positive benchmark-adjusted alpha in **every single year**, net of a modelled 0.4%
round-trip cost.

But the effect is **decaying monotonically**:

| 2010 | 2013 | 2016 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|
| 2.27% | 1.90% | 1.87% | 2.54% | 2.39% | 1.13% | 1.41% | 0.35% | 1.23% | 0.79% |

- Spearman r = **−0.653, p = 0.0013** (excluding 2020: −0.707, p = 0.0003)
- Slope ≈ **−0.12 percentage points per year**, stable across every specification
- All leave-one-out slopes negative; both COVID treatments agree

A structural-break test put the best split before **2023** — when T+1 settlement
completed, an event named in the pre-registration *before* the data was examined —
but at p = 0.0375 it fails the robustness check (p = 0.0503 excluding 2020), so it is
reported as **suggestive, not established**.

**A caution on the headline statistic.** The sign test is now 16/16 positive,
p = 1/65,536. That is 32× more "significant" than the original 11/11 result — *while
the effect size fell ~78%*. A sign test measures whether the sign is real and says
nothing about magnitude; 16 years of +0.1% would score identically. **The honest
headline is the decay, not the p-value.**

The mirror-image finding is cleaner and needs no caveats: **chasing** breakouts loses
in every configuration tested — roughly **−1.55% per trade net**, n = 1,033.

### 2. Anomaly replication — most published effects don't show up here

Eleven documented equity anomalies (momentum, short-term reversal, 52-week-high,
low-volatility, MAX/lottery, illiquidity, turnover, beta, volume shock, skewness,
long-term reversal) tested on NSE 2010–2024 using standard cross-sectional quintile
portfolio sorts — 290,651 symbol-months, 161 usable months.

**In the liquid, tradeable universe (~134 names/month), essentially nothing survives.**
Widen the universe and two effects appear and hold up:

| anomaly | wide (~445 names) | mid (~234) | liquid (~134) |
|---|---|---|---|
| **momentum (12-1)** | **t = 3.37 / +15.3%** | 2.19 / +9.8% | 1.72 / +6.5% |
| **52-week high** | **t = 3.05 / +15.3%** | 1.87 / +8.1% | 1.29 / +5.1% |
| skewness | t = −2.43 / −8.6% | −3.15 / −11.6% | −1.43 / −8.7% |
| low volatility | 1.80 / +6.1% | 0.50 / −0.4% | 0.33 / −1.0% |
| short-term reversal | −0.92 / −7.9% | −0.13 / −4.5% | −0.22 / −5.1% |

*(gross Newey-West t / annualised net %; the other six are all |t| < 1.3)*

Corrected for multiple testing: **Benjamini-Hochberg at q = 0.10 leaves three
survivors**, and a **bootstrap of the maximum |t| under the null** (returns permuted
within month, 1,000 iterations) gives observed 3.374 vs a null 95th percentile of
2.828, **p = 0.012**. The strict test passes.

Two things worth reading carefully:

- **Skewness is significant but not tradeable.** Its significance is significance of
  *losing money*. Flipped to the profitable direction and re-costed it is ~+0.8%/yr —
  nil. Only momentum and 52-week-high are both significant *and* economically large.
- **Short-term reversal is exactly null at monthly horizon** (gross 0.000%/mo). This
  doesn't contradict finding #1 — it *locates* it. Reversal in India lives at the
  daily/event horizon and does not exist at monthly formation.

A **conditional double sort** (ranking each characteristic *within* liquidity terciles)
confirms these aren't liquidity in disguise: momentum 3.37 → holds, 52-week-high
3.05 → holds. Magnitudes shrink by roughly a third; significance survives.

### 3. The cost hurdle, which is larger than people expect

A **zero-alpha** quintile long-short at ~80% monthly turnover and 0.4% round-trip loses
**6.6% per year** purely to costs. That is the bar every result above has to clear, and
it is asserted as a test rather than left as a footnote.

---

## Why these numbers are trustworthy

The methodology is the actual product. Five things this repo does that most backtests
don't:

**Point-in-time, structurally.** Entries are at the *next day's open* — bhavcopy is
end-of-day, so you cannot act on today's close. The candidate scanner physically
filters the panel to prior rows rather than relying on discipline.

**Survivorship-free, and verified.** The universe is built from per-day NSE bhavcopy
archives, so each row is what actually traded that day. Checked empirically: 2,543
symbols over 2010–2020, of which **771 stop appearing before mid-2020**. The dead are
present and end at their real demise — `SATYAMCOMP` → 2013-07-03, `EDUCOMP` →
2017-09-22, `GITANJALI` → 2018-07-09, `AMTEKAUTO` → 2018-03-28, `JETAIRWAYS` →
2019-09-23, `UNITECH` → 2020-03-09. A symbol list backfilled from today cannot produce
that pattern.

**Benchmark-adjusted.** Every return is measured against an equal-weight liquid-universe
index over the same holding period. A number without a comparator is uninterpretable.

**Costs modelled, not discounted mentally.** `ExitConfig.cost_pct` is subtracted from
every trade; `stats()` reports gross and net side by side.

**Corporate actions back-adjusted, with the audit trail exposed.** Raw bhavcopy has no
split adjustment, so a 1:2 split looks like a −50% crash. The adjuster took two rounds
of real bugs to get right (see [PROJECT_DEEP_DIVE.md](./PROJECT_DEEP_DIVE.md) §4.1) and
every decision it makes is queryable via `list_data_anomalies`.

### Hypotheses are pre-registered, and one was falsified on purpose

`artifacts/prereg_breadth.py` recorded, **before** the data was computed, a prediction
interval and an explicit falsification region for a hypothesis about why fade alpha
varies. The observation landed **outside** the interval, in the direction opposite to
the prediction. The hypothesis was recorded as dead.

The same discipline was applied to the multiple-testing correction and to the decay
analysis — including a **stopping rule** fixed in advance, to prevent an infinite
regress of robustness checks.

### Reproducibility is enforced, after it failed once

An earlier headline figure (935 trades, +1.2% alpha) **could not be reproduced**.
Eighteen parameter combinations were swept; the git history showed the candidate
selection, entry fill and exit defaults were byte-identical to the commit that produced
it. The conclusion: the original run was never captured — a number was read off a
terminal and typed into a document.

`provenance.py` now stamps every run with git SHA *and dirty-tree flag*, a database
fingerprint (rows, date range, SHA-256), library versions and full config. There is a
test asserting no opt-out flag exists.

The superseded figures were kept with a dated correction rather than quietly replaced.

---

## The governance layer

An LLM given a `place_order` tool and an instruction to "always ask first" is not
governed. The reference LLM trading server in India gates real orders behind a
`confirmed=true` parameter **that the model sets itself**. That is theatre.

finLM's answer:

1. **A mandate file** (`risk_limits.json`) the *human* writes, specifying order value
   cap, daily rupee cap, quantity cap, daily order count, allowed products, exchanges,
   and symbol allow/blocklists. **No code path in the package can create it** —
   asserted by a test. Missing file ⇒ orders blocked, not defaulted open.
2. **A filesystem kill switch** — create a file named `KILL_SWITCH` and every order is
   refused until it's deleted. No API, works even if the model is misbehaving.
3. **No bypass parameter**, asserted by a test that fails if anyone adds an argument
   with a bypass-shaped name.

### It was red-teamed, and it lost four times

Assume the model is adversarial. Every input it controls is a `place_order` argument.
Four real bypasses were found and fixed:

| bypass | how it worked |
|---|---|
| **Price spoofing** | `order_type="MARKET", price=1.0, quantity=100` — the gate valued the order at ₹100 and let it through a ₹50,000 cap, while it executed at market for ₹500,000. The exchange ignores `price` on a market order; the gate didn't. |
| **Negative quantity** | `quantity=-100` passes `qty > max_qty` *and* makes `qty × price` negative, clearing both caps at once. |
| **Unicode evasion** | `"YES​BANK"` with a zero-width space, or full-width `ＹＥＳＢＡＮＫ`, slid past a blocklist entry for `YESBANK`. |
| **NaN price** | every comparison with `NaN` is false, so `100 × NaN > 50,000` evaluated false and cleared the cap. |

Plus a fifth found in review: per-order caps don't bound daily exposure, so
`max_daily_value_inr` now sums the order ledger.

All four are one bug wearing four hats — **the gate trusted caller-supplied values as
ground truth.** That is the classic *confused deputy* problem: a program holding
authority the caller lacks, tricked into exercising it because it believed the caller's
description of the request.

The honest claim isn't "our gate is unbypassable." It's **"we attacked our own gate,
found four holes, fixed them, and here is the suite that keeps them shut."**

---

## Setup

```bash
git clone https://github.com/hasil7677/finLLM && cd finLLM
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate on Unix
pip install -e ".[dev]"
cp .env.example .env              # optional: TAVILY_API_KEY for catalyst research
llmfin-ingest                     # build the market DB (~5 min, no accounts needed)
pytest -q                         # 120 tests
```

**Runs with zero credentials** on free public NSE data. API keys only unlock the live
tier (quotes, positions, orders).

Data lives in `~/.llmfin/` (`market.db`, `market_historical.db`, `journal.db`) — not in
the repo, since the databases are large.

### Historical data

```bash
python -m llmfin.historical_ingest --start 2010-01-01 --end 2024-07-31   # old format
llmfin-ingest                                                            # UDiFF, 2024-07 onward
```

⚠ **Never run an analysis against a database while an ingest is writing to it.** SQLite
will lock and kill the ingest, and worse, any analysis that *does* read will silently
use a moving dataset. This cost real debugging time — see PROJECT_DEEP_DIVE.md §4.7.

### Exposing the tools to an MCP client

The server speaks MCP over stdio, or streamable HTTP:

```bash
llmfin-server                     # stdio
llmfin-server --http              # http://127.0.0.1:8747/mcp
```

`mcp_client_config.json` is a ready-made stdio config to merge into your client's
config file. Some desktop clients rewrite their config on exit — if yours does, use
`--http` and add the URL as a connector instead.

### Enabling order placement (deliberately manual)

```bash
cp risk_limits.example.json risk_limits.json    # then EDIT IT YOURSELF
python -m llmfin.auth                           # Kite login, daily
```

Nothing in this codebase will create `risk_limits.json` for you. That is the entire
point — it's consent that has to originate outside the model's reach. To freeze
trading instantly, create a file named `KILL_SWITCH` next to it.

---

## The 15 MCP tools

**Discovery** (free, local data) — `ingest_market_data`, `scan_market`,
`scan_accumulation`¹, `list_data_anomalies`
**Analysis** (free; upgrades with a broker session) — `research_instrument`,
`get_batch_research`, `research_symbol`
**Journal** (the feedback loop) — `log_decision`, `eod_review`, `get_journal`
**Broker** (requires a Kite session) — `get_market_quote`, `search_instruments`,
`get_portfolio_positions`, `place_order`, `get_risk_status`

¹ `scan_accumulation` is **UNVALIDATED** — it has never been through the backtest loop
and declares that in its own returned payload, not just its docstring.

A test asserts the tool list in the source matches the tools actually registered, and
that documented counts across the repo match reality. It caught a real drift.

---

## Design decisions that look like bugs and aren't

**The two alpha models are never averaged.** Summing momentum and mean-reversion scores
is self-cancelling — a strong uptrend scores +1 trend, −1 overbought = HOLD, which
structurally HOLDs exactly the stocks worth trading. They are opposing philosophies;
both opinions are shown and the disagreement is the signal. The backtest confirms it:
they have *opposite signs of alpha*.

**Indicators are hand-rolled, not `pandas_ta`.** That library is unmaintained and breaks
on numpy ≥ 2. Hand-rolling is why this runs on Python 3.14.

**Two data sources with silent fallback.** A broker session upgrades data quality when
present; the free path always works. Screening never requires a paid subscription.

**Broker tokens expire on the IST day boundary**, not UTC. Comparing UTC dates marks
dead tokens as fresh.

**Web research trusts sources, not summaries.** The research provider's synthesised
answer once invented a "positive earnings report" that didn't exist.

---

## Known limits — read before trusting anything

- **The measured edge is ~97% short-side.** An Indian retail *cash* account cannot hold
  a multi-day short. Trading this requires futures, which restricts the universe to
  ~180 names and adds roll costs that are **not modelled**. For a cash account the
  honest use is a **de-risking filter** — don't buy the spike — not a return strategy.
- **The edge is decaying** and was down to 0.79% in 2025. Extrapolating the trend, it
  reaches zero within a few years.
- **No market capitalisation data.** Bhavcopy has no shares outstanding, so there is no
  value-weighting and no true size factor. Turnover is a liquidity proxy, not size —
  part of the universe gradient could be size in disguise.
- **No intraday data.** EOD only: no spreads, no order book, no execution modelling.
- **Factor attribution has not been done.** Momentum and 52-week-high have not been
  regressed on size/value/volatility factors. Until that lands they are candidate
  findings, not established alpha.
- **Long-side screening is unvalidated** (`scan_accumulation`).
- **A ~11-month data seam** exists in raw form between the two ingest formats; the
  merged `market_full.db` closes it, and the boundary was validated (100% symbol
  carry-over, no price/volume discontinuity) before use.

---

## Repository layout

```
src/llmfin/
  server.py             MCP server, 15 tools (FastMCP), stdio + --http
  data_store.py         NSE bhavcopy → SQLite (UDiFF format, 2024-07 onward)
  historical_ingest.py  Pre-2024 bhavcopy backfill (separate DB)
  corporate_actions.py  Split/bonus back-adjustment, shared by backtest + live
  scanner.py            Deterministic whole-universe mover screen
  accumulation_scanner.py  Long-side screen — UNVALIDATED
  indicators.py         RSI/MACD/Bollinger/EMA/ATR, hand-rolled
  signals.py            Trend-following + mean-reversion models, never averaged
  market_research.py    Per-symbol pipeline
  research_web.py       Catalyst search ("why did it move")
  journal.py            Decision log with thesis + outcome scoring
  risk.py               Mandate, kill switch, caps — the governance core
  backtest.py           Point-in-time event simulator
  anomalies.py          Cross-sectional portfolio-sort engine + FDR/bootstrap
  portfolio_backtest.py Shared-book sequencing, sizing, drawdown
  regime_analysis.py    Per-year alpha vs volatility/breadth
  provenance.py         Run artifacts — git SHA, data fingerprint, config
  diagnostics.py        Corporate-action audit trail
tests/                  120 tests
artifacts/              Stamped run outputs + pre-registrations
```

**[PROJECT_DEEP_DIVE.md](./PROJECT_DEEP_DIVE.md)** — a long-form walkthrough written for
readers with no trading background: the concepts from zero, the project timeline, and
deep dives on every incident (the adjuster bugs, the gate bypasses, the unreproducible
headline, the falsified hypothesis, the anomaly study).

---

## ⚠️ Disclaimer

**Not investment advice.** This is research and engineering work, published for
inspection. Nothing here is a recommendation to buy or sell any security. The strategies
described are demonstrably decaying, largely untradeable in a retail cash account, and
have never been executed with real money. Backtested results are not indicative of
future returns. If you connect this to a funded brokerage account, you do so entirely at
your own risk.
