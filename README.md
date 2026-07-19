# llmfin — MCP Trading Intelligence Layer

> **An MCP-based intelligence layer for LLMs that performs hedge-fund-style market research as a batch job using the Zerodha Kite Connect SDK and suggests trades.**

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    LLM Client                        │
│          (Claude / GPT-4 / Gemini via MCP)          │
└──────────────────────┬──────────────────────────────┘
                       │  MCP Protocol (stdio)
┌──────────────────────▼──────────────────────────────┐
│               llmfin MCP Server                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  Tools:                                       │   │
│  │  • research_instrument  (full TA analysis)    │   │
│  │  • get_batch_research   (watchlist screening) │   │
│  │  • get_market_quote     (live LTP)            │   │
│  │  • search_instruments   (find tokens)         │   │
│  │  • get_portfolio_positions                    │   │
│  │  • place_order          (with safety guard)   │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│           market_research.py (core engine)           │
│   OHLCV fetch → RSI/MACD/BB/EMA/ATR → Signal gen   │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│         session_manager.py (auth + caching)          │
│   OAuth flow → token persistence (~/.llmfin_session) │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│           Zerodha Kite Connect API                   │
│   Historical Data • Quotes • Orders • Positions      │
└─────────────────────────────────────────────────────┘
```

## Research Engine — How It Works

The research pipeline is inspired by quantitative hedge fund strategies:

| Indicator | Signal Logic | Contribution |
|-----------|-------------|-------------|
| **RSI-14** | < 35 → BUY; > 65 → SELL | ±1 |
| **MACD Histogram** | Positive → BUY; Negative → SELL | ±1 |
| **Bollinger Bands** | Close in bottom 25% → BUY; top 25% → SELL | ±1 |
| **EMA-50 Trend** | Close above → BUY; below → SELL | ±1 |

Score ≥ 2 → **BUY** | Score ≤ -2 → **SELL** | Otherwise → **HOLD**

**Confidence** = |score| / 4 (0–100%)

**Risk levels** use ATR-14:
- Stop-loss: 1.5 × ATR from entry
- Target: 2.5 × ATR from entry (R:R ≈ 1.67)

## Inspiration — Open Source Research

This system draws from these hedge-fund-inspired open source repositories:

| Repo | What it does |
|------|-------------|
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | Full backtesting + live trading engine (C#/Python) |
| [quantopian/zipline](https://github.com/quantopian/zipline) | Pythonic algorithmic trading library |
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | Lightweight Python backtesting |
| [blankly-finance/blankly](https://github.com/blankly-finance/blankly) | Multi-exchange trading framework |
| [twopirllc/pandas-ta](https://github.com/twopirllc/pandas-ta) | 130+ technical indicators (used here) |
| [zerodha/kiteconnect-py](https://github.com/zerodha/kiteconnect-py) | Official Kite Connect Python SDK |

## Installation

### Prerequisites
- Python 3.10+
- A Zerodha Kite Connect API subscription (get from [developers.kite.trade](https://developers.kite.trade))

### Setup

```bash
cd C:\Users\SahilTamang\downloads\llmfin

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # macOS/Linux

# Install in editable mode
pip install -e .

# Copy and fill in credentials
copy .env.example .env
# Edit .env with your KITE_API_KEY and KITE_API_SECRET
```

## Authentication (One-Time Per Trading Day)

Zerodha tokens expire at midnight IST. Each morning:

```bash
# Step 1 — Get your login URL
python -m llmfin.auth

# Step 2 — Open the URL, log in, copy the request_token from the redirect
python -m llmfin.auth --request-token xxxxxxxxxxxxxxxxxxxxxxxx
```

The access token is cached at `~/.llmfin_session.json` and reused automatically all day.

## Usage

### 1. Batch Research Runner (standalone)

```bash
# Research the default 10-stock Nifty 50 watchlist
llmfin-research

# Custom watchlist
LLMFIN_WATCHLIST=watchlist.json llmfin-research

# Save reports to a directory
LLMFIN_REPORT_DIR=./reports llmfin-research
```

Example output:
```
┌─────────────────────────────────────────────────┐
│ Symbol   │ Signal │ Conf │ Close    │ Stop-Loss  │
├──────────┼────────┼──────┼──────────┼────────────┤
│ INFY     │  BUY   │  75% │ ₹1,432   │ ₹1,385     │
│ RELIANCE │  HOLD  │  25% │ ₹2,891   │    —        │
│ TCS      │  SELL  │  50% │ ₹3,740   │ ₹3,812     │
└─────────────────────────────────────────────────┘
```

### 2. MCP Server (for LLM clients)

```bash
llmfin-server
```

Then connect via any MCP client.

### 3. Claude Desktop Integration

Copy [claude_desktop_config.json](./claude_desktop_config.json) content into:
`%APPDATA%\Claude\claude_desktop_config.json`

Then ask Claude:
- *"Research INFY and tell me if I should buy it today."*
- *"Screen my watchlist and find the top 3 BUY signals."*
- *"What's the live price of TCS and HDFCBANK?"*
- *"Place a limit buy order for 10 shares of RELIANCE at ₹2,850."*

## MCP Tools Reference

| Tool | Description |
|------|-------------|
| `research_instrument` | Full TA + signal for one stock |
| `get_batch_research` | Screen a list of stocks in one call |
| `get_market_quote` | Live LTP / OHLC / volume |
| `search_instruments` | Find instrument_token by ticker name |
| `get_portfolio_positions` | Current positions |
| `place_order` | Place order (requires `confirmed=true`) |

## Project Structure

```
llmfin/
├── src/llmfin/
│   ├── __init__.py
│   ├── server.py           ← MCP server (6 tools)
│   ├── market_research.py  ← OHLCV + indicators + signals
│   ├── batch_runner.py     ← Standalone batch job
│   ├── session_manager.py  ← Auth + token persistence
│   └── auth.py             ← OAuth CLI helper
├── watchlist.json          ← Default 15-stock watchlist
├── .env.example            ← Config template
├── claude_desktop_config.json
└── pyproject.toml
```

## Scheduled Batch (Windows Task Scheduler)

Run research every morning at 9:00 AM before market open:

```batch
# save as run_research.bat
cd C:\Users\SahilTamang\downloads\llmfin
.venv\Scripts\activate
set LLMFIN_WATCHLIST=watchlist.json
set LLMFIN_REPORT_DIR=reports
llmfin-research
```

Schedule via Task Scheduler → New Task → Trigger: Daily 9:00 AM.

## ⚠️ Disclaimer

This software is for **educational and research purposes only**. It does not constitute financial advice. Algorithmic trading carries significant risk of loss. Always paper-trade and validate strategies before using real capital. The authors are not responsible for any financial losses incurred.
