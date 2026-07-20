"""
server.py
─────────
llmfin MCP server — the trading intelligence layer, exposed to any MCP client
(Claude Desktop, Claude Code, Cursor, …) over stdio.

Tool map (12 tools, 4 layers)
─────────────────────────────
  DISCOVERY   (free, no credentials — local bhavcopy DB)
    ingest_market_data      refresh the local NSE EOD database
    scan_market             deterministic screen: today's liquid movers
  ANALYSIS    (free; upgrades to live Kite data when authenticated)
    research_instrument     indicators + trend/mean-reversion signals + ATR plan
    get_batch_research      the same, over a list of symbols
    research_symbol         news/catalyst lookup — WHY is it moving (Tavily)
  JOURNAL     (free — the feedback loop)
    log_decision            record a pick WITH its thesis
    eod_review              score past picks against what actually happened
    get_journal             recent decisions
  BROKER      (requires Zerodha Kite session)
    get_market_quote        live LTP / OHLC / volume
    search_instruments      symbol → instrument_token (disk-cached daily)
    get_portfolio_positions current positions
    place_order             risk-gated order placement (mandate + kill switch)

The risk gate on place_order is enforced server-side (see risk.py) — there is
no flag an LLM can set to bypass it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import anyio
from mcp.server.fastmcp import FastMCP

from llmfin import journal as journal_mod
from llmfin import risk as risk_mod
from llmfin.data_store import DATA_DIR, ingest_range
from llmfin.market_research import research_instrument as _research
from llmfin.research_web import research_symbol as _research_symbol
from llmfin.scanner import scan_market as _scan
from llmfin.session_manager import get_kite_client, get_kite_client_or_none

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("llmfin-trading-intelligence")


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════════
# DISCOVERY LAYER
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def ingest_market_data(days: int = 60) -> str:
    """Download NSE daily bhavcopy files into the local market database.

    Run this once to bootstrap (and each evening to refresh) the free EOD
    data spine that powers scan_market and offline research. Takes about a
    minute for a 60-day backfill. No broker account or API key needed.
    """
    result = await anyio.to_thread.run_sync(lambda: ingest_range(days_back=days))
    return _json(result)


@mcp.tool()
async def scan_market(
    direction: Literal["up", "down", "both"] = "both",
    min_price: float = 100.0,
    min_avg_volume: int = 500_000,
    min_volume_ratio: float = 1.5,
    min_abs_change_pct: float = 1.0,
    limit: int = 15,
) -> str:
    """Deterministically screen the whole NSE universe for today's notable movers.

    Filters ~2000 stocks down to the few that are liquid AND unusually active:
    price/volume/turnover floors, then gap %, change %, and volume vs the
    20-day average. This is the discovery step — run it FIRST, then use
    research_symbol on the survivors to find out WHY each one is moving.
    Uses the local bhavcopy DB (run ingest_market_data if it errors).
    """
    hits = await anyio.to_thread.run_sync(
        lambda: _scan(
            direction=direction,
            min_price=min_price,
            min_avg_volume=min_avg_volume,
            min_volume_ratio=min_volume_ratio,
            min_abs_change_pct=min_abs_change_pct,
            limit=limit,
        )
    )
    if not hits:
        return _json({"hits": [], "note": "No symbols passed the filters — try loosening them."})
    return _json([asdict(h) for h in hits])


# ═══════════════════════════════════════════════════════════════════════════
# ANALYSIS LAYER
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def research_instrument(
    symbol: str,
    instrument_token: Optional[int] = None,
    exchange: str = "NSE",
    interval: Literal["1d", "1h", "30m", "15m", "5m"] = "1d",
    lookback_days: int = 180,
) -> str:
    """Full technical research on one instrument: RSI/MACD/Bollinger/EMA/ATR
    plus TWO independent signals — trend-following and mean-reversion — each
    with conviction (-1..+1) and written reasoning, and an ATR-based
    entry/stop/target plan for any non-HOLD signal.

    The two signals are opposing philosophies and are deliberately NOT
    averaged: weigh them yourself based on the regime and the news context.
    Works without any broker credentials (daily data from the local DB);
    uses live Kite data and intraday intervals when a session exists.
    """
    kite = get_kite_client_or_none()
    result = await anyio.to_thread.run_sync(
        lambda: _research(
            symbol=symbol,
            instrument_token=instrument_token,
            exchange=exchange,
            interval=interval,
            lookback_days=lookback_days,
            kite=kite,
        )
    )
    return _json(result.to_dict())


@mcp.tool()
async def get_batch_research(symbols: list[str], lookback_days: int = 180) -> str:
    """Run research_instrument over a list of NSE symbols (e.g. the survivors
    of scan_market) and return results sorted by strongest absolute signal
    conviction. Daily data; works with no credentials."""
    kite = get_kite_client_or_none()

    def _run() -> list[dict]:
        out = []
        for sym in symbols:
            try:
                out.append(_research(symbol=sym, kite=kite, lookback_days=lookback_days).to_dict())
            except Exception as exc:
                out.append({"symbol": sym, "error": str(exc)})
        out.sort(
            key=lambda r: max(
                (abs(s["conviction"]) for s in r.get("signals", [])), default=0
            ),
            reverse=True,
        )
        return out

    return _json(await anyio.to_thread.run_sync(_run))


@mcp.tool()
async def research_symbol(
    symbol: str,
    company_name: str = "",
    days: int = 3,
    change_pct: Optional[float] = None,
    move_date: str = "",
) -> str:
    """Find out WHY a stock is moving: recent news, earnings, orders, corporate
    actions — via web search (Tavily). A 4% gap on a real catalyst is a
    different trade than a 4% gap on nothing; run this on every scan_market
    survivor before ranking. ALWAYS pass change_pct and move_date from the
    scan_market hit when available — it makes the search dramatically more
    precise. Weigh the returned sources yourself; the synthesized answer can
    conflate companies. Requires TAVILY_API_KEY in the environment."""
    result = await anyio.to_thread.run_sync(
        lambda: _research_symbol(
            symbol, company_name=company_name, days=days,
            change_pct=change_pct, move_date=move_date,
        )
    )
    return _json(result)


# ═══════════════════════════════════════════════════════════════════════════
# JOURNAL LAYER
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def log_decision(
    symbol: str,
    direction: Literal["BUY", "SELL", "AVOID"],
    thesis: str,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    quantity: Optional[int] = None,
    source: str = "scanner+news",
) -> str:
    """Journal a trading decision WITH its full reasoning, before or instead of
    acting on it. ALWAYS log the thesis verbatim — eod_review scores these
    against real market data later, which is how this system learns whether
    its picks are any good. AVOID entries (deliberate passes) are scored too."""
    result = await anyio.to_thread.run_sync(
        lambda: journal_mod.log_decision(
            symbol=symbol,
            direction=direction,
            thesis=thesis,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            quantity=quantity,
            source=source,
        )
    )
    return _json(result)


@mcp.tool()
async def eod_review(trade_date: Optional[str] = None) -> str:
    """Score journaled decisions against what the market actually did
    (stop hit? target hit? still open?) using the local bhavcopy DB, and
    return all-time hit-rate stats. Run after ingest_market_data has pulled
    the latest close. Pass trade_date=YYYY-MM-DD to re-score one day."""
    result = await anyio.to_thread.run_sync(lambda: journal_mod.eod_review(trade_date))
    return _json(result)


@mcp.tool()
async def get_journal(limit: int = 50) -> str:
    """Return the most recent journaled decisions with outcomes — the raw
    material for 'what is my hit rate on gap-up trades' style questions."""
    return _json(await anyio.to_thread.run_sync(lambda: journal_mod.get_journal(limit)))


# ═══════════════════════════════════════════════════════════════════════════
# BROKER LAYER (Zerodha Kite session required)
# ═══════════════════════════════════════════════════════════════════════════

_NO_SESSION_MSG = (
    "No Zerodha session. Set KITE_API_KEY/KITE_API_SECRET in .env and complete "
    "the login flow (python -m llmfin.auth). The discovery/analysis/journal "
    "tools all work without it."
)


@mcp.tool()
async def get_market_quote(instruments: list[str]) -> str:
    """Live LTP, OHLC, volume and circuit limits for instruments given as
    'EXCHANGE:SYMBOL' strings, e.g. ["NSE:INFY", "NSE:TCS"]. Requires an
    authenticated Zerodha session."""
    kite = get_kite_client_or_none()
    if kite is None:
        return _json({"error": _NO_SESSION_MSG})
    return _json(await anyio.to_thread.run_sync(lambda: kite.quote(instruments)))


_INSTRUMENT_CACHE = DATA_DIR / "instruments_cache.json"


def _load_instruments(kite) -> list[dict]:
    """Zerodha's instrument dump is ~100k rows; cache it on disk for a day."""
    if _INSTRUMENT_CACHE.exists():
        age = datetime.now(timezone.utc).timestamp() - _INSTRUMENT_CACHE.stat().st_mtime
        if age < 24 * 3600:
            return json.loads(_INSTRUMENT_CACHE.read_text())
    instruments = kite.instruments()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _INSTRUMENT_CACHE.write_text(json.dumps(instruments, default=str))
    return instruments


