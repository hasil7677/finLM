"""
batch_runner.py
───────────────
Standalone batch job: research a watchlist, rank by signal, and print a rich report.
Designed to be run on a schedule (e.g. cron @ 9:00 AM IST before market open).

Usage
─────
    python -m llmfin.batch_runner
    # or via entry-point:
    llmfin-research

    # With a custom watchlist JSON:
    LLMFIN_WATCHLIST=watchlist.json llmfin-research

Watchlist JSON format
─────────────────────
    [
      {"symbol": "RELIANCE", "instrument_token": 738561, "exchange": "NSE"},
      {"symbol": "TCS",      "instrument_token": 2953217, "exchange": "NSE"}
    ]
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich import box

from llmfin.market_research import research_instrument, ResearchResult
from llmfin.session_manager import get_kite_client

logger = logging.getLogger(__name__)
console = Console()

# ---------------------------------------------------------------------------
# Default watchlist (Nifty 50 blue chips — tokens as of 2024)
# ---------------------------------------------------------------------------
DEFAULT_WATCHLIST: list[dict[str, Any]] = [
    {"symbol": "RELIANCE",  "instrument_token": 738561,   "exchange": "NSE"},
    {"symbol": "TCS",       "instrument_token": 2953217,  "exchange": "NSE"},
    {"symbol": "HDFCBANK",  "instrument_token": 341249,   "exchange": "NSE"},
    {"symbol": "INFY",      "instrument_token": 408065,   "exchange": "NSE"},
    {"symbol": "ICICIBANK", "instrument_token": 1270529,  "exchange": "NSE"},
    {"symbol": "HINDUNILVR","instrument_token": 356865,   "exchange": "NSE"},
    {"symbol": "WIPRO",     "instrument_token": 969473,   "exchange": "NSE"},
    {"symbol": "AXISBANK",  "instrument_token": 1510401,  "exchange": "NSE"},
    {"symbol": "SBIN",      "instrument_token": 779521,   "exchange": "NSE"},
    {"symbol": "BHARTIARTL","instrument_token": 2714625,  "exchange": "NSE"},
]


def load_watchlist() -> list[dict[str, Any]]:
    wl_path = os.getenv("LLMFIN_WATCHLIST")
    if wl_path:
        p = Path(wl_path)
        if p.exists():
            return json.loads(p.read_text())
        else:
            logger.warning("LLMFIN_WATCHLIST=%s not found — using default.", wl_path)
    return DEFAULT_WATCHLIST


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

SIGNAL_STYLE = {"BUY": "bold green", "SELL": "bold red", "HOLD": "yellow"}


def _render_report(results: list[ResearchResult]) -> None:
    as_of = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.rule(f"[bold cyan]llmfin — Market Research Report  •  {as_of}[/bold cyan]")
    console.print()

    table = Table(
        title="Trade Signals (sorted by confidence ↓)",
        box=box.ROUNDED,
        show_lines=True,
        style="bright_white",
    )
    table.add_column("Symbol",     style="bold white",  no_wrap=True)
    table.add_column("Signal",     justify="center")
    table.add_column("Conf.",      justify="right")
    table.add_column("Close",      justify="right", style="cyan")
    table.add_column("Entry",      justify="right")
    table.add_column("Stop-Loss",  justify="right", style="red")
    table.add_column("Target",     justify="right", style="green")
    table.add_column("RSI",        justify="right")
    table.add_column("Chg 1D",     justify="right")
    table.add_column("Chg 5D",     justify="right")

    for r in results:
        sig = r.signal
        snap = r.snapshot
        style = SIGNAL_STYLE.get(sig.direction, "")
        pct1d = f"{snap.percent_change_1d:+.2f}%" if snap.percent_change_1d is not None else "—"
        pct5d = f"{snap.percent_change_5d:+.2f}%" if snap.percent_change_5d is not None else "—"

        table.add_row(
            r.symbol,
            f"[{style}]{sig.direction}[/{style}]",
            f"{sig.confidence:.0%}",
            f"₹{snap.close:,.2f}",
            f"₹{sig.suggested_entry:,.2f}" if sig.suggested_entry else "—",
            f"₹{sig.suggested_stop_loss:,.2f}" if sig.suggested_stop_loss else "—",
            f"₹{sig.suggested_target:,.2f}" if sig.suggested_target else "—",
            f"{snap.rsi_14:.1f}" if snap.rsi_14 is not None else "—",
            pct1d,
            pct5d,
        )

    console.print(table)
    console.print()

    # Reasoning drill-down for actionable signals
    actionable = [r for r in results if r.signal.direction != "HOLD" and r.signal.confidence >= 0.5]
    if actionable:
        console.rule("[bold yellow]Top Actionable Signals — Reasoning[/bold yellow]")
        for r in actionable:
            console.print(f"\n[bold]{r.symbol}[/bold] — [{SIGNAL_STYLE[r.signal.direction]}]{r.signal.direction}[/{SIGNAL_STYLE[r.signal.direction]}] ({r.signal.confidence:.0%} confidence)")
            for reason in r.signal.reasoning:
                console.print(f"  • {reason}")

    console.rule()


# ---------------------------------------------------------------------------
# Save JSON report
# ---------------------------------------------------------------------------

def _save_json(results: list[ResearchResult]) -> None:
    out_dir = Path(os.getenv("LLMFIN_REPORT_DIR", "."))
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"research_{ts}.json"
    data = [r.to_dict() for r in results]
    out_file.write_text(json.dumps(data, indent=2, default=str))
    console.print(f"\n[dim]JSON report saved → {out_file}[/dim]")


# ---------------------------------------------------------------------------
# Main batch runner
# ---------------------------------------------------------------------------

def run_batch() -> list[ResearchResult]:
    watchlist = load_watchlist()
    console.print(f"[cyan]Authenticating with Zerodha …[/cyan]")
    kite = get_kite_client()

    results: list[ResearchResult] = []
    total = len(watchlist)

    for idx, item in enumerate(watchlist, 1):
        sym = item["symbol"]
        console.print(f"  [{idx}/{total}] Researching [bold]{sym}[/bold] …", end="\r")
        try:
            r = research_instrument(
                symbol=sym,
                instrument_token=int(item["instrument_token"]),
                exchange=item.get("exchange", "NSE"),
                interval="1d",
                lookback_days=120,
                kite=kite,
            )
            results.append(r)
        except Exception as exc:
            console.print(f"  [red]✗ {sym} failed: {exc}[/red]")

    # Sort: BUY/SELL first, then by confidence descending
    def sort_key(r: ResearchResult):
        priority = {"BUY": 0, "SELL": 1, "HOLD": 2}
        return (priority.get(r.signal.direction, 3), -r.signal.confidence)

    results.sort(key=sort_key)
    return results


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    console.print("\n[bold cyan]llmfin Market Research Batch Runner[/bold cyan]\n")
    results = run_batch()
    _render_report(results)
    if os.getenv("LLMFIN_SAVE_REPORT", "1") != "0":
        _save_json(results)


if __name__ == "__main__":
    main()
