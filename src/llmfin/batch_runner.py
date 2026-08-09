"""
batch_runner.py
───────────────
Standalone morning batch: scan the market, research the movers, print a report.
Designed for a scheduler (Task Scheduler / cron) before market open.

Works with ZERO credentials - scanner and research run off the local bhavcopy
DB. With a Kite session, research upgrades to live data automatically.

Usage
─────
    llmfin-research                      # scan latest day + research movers
    llmfin-research --watchlist watchlist.json   # fixed watchlist instead of scan
    LLMFIN_REPORT_DIR=reports llmfin-research    # also save JSON report
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from llmfin.market_research import ResearchResult, research_instrument
from llmfin.scanner import scan_market
from llmfin.session_manager import get_kite_client_or_none

logger = logging.getLogger(__name__)
console = Console()

SIGNAL_STYLE = {"BUY": "bold green", "SELL": "bold red", "HOLD": "yellow"}


def _strongest(r: ResearchResult):
    return max(r.signals, key=lambda s: abs(s.conviction))


def _render_report(results: list[ResearchResult]) -> None:
    as_of = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.rule(f"[bold cyan]llmfin - Market Research Report  •  {as_of}[/bold cyan]")
    console.print()

    table = Table(
        title="Signals by model (trend vs mean-reversion are NOT averaged)",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Symbol", style="bold white", no_wrap=True)
    table.add_column("Close", justify="right", style="cyan")
    table.add_column("Trend", justify="center")
    table.add_column("MeanRev", justify="center")
    table.add_column("RSI", justify="right")
    table.add_column("Chg 1D", justify="right")
    table.add_column("Source", justify="center")

    for r in results:
        snap = r.snapshot
        cells = {}
        for s in r.signals:
            style = SIGNAL_STYLE.get(s.direction, "")
            cells[s.model] = f"[{style}]{s.direction} {s.conviction:+.2f}[/{style}]"
        table.add_row(
            r.symbol,
            f"Rs {snap.close:,.2f}",
            cells.get("trend_following", "-"),
            cells.get("mean_reversion", "-"),
            f"{snap.rsi_14:.1f}" if snap.rsi_14 is not None else "-",
            f"{snap.percent_change_1d:+.2f}%" if snap.percent_change_1d is not None else "-",
            r.data_source,
        )

    console.print(table)
    console.print()

    actionable = [r for r in results if any(s.direction != "HOLD" and abs(s.conviction) >= 0.5 for s in r.signals)]
    if actionable:
        console.rule("[bold yellow]Actionable - Reasoning & Trade Plans[/bold yellow]")
        for r in actionable:
            for s in r.signals:
                if s.direction == "HOLD" or abs(s.conviction) < 0.5:
                    continue
                style = SIGNAL_STYLE[s.direction]
                console.print(
                    f"\n[bold]{r.symbol}[/bold] · {s.model} - [{style}]{s.direction}[/{style}] "
                    f"(conviction {s.conviction:+.2f})"
                )
                for reason in s.reasoning:
                    console.print(f"  • {reason}")
                plan = r.trade_plans.get(s.model)
                if plan:
                    console.print(
                        f"  [dim]Plan: entry Rs {plan.entry:,.2f} · stop Rs {plan.stop_loss:,.2f} · "
                        f"target Rs {plan.target:,.2f} · R:R {plan.risk_reward}[/dim]"
                    )
    console.rule()


def _save_json(results: list[ResearchResult]) -> None:
    out_dir = os.getenv("LLMFIN_REPORT_DIR")
    if not out_dir:
        return
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    f = out / f"research_{ts}.json"
    f.write_text(json.dumps([r.to_dict() for r in results], indent=2, default=str))
    console.print(f"\n[dim]JSON report saved → {f}[/dim]")


def run_batch(watchlist_path: str | None = None, limit: int = 12) -> list[ResearchResult]:
    kite = get_kite_client_or_none()
    if kite:
        console.print("[green]Zerodha session found - using live Kite data.[/green]")
    else:
        console.print("[dim]No Zerodha session - using free local bhavcopy DB.[/dim]")

    if watchlist_path:
        items = json.loads(Path(watchlist_path).read_text())
        symbols = [i["symbol"] if isinstance(i, dict) else i for i in items]
        console.print(f"[cyan]Researching fixed watchlist ({len(symbols)} symbols)...[/cyan]")
    else:
        console.print("[cyan]Scanning market for today's movers...[/cyan]")
        hits = scan_market(limit=limit)
        symbols = [h.symbol for h in hits]
        if not symbols:
            console.print("[yellow]Scanner returned nothing - is the DB ingested? Run llmfin-ingest.[/yellow]")
            return []
        console.print(f"  Movers: [bold]{', '.join(symbols)}[/bold]")

    results: list[ResearchResult] = []
    for idx, sym in enumerate(symbols, 1):
        console.print(f"  [{idx}/{len(symbols)}] Researching [bold]{sym}[/bold] ...", end="\r")
        try:
            results.append(research_instrument(symbol=sym, kite=kite))
        except Exception as exc:
            console.print(f"  [red]x {sym} failed: {exc}[/red]")

    results.sort(key=lambda r: abs(_strongest(r).conviction), reverse=True)
    return results


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="llmfin morning research batch")
    parser.add_argument("--watchlist", help="JSON watchlist path (skips the market scan)")
    parser.add_argument("--limit", type=int, default=12, help="Max symbols from the scanner")
    args = parser.parse_args()

    console.print("\n[bold cyan]llmfin Market Research Batch Runner[/bold cyan]\n")
    results = run_batch(watchlist_path=args.watchlist, limit=args.limit)
    if results:
        _render_report(results)
        _save_json(results)


if __name__ == "__main__":
    main()