@mcp.tool()
async def search_instruments(query: str, exchange: str = "NSE", limit: int = 10) -> str:
    """Search Zerodha's instrument master for a ticker; returns the
    instrument_token needed for intraday research_instrument calls.
    Requires an authenticated Zerodha session (dump is cached daily)."""
    kite = get_kite_client_or_none()
    if kite is None:
        return _json({"error": _NO_SESSION_MSG})

    def _run() -> list[dict]:
        q = query.upper()
        matches = [
            i
            for i in _load_instruments(kite)
            if (exchange in ("ALL", i.get("exchange")))
            and (q in str(i.get("tradingsymbol", "")).upper() or q in str(i.get("name", "")).upper())
        ]
        return matches[:limit]

    return _json(await anyio.to_thread.run_sync(_run))


@mcp.tool()
async def get_portfolio_positions() -> str:
    """Current intraday and overnight positions from the Zerodha account.
    Requires an authenticated session."""
    kite = get_kite_client_or_none()
    if kite is None:
        return _json({"error": _NO_SESSION_MSG})
    return _json(await anyio.to_thread.run_sync(kite.positions))


@mcp.tool()
async def place_order(
    tradingsymbol: str,
    transaction_type: Literal["BUY", "SELL"],
    quantity: int,
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"] = "MARKET",
    price: Optional[float] = None,
    trigger_price: Optional[float] = None,
    product: Literal["CNC", "MIS", "NRML"] = "CNC",
    exchange: str = "NSE",
) -> str:
    """Place an order via Zerodha Kite — gated by the user's risk mandate.

    Every order is validated server-side against risk_limits.json (order value
    cap, quantity cap, daily order count, product/symbol allowlists) and a
    filesystem kill switch. If the mandate file does not exist, orders are
    BLOCKED by design: only the user, outside this conversation, can create
    it. There is no parameter that bypasses the gate. Successful orders are
    recorded for the daily cap and auto-journaled."""
    kite = get_kite_client_or_none()
    if kite is None:
        return _json({"error": _NO_SESSION_MSG})

    def _run() -> dict:
        # Estimate order value for the mandate check: limit price if given,
        # else live LTP.
        est_price = price
        if est_price is None:
            try:
                q = kite.ltp([f"{exchange}:{tradingsymbol}"])
                est_price = list(q.values())[0]["last_price"]
            except Exception:
                est_price = None

        verdict = risk_mod.check_order(
            symbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            exchange=exchange,
            product=product,
            est_price=est_price,
        )
        if not verdict.allowed:
            return {"status": "REJECTED_BY_RISK_GATE", **verdict.to_dict()}

        params = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": transaction_type,
            "quantity": int(quantity),
            "order_type": order_type,
            "product": product,
            "variety": kite.VARIETY_REGULAR,
        }
        if price is not None:
            params["price"] = float(price)
        if trigger_price is not None:
            params["trigger_price"] = float(trigger_price)

        order_id = kite.place_order(**params)
        risk_mod.record_order(
            tradingsymbol, transaction_type, quantity, (est_price or 0) * quantity, str(order_id)
        )
        journal_mod.log_decision(
            symbol=tradingsymbol,
            direction=transaction_type,
            thesis=f"Order placed via MCP: {order_type} {quantity} @ {price or 'MKT'}",
            entry=est_price,
            quantity=quantity,
            source="order",
        )
        return {"status": "success", "order_id": order_id, "risk_check": verdict.to_dict()}

    return _json(await anyio.to_thread.run_sync(_run))


