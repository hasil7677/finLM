# finLM — the intelligence layer that won't let your LLM chase

**An MCP server that gives any LLM a disciplined trading brain for Indian markets: it scans all ~2,700 NSE stocks, explains why they're moving, refuses to bypass your risk limits, and grades every call it makes.**

## The problem

Broker MCP servers already exist — Zerodha ships one, so does Alpaca. But they're plumbing: they hand an LLM live prices and an order button, with no memory, no analytics, and no enforced risk controls. Zerodha's own hosted server can't even fetch your trade history, and its "safety" is a flag the model sets on itself. Meanwhile every AI stock-picker demo asks you to trust a language model's price predictions, which the research says are weak at short horizons.

So you get two bad options: raw broker access with no judgment, or confident-sounding predictions with no evidence.

## What finLM does

finLM is the missing layer between them — 13 MCP tools across four stages, built on one principle: **the LLM never predicts prices and never bypasses risk math.**

- **Discovery (deterministic).** A nightly job pulls NSE's free daily bhavcopy into SQLite, and `scan_market` screens the entire universe down to the 10-15 names that are liquid *and* unusually active — price/volume/turnover floors, then gap %, change %, and volume against a 20-day average. No LLM, no paid data feed, no broker account.
- **Analysis (two opinions, never averaged).** Each candidate runs through two independent alpha models — trend-following and mean-reversion — that each return a conviction score, written reasoning, and an ATR-based entry/stop/target. They're deliberately not blended into one number, because a strong uptrend is a buy to one and an overbought sell to the other; collapsing that disagreement hides the actual decision. Then `research_symbol` searches the web for *why* the stock moved — earnings, order wins, or nothing at all.
- **Judgment (the feedback loop).** Every pick is journaled with its thesis verbatim, and `eod_review` later scores it against real closing prices: stop hit, target hit, or still open, with all-time hit-rate stats.
- **Execution (guardrails in code, not prompts).** Orders are validated server-side against a mandate file the user writes — order-value cap, quantity cap, daily count, symbol allowlists — plus a filesystem kill switch. No mandate file means no orders. There is no parameter the model can pass to get around it.

## Why it's different: it proved itself, then admitted where it was wrong

finLM ships with a point-in-time backtest engine (next-day-open entries, stop-first assumptions, gap-through handling, train/test splits, and benchmark-adjusted alpha so a rising market can't masquerade as skill). Across ~11 months of full-universe NSE data and 2,758 signal events:

- **Chasing spikes loses, in every configuration tested** — 37% win rate, about -1.3% alpha per trade. The system's most valuable output is knowing not to do the thing every naive AI picker does.
- **Fading them is real alpha, not market beta** — short-the-mover held +0.5% to +1.6% alpha per trade out-of-sample *while the benchmark rose 26%*.
- **Best robust setup: pullback-entry fade** — 935 trades, 52.6% win rate, +1.2% alpha per trade, profit factor 1.18, positive alpha in 10 of 12 months, and it *improved* out-of-sample (PF 1.14 → 1.27), which is what a real pattern looks like rather than a curve fit.

Then it ran forward, live. Five calls were logged on 21 July and graded on 24 July: the top fade (BEPL — a 100x-average-volume blowoff with no findable catalyst) hit its target for **+10.31%**, roughly +9.4% alpha against a market that fell 0.95%. The three "avoid" calls all dropped 6-10%, so not chasing them was right.

It also recorded a hypothesis it got wrong: two of those avoids were spared *because they had genuine earnings catalysts*, on the theory that catalyst-backed moves fade less. They faded just as hard. Sample size five, one week — but it's logged, dated, and testable, which is the entire point. Most AI trading projects can't tell you whether they were right. This one keeps receipts.

## Tech

Python, FastMCP, SQLite, pandas (hand-rolled indicators — no dead dependencies), Tavily for news, Zerodha Kite Connect for the live tier. Works with **zero credentials**: the scanner, both signal models, the journal, and the backtest all run on free public NSE data. API keys only unlock live quotes and order placement.

## Status

Working end-to-end and verified over the MCP protocol — 13 tools, live in Claude Desktop and Claude Code. 14 months of market history ingested, backtest reproducible from the repo, journal actively scoring live calls.

**Next:** benchmark-adjusted journal analytics with catalyst tagging (to properly test the hypothesis above), a long-side scanner, and a paper-portfolio equity curve.

---

## Short-form fields (for submission forms)

**Tagline:** finLM — an MCP intelligence layer that gives LLMs a disciplined trading brain, and makes them prove it.

**Theme:** Verifiable agentic AI — evidence over vibes. This isn't really a trading project; it's about making an AI agent trustworthy when the stakes are real. Markets are the ideal proving ground because reality grades you automatically.

**Problem:** Broker MCP servers are pure plumbing — live prices and an order button, no memory, no analytics, and "safety" that's just a flag the model sets on itself. AI stock-pickers ask you to trust LLM price predictions that research shows are weak at short horizons. Nobody ships the layer in between: judgment with guardrails, and proof it works.

**Idea:** Split the work by what each side is good at. Deterministic math finds what is moving and enforces what you're allowed to do. The LLM explains why it's moving and writes the plan. Every decision is logged with its reasoning and scored against reality later. The LLM never predicts prices and never bypasses risk math.

**Solution:** 13 MCP tools in four layers — discovery (free NSE bhavcopy → SQLite → whole-universe screener), analysis (two never-averaged alpha models with reasoning and ATR trade plans, plus web catalyst research), judgment (thesis journal + automatic scoring against real closes), execution (server-side risk mandate + kill switch, unbypassable by the model).

**One-box version (150 words):** finLM is an MCP server that gives any LLM a disciplined trading brain for Indian markets — and then makes it prove itself. Deterministic code screens all ~2,700 NSE stocks for unusual activity and enforces hard risk limits the model cannot bypass; the LLM does the part it's actually good at, explaining why a stock is moving and writing the trade plan. Every decision is journaled with its reasoning and later graded against real prices, so the system builds a track record instead of opinions. A point-in-time backtest across 2,758 signals proved chasing spikes loses (-1.3% alpha per trade) while fading them earns +1.2% alpha, holding up out-of-sample. Live, its top call returned +10.31% against a falling market — and it logged a hypothesis it got wrong. Works with zero credentials on free public data. The real subject isn't trading; it's how you make an AI agent trustworthy when money is on the line.
