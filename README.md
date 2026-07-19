# llmfin — MCP Trading Intelligence Layer for NSE

> An MCP server that gives LLMs a real trading intelligence layer for Indian markets:
> deterministic market scanning, regime-separated signals, news research, a decision
> journal that scores itself, and risk-gated execution via Zerodha Kite Connect.

The design principle: **the LLM never predicts prices and never bypasses risk math.**
Deterministic code finds *what* is moving and enforces *what you're allowed to do*;
the LLM's job is the part it's actually good at — explaining *why* something is
moving, weighing evidence, and writing the trade plan.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│               LLM Client (Claude / Cursor / …)           │
└────────────────────────┬─────────────────────────────────┘
                         │ MCP (stdio)
┌────────────────────────▼─────────────────────────────────┐
│                  llmfin MCP server — 13 tools            │
│                                                          │
│  DISCOVERY   scan_market · ingest_market_data            │
│  ANALYSIS    research_instrument · get_batch_research    │
│              research_symbol (news, via Tavily)          │
│  JOURNAL     log_decision · eod_review · get_journal     │
│  BROKER      get_market_quote · search_instruments       │
│              get_portfolio_positions · place_order       │
│              get_risk_status                             │
└──────┬──────────────────┬──────────────────┬─────────────┘
       │                  │                  │
┌──────▼───────┐  ┌───────▼────────┐  ┌──────▼───────────┐
│ NSE bhavcopy │  │ Risk gate      │  │ Zerodha Kite     │
│ → SQLite     │  │ risk_limits.   │  │ Connect API      │
│ (free EOD    │  │ json + KILL_   │  │ (optional: live  │
│  data spine) │  │ SWITCH file    │  │  data + orders)  │
└──────────────┘  └────────────────┘  └──────────────────┘
```

**Three tiers of capability, by what you've configured:**

| Configured | You get |
|---|---|
| Nothing at all | Market scanner, technical research, signals, journal — on free NSE EOD data |
| + `TAVILY_API_KEY` (free) | News/catalyst research: *why* is it moving |
| + Kite Connect API keys | Live quotes, intraday candles, positions, risk-gated orders |

## The intelligence layer, piece by piece

**Scanner (deterministic).** `scan_market` cuts the ~2000-stock NSE universe to the
few names that are liquid *and* unusually active: price/volume/turnover floors,
then gap %, change %, and volume vs the 20-day average. No LLM involved — pandas
math over the local DB.

**Signals (regime-separated).** `research_instrument` runs two independent alpha
models — trend-following and mean-reversion — each returning conviction (-1..+1)
and written reasoning, plus ATR-based entry/stop/target plans. They are
deliberately **not averaged**: a strong uptrend is a BUY to one and an overbought
SELL to the other, and collapsing that disagreement into one number is how the
old version of this repo ended up structurally HOLD-ing every trending stock.
The mean-reversion model has a falling-knife veto: it refuses to buy oversold
names trading >10% below their EMA50.

**News layer.** `research_symbol` answers "why is this stock moving today" via
Tavily search — earnings, order wins, corporate actions. A 4% gap on a real
catalyst is a different trade than a 4% gap on nothing; this is the one step
where the LLM adds value the math can't.

**Journal (the feedback loop).** `log_decision` records every pick *with its
thesis verbatim*; `eod_review` later scores each one against real market data —
stop hit, target hit, still open — and keeps all-time hit-rate stats. After a few
weeks you have hard numbers on whether the picks beat a coin flip.

**Risk gate (enforced, not prompted).** `place_order` validates every order
server-side against `risk_limits.json` — order-value cap, quantity cap, daily
order count, product/symbol allowlists — plus a filesystem `KILL_SWITCH`. No
mandate file ⇒ orders are blocked. There is **no parameter the LLM can pass to
bypass this** (the old `confirmed=true` flag was theater — the model just set it
itself).

## Setup

```bash
git clone https://github.com/hasil7677/finLLM && cd finLLM
python -m venv .venv
.venv\Scripts\activate          # Windows   (source .venv/bin/activate on mac/linux)
pip install -e .

copy .env.example .env           # then fill in whichever keys you have (all optional)