@mcp.tool()
async def get_risk_status() -> str:
    """Show the active risk mandate, kill-switch state, and today's order
    count — what place_order will and won't allow right now."""

    def _run() -> dict:
        return {
            "mandate_file": str(risk_mod.RISK_FILE.resolve()),
            "mandate": risk_mod.load_mandate(),
            "kill_switch_active_at": risk_mod.kill_switch_active(),
            "orders_placed_today": risk_mod.orders_placed_today(),
            "note": (
                "Edit risk_limits.json yourself to change limits — the LLM "
                "cannot. Touch a KILL_SWITCH file to freeze all trading."
            ),
        }

    return _json(await anyio.to_thread.run_sync(_run))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="llmfin MCP server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve over streamable HTTP at http://127.0.0.1:<port>/mcp "
        "(for Claude Desktop custom connectors) instead of stdio",
    )
    parser.add_argument("--port", type=int, default=8747)
    args = parser.parse_args()

    if args.http:
        mcp.settings.host = "127.0.0.1"   # local only — never bind 0.0.0.0
        mcp.settings.port = args.port
        logger.info("Starting llmfin MCP server at http://127.0.0.1:%d/mcp …", args.port)
        mcp.run(transport="streamable-http")
    else:
        logger.info("Starting llmfin MCP server (stdio) …")
        mcp.run()


if __name__ == "__main__":
    main()
