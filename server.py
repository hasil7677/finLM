"""
server.py
─────────
MCP Server — exposes the trading intelligence tools to any LLM client
(Claude Desktop, Cursor, Copilot, etc.) via the Model Context Protocol.

Tools exposed
─────────────
  1. research_instrument      → full technical analysis + trade signal
  2. get_market_quote         → live LTP / OHLC quote
  3. search_instruments       → search Zerodha instrument list
  4. get_portfolio_positions  → current positions from Kite
  5. place_order              → submit a market/limit order (with safety guard)
  6. get_batch_research       → run research on a list of symbols (batch)

Run
───
    python -m llmfin.server
    # or via pyproject entry-point:
    llmfin-server
"""

from __future__ import annotations

import logging
import os
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
    Tool,
)

from llmfin.market_research import research_instrument
from llmfin.session_manager import get_kite_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

app = Server("llmfin-trading-intelligence")

# ---------------------------------------------------------------------------
# Tool definitions (JSON Schema)
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="research_instrument",
        description=(
            "Perform full technical market research on a Zerodha-listed instrument. "
            "Returns RSI, MACD, Bollinger Bands, EMA, ATR, and a BUY/SELL/HOLD trade signal "
            "with entry, stop-loss, and target levels derived from ATR."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. 'RELIANCE', 'NIFTY 50'",
                },
                "instrument_token": {
                    "type": "integer",
                    "description": (
                        "Zerodha instrument token (obtain via search_instruments first). "
                        "e.g. 738561 for RELIANCE NSE"
                    ),
                },
                "exchange": {
                    "type": "string",
                    "enum": ["NSE", "BSE", "NFO", "MCX"],
                    "default": "NSE",
                    "description": "Exchange segment",
                },
                "interval": {
                    "type": "string",
                    "enum": ["1d", "1h", "30m", "15m", "5m"],
                    "default": "1d",
                    "description": "Candle interval",
                },
                "lookback_days": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 365,
                    "default": 120,
                    "description": "Number of calendar days of history to fetch",
                },
            },
            "required": ["symbol", "instrument_token"],
        },
    ),
    Tool(
        name="get_market_quote",
        description="Fetch live LTP, OHLC, volume and circuit limits for one or more instruments.",
        inputSchema={
            "type": "object",
            "properties": {
                "instruments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of 'EXCHANGE:SYMBOL' strings, e.g. ['NSE:INFY', 'NSE:TCS']",
                },
            },
            "required": ["instruments"],
        },
    ),
    Tool(
        name="search_instruments",
        description="Search the Zerodha instrument master list for a ticker name. Returns instrument_token needed for other tools.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Ticker symbol or partial name to search, e.g. 'RELIANCE'",
                },
                "exchange": {
                    "type": "string",
                    "enum": ["NSE", "BSE", "NFO", "MCX", "ALL"],
                    "default": "NSE",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_portfolio_positions",
        description="Return current intraday and overnight positions from the Zerodha portfolio.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="place_order",
        description=(
            "Place a buy or sell order via Zerodha Kite. "
            "IMPORTANT: Only call this when the user has explicitly confirmed they want to trade. "
            "Returns the Zerodha order_id on success."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "tradingsymbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. 'RELIANCE'",
                },
                "exchange": {
                    "type": "string",
                    "enum": ["NSE", "BSE", "NFO", "MCX"],
                    "default": "NSE",
                },
                "transaction_type": {
                    "type": "string",
                    "enum": ["BUY", "SELL"],
                },
                "quantity": {
                    "type": "integer",
                    "minimum": 1,
                },
                "order_type": {
                    "type": "string",
                    "enum": ["MARKET", "LIMIT", "SL", "SL-M"],
                    "default": "MARKET",
                },
                "price": {
                    "type": "number",
                    "description": "Required for LIMIT orders",
                },
                "trigger_price": {
                    "type": "number",
                    "description": "Required for SL / SL-M orders",
                },
                "product": {
                    "type": "string",
                    "enum": ["CNC", "MIS", "NRML"],
                    "default": "CNC",
                    "description": "CNC=delivery, MIS=intraday, NRML=F&O",
                },
                "confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": "Must be true for the order to actually be placed (safety guard)",
                },
            },
            "required": ["tradingsymbol", "transaction_type", "quantity", "confirmed"],
        },
    ),
    Tool(
        name="get_batch_research",
        description=(
            "Run market research on a batch of instruments in one call. "
            "Returns a list of ResearchResult objects sorted by signal confidence. "
            "Ideal for screening a watchlist and identifying the best trade candidates."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "instruments": {
                    "type": "array",
                    "description": "List of {symbol, instrument_token, exchange} objects",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "instrument_token": {"type": "integer"},
                            "exchange": {"type": "string", "default": "NSE"},
                        },
                        "required": ["symbol", "instrument_token"],
                    },
                },
                "interval": {
                    "type": "string",
                    "enum": ["1d", "1h", "30m", "15m"],
                    "default": "1d",
                },
                "lookback_days": {
                    "type": "integer",
                    "default": 120,
                },
            },
            "required": ["instruments"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools(request: ListToolsRequest) -> ListToolsResult:  # type: ignore[override]
    return ListToolsResult(tools=TOOLS)


@app.call_tool()
async def call_tool(request: CallToolRequest) -> CallToolResult:  # type: ignore[override]
    name = request.params.name
    args: dict[str, Any] = request.params.arguments or {}

    try:
        result = await anyio.to_thread.run_sync(lambda: _dispatch(name, args))
        return CallToolResult(content=[TextContent(type="text", text=result)])
    except Exception as exc:
        logger.exception("Tool %s failed: %s", name, exc)
        return CallToolResult(
            content=[TextContent(type="text", text=f"ERROR: {exc}")],
            isError=True,
        )


def _dispatch(name: str, args: dict) -> str:
    """Synchronous dispatch — runs in a thread to avoid blocking the event loop."""
    import json

    kite = get_kite_client()

    if name == "research_instrument":
        result = research_instrument(
            symbol=args["symbol"],
            instrument_token=int(args["instrument_token"]),
            exchange=args.get("exchange", "NSE"),
            interval=args.get("interval", "1d"),
            lookback_days=int(args.get("lookback_days", 120)),
            kite=kite,
        )
        return json.dumps(result.to_dict(), indent=2, default=str)

    elif name == "get_market_quote":
        instruments = args["instruments"]
        quotes = kite.quote(instruments)
        return json.dumps(quotes, indent=2, default=str)

    elif name == "search_instruments":
        query: str = args["query"].upper()
        exchange_filter: str = args.get("exchange", "NSE")
        limit: int = int(args.get("limit", 10))

        # Kite provides a downloadable CSV; we cache it lazily
        instruments_all = kite.instruments(exchange=None if exchange_filter == "ALL" else exchange_filter)
        matches = [
            inst for inst in instruments_all
            if query in inst.get("tradingsymbol", "").upper()
               or query in inst.get("name", "").upper()
        ][:limit]
        return json.dumps(matches, indent=2, default=str)

    elif name == "get_portfolio_positions":
        positions = kite.positions()
        return json.dumps(positions, indent=2, default=str)

    elif name == "place_order":
        if not args.get("confirmed", False):
            return (
                "Order NOT placed. "
                "To proceed, call this tool again with confirmed=true after the user has verified the details:\n"
                + json.dumps({k: v for k, v in args.items() if k != "confirmed"}, indent=2)
            )

        order_params = {
            "tradingsymbol": args["tradingsymbol"],
            "exchange": args.get("exchange", kite.EXCHANGE_NSE),
            "transaction_type": args["transaction_type"],
            "quantity": int(args["quantity"]),
            "order_type": args.get("order_type", kite.ORDER_TYPE_MARKET),
            "product": args.get("product", kite.PRODUCT_CNC),
            "variety": kite.VARIETY_REGULAR,
        }
        if "price" in args:
            order_params["price"] = float(args["price"])
        if "trigger_price" in args:
            order_params["trigger_price"] = float(args["trigger_price"])

        order_id = kite.place_order(**order_params)
        return json.dumps({"order_id": order_id, "status": "success"}, indent=2)

    elif name == "get_batch_research":
        instruments_list = args["instruments"]
        interval = args.get("interval", "1d")
        lookback_days = int(args.get("lookback_days", 120))

        results = []
        for inst in instruments_list:
            try:
                r = research_instrument(
                    symbol=inst["symbol"],
                    instrument_token=int(inst["instrument_token"]),
                    exchange=inst.get("exchange", "NSE"),
                    interval=interval,
                    lookback_days=lookback_days,
                    kite=kite,
                )
                results.append(r.to_dict())
            except Exception as exc:
                results.append({"symbol": inst["symbol"], "error": str(exc)})

        # Sort by signal confidence descending
        results.sort(
            key=lambda x: x.get("signal", {}).get("confidence", 0),
            reverse=True,
        )
        return json.dumps(results, indent=2, default=str)

    else:
        return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Starting llmfin MCP server (stdio transport) …")
    anyio.run(_run_server)


async def _run_server() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    main()