# Bootstrap the free data spine (~1 min, no accounts needed)
llmfin-ingest --days 150
```

### Try it standalone

```bash
llmfin-scan --limit 10           # today's movers, as JSON
llmfin-research                  # scan + research + rich terminal report
```

### Wire into Claude Desktop

Merge [claude_desktop_config.json](./claude_desktop_config.json) into
`%APPDATA%\Claude\claude_desktop_config.json` (adjust paths), restart Claude,
then try:

- *"Ingest the latest market data, scan for today's movers, research the top 5,
  and build me a ranked watchlist with trade plans."*
- *"Why is RELAXO up 20%? Search the news, then log a decision with your thesis."*
- *"Run the EOD review — what's my hit rate so far?"*

### Enable live trading (deliberately manual)

1. Get Kite Connect keys at [developers.kite.trade](https://developers.kite.trade), put them in `.env`.
2. Each trading day: `python -m llmfin.auth` → log in → `python -m llmfin.auth --request-token <TOKEN>`.
3. Copy `risk_limits.example.json` → `risk_limits.json` and set **your** caps.
   This file is your mandate; the LLM can't create or edit it. Create a file named
   `KILL_SWITCH` (repo root or `~/.llmfin/`) to freeze all trading instantly.

## Daily rhythm

| When (IST) | What | How |
|---|---|---|
| Evening (after ~6 PM) | Refresh EOD data | `llmfin-ingest` (or ask the LLM: "ingest market data") |
| Evening | Score today's picks | `eod_review` tool |
| Morning | Build the watchlist | `scan_market` → `research_symbol` on survivors → `get_batch_research` → `log_decision` per pick |

Automate the evening steps with Windows Task Scheduler or cron if you like.

## Project structure

```
finLLM/
├── src/llmfin/
│   ├── server.py           MCP server — 13 tools, 4 layers
│   ├── data_store.py       NSE bhavcopy → SQLite (the free data spine)
│   ├── scanner.py          deterministic market screen
│   ├── indicators.py       RSI/MACD/BB/EMA/ATR in pure pandas (no pandas_ta)
│   ├── signals.py          trend + mean-reversion alpha models → Signal objects
│   ├── market_research.py  per-instrument research (Kite or local DB)
│   ├── research_web.py     news/catalyst lookup (Tavily)
│   ├── journal.py          decision log + EOD self-scoring
│   ├── risk.py             mandate + kill-switch order gate
│   ├── session_manager.py  Kite auth + daily token cache (IST-aware)
│   ├── batch_runner.py     standalone morning report (llmfin-research)
│   └── auth.py             Kite OAuth CLI helper
├── risk_limits.example.json
├── watchlist.json           optional fixed watchlist for llmfin-research
└── claude_desktop_config.json
```

Data lives in `~/.llmfin/` (`market.db`, `journal.db`, `orders.db`), overridable
via `LLMFIN_DATA_DIR`.

## Backtest — what the deterministic core is actually worth

`llmfin-backtest` replays the scanner + signal models point-in-time over the
local DB with no look-ahead: entries at next-day open (or a pullback limit),
ATR stops/targets from the actual entry, stop-first assumption, gap-through
handling, and **benchmark-adjusted alpha** (equal-weight liquid-universe
index) so a market drift can't masquerade as edge. The engine separates the
expensive scan pass from exit simulation, so parameter sweeps and
train/test splits (`--start` / `--end`) are cheap.

Findings on ~11 months of full-universe NSE data (2,758 signal events,
train = pre-Feb 2026, test = after; before transaction costs):

1. **Chasing movers loses, everywhere.** Buying yesterday's spike at next
   open: ~37% win rate, ≈ -1.3% alpha/trade in train, negative in every
   config tested. This is the single most valuable output: the system now
   *knows* not to do the thing naive AI stock-pickers do.
2. **The fade is real alpha, not beta.** Short-the-mover strategies kept
   +0.5 to +1.6% alpha/trade out-of-sample even while the benchmark rose
   ~26% in the test window.
3. **Best robust config: pullback-entry fade** — wait up to 3 days for a
   bounce to `scan_close + 0.5×ATR`, short there, stop 2.0×ATR, target
   2.5×ATR, horizon 10 days: 935 trades, 52.6% win, +0.58% raw /
   **+1.2% alpha** per trade, profit factor 1.18, positive alpha in 10 of
   12 months. It *improved* out-of-sample (train PF 1.14 → test PF 1.27),
   which is what a real pattern looks like.

Practical constraints, stated plainly: retail cash-market shorting beyond
intraday isn't possible in India, so the fade edge is directly tradeable
only via F&O names or intraday; for cash-only accounts the proven value is
the **avoid/exit filter** (holding something that just spiked on volume is
a de-risking signal) and ATR exits (~0.7pp/trade better than passive).
The long side has no edge in this candidate stream — finding one (quiet
uptrends? post-fade re-entries?) is the next experiment, and the journal
exists to keep score.

## Design inspirations

- [ai-hedge-fund v2](https://github.com/virattt/ai-hedge-fund) — the AlphaModel/Signal
  abstraction: every signal source, LLM or quant, returns conviction + reasoning
  through one interface.
- [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) — user-committed mandates and
  filesystem kill switches as structural (not prompt-level) guardrails.
- [zerodha/kite-mcp-server](https://github.com/zerodha/kite-mcp-server) — broker
  plumbing reference. llmfin is the layer that sits *above* raw broker access.

## ⚠️ Disclaimer

Educational and research software. Not financial advice, not a registered
investment adviser. Intraday signal quality from any system — LLM or otherwise —
is modest at best; that's exactly why the journal exists. Paper-trade first,
size small, and let `eod_review` tell you the truth about your hit rate.
