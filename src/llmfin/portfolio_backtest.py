"""
portfolio_backtest.py
──────────────────────
Sequences backtest.py's independent per-trade results into a single
capital-constrained portfolio.

backtest.py answers "does this signal have edge?" by scoring every trade as
if it alone had the whole account — realistic for measuring alpha, but not
for measuring what an actual account does, since real capital and slots are
finite and shared across whatever's open at once. This module answers the
next question: run the same trades through one shared book and see what
happens to the equity curve.

What it adds on top of the per-trade engine:
  • Risk-based position sizing — each trade risks `risk_per_trade_pct` of
    CURRENT equity (not a fixed starting amount), sized off that trade's own
    ATR-stop distance, capped at `max_position_pct` of equity so a very
    tight stop can't imply an absurd position.
  • A concurrency cap — at most `max_concurrent_positions` open at once;
    once full, new candidates are skipped, not force-fit.
  • A correlation cap — before admitting a new trade, count how many
    currently open positions have >= `corr_threshold` trailing daily-return
    correlation with it (computed from the same price panel the scan ran
    over); reject if that count is already at `max_correlated_cluster`. This
    is what actually differs from just capping position count: five
    simultaneous fades in the same crashing sector are one bet, not five.
  • An equity curve and max drawdown, computed from an event-driven walk
    (entries and exits processed in true chronological order, so freed
    capital/slots from an exit are available to same-day-or-later entries).

Candidates are admitted in (entry_date, conviction desc) order — best ideas
each day get capital first when the book is constrained.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from llmfin.backtest import DB_PATH, ExitConfig, ScanConfig, collect_events, simulate


@dataclass
class PortfolioConfig:
    starting_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 1.0      # % of CURRENT equity risked if the stop is hit
    max_position_pct: float = 20.0       # hard cap on any single position, as % of equity
    max_concurrent_positions: int = 10
    max_correlated_cluster: int = 2      # max already-open positions correlated with a candidate
    corr_threshold: float = 0.6
    corr_lookback_days: int = 60


def _pivot_returns(panel: pd.DataFrame, symbols: set[str]) -> pd.DataFrame:
    """Daily-return matrix (date x symbol), restricted to the traded
    universe -- pivoting the full ~2000-symbol panel would be needless work
    when only a few dozen symbols ever actually appear in the trade list."""
    sub = panel[panel["symbol"].isin(symbols)]
    close = sub.pivot(index="date", columns="symbol", values="close").sort_index()
    return close.pct_change()


def _correlation(returns: pd.DataFrame, sym_a: str, sym_b: str, as_of: str, lookback: int) -> Optional[float]:
    if sym_a == sym_b or sym_a not in returns.columns or sym_b not in returns.columns:
        return None
    window = returns.loc[:as_of].tail(lookback)[[sym_a, sym_b]].dropna()
    if len(window) < max(10, lookback // 3):  # not enough overlapping history to trust it
        return None
    c = window[sym_a].corr(window[sym_b])
    return None if pd.isna(c) else float(c)


def run_portfolio(trades: pd.DataFrame, panel: pd.DataFrame, cfg: PortfolioConfig) -> dict:
    """Walk backtest.simulate()'s trade list through one shared book (see
    module docstring). `panel` is the same corporate-action-adjusted price
    panel the scan ran over -- used only for the correlation cap."""
    if trades.empty:
        return {"trades": 0, "accepted": 0, "equity_curve": [], "final_equity": cfg.starting_capital}

    returns = _pivot_returns(panel, set(trades["symbol"].unique()))
    candidates = trades.sort_values(["entry_date", "conviction"], ascending=[True, False]).to_dict("records")

    equity = cfg.starting_capital
    open_positions: list[dict] = []
    equity_curve: list[dict] = [{"date": candidates[0]["entry_date"], "equity": round(equity, 2)}]
    accepted: list[dict] = []
    rejected_concurrency = rejected_correlation = 0

    i = 0
    while i < len(candidates) or open_positions:
        next_entry = candidates[i] if i < len(candidates) else None
        next_exit = min(open_positions, key=lambda p: p["exit_date"]) if open_positions else None

        # Ties resolved exit-first: capital freed today is available to a
        # same-day entry, matching how a desk would actually work the book.
        if next_exit is not None and (next_entry is None or next_exit["exit_date"] <= next_entry["entry_date"]):
            equity += next_exit["size_dollars"] * (next_exit["pnl_pct"] / 100.0)
            open_positions.remove(next_exit)
            equity_curve.append({"date": next_exit["exit_date"], "equity": round(equity, 2)})
            continue

        cand = candidates[i]
        i += 1

        if len(open_positions) >= cfg.max_concurrent_positions:
            rejected_concurrency += 1
            continue

        corr_count = sum(
            1
            for pos in open_positions
            if (c := _correlation(returns, cand["symbol"], pos["symbol"], cand["entry_date"], cfg.corr_lookback_days))
            is not None
            and abs(c) >= cfg.corr_threshold
        )
        if corr_count >= cfg.max_correlated_cluster:
            rejected_correlation += 1
            continue

        stop_distance_pct = max(float(cand["stop_distance_pct"]), 0.1)  # guard against a ~0 ATR trade
        risk_dollars = equity * cfg.risk_per_trade_pct / 100.0
        size = min(risk_dollars / (stop_distance_pct / 100.0), equity * cfg.max_position_pct / 100.0)

        open_positions.append(
            {
                "symbol": cand["symbol"],
                "exit_date": cand["exit_date"],
                "size_dollars": size,
                "pnl_pct": cand["pnl_pct"],
            }
        )
        accepted.append({**cand, "size_dollars": round(size, 2)})

    curve = pd.DataFrame(equity_curve).drop_duplicates("date", keep="last").sort_values("date")
    running_max = curve["equity"].cummax()
    drawdown_pct = (curve["equity"] - running_max) / running_max * 100

    return {
        "trades_considered": len(trades),
        "accepted": len(accepted),
        "rejected_concurrency_cap": rejected_concurrency,
        "rejected_correlation_cap": rejected_correlation,
        "starting_capital": cfg.starting_capital,
        "final_equity": round(float(curve["equity"].iloc[-1]), 2),
        "total_return_pct": round(float((curve["equity"].iloc[-1] / cfg.starting_capital - 1) * 100), 2),
        "max_drawdown_pct": round(float(drawdown_pct.min()), 2),
        "equity_curve": curve.to_dict("records"),
        "config": asdict(cfg),
    }


def run_portfolio_backtest(
    exit_cfg: ExitConfig,
    portfolio_cfg: PortfolioConfig,
    limit: int = 10,
    conviction_min: float = 0.5,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db_path: Path = DB_PATH,
    adjust_splits: bool = True,
    scan_cfg: Optional[ScanConfig] = None,
) -> dict:
    """End-to-end: scan + signals (collect_events), replay exits
    (simulate), then sequence the resulting trades through one shared book
    (run_portfolio)."""
    scan_cfg = scan_cfg or ScanConfig()
    data = collect_events(limit, conviction_min, db_path, adjust_splits, scan_cfg)
    events = data["events"]
    if start:
        events = [e for e in events if e["scan_date"] >= start]
    if end:
        events = [e for e in events if e["scan_date"] <= end]
    trades = simulate(events, data["bench"], exit_cfg)
    return run_portfolio(trades, data["panel"], portfolio_cfg)


def main() -> None:
    p = argparse.ArgumentParser(description="Portfolio-level backtest: capital, sizing, and correlation caps over backtest.py's per-trade engine")
    p.add_argument("--horizon", type=int, default=10)
    p.add_argument("--stop", type=float, default=1.5)
    p.add_argument("--target", type=float, default=2.5)
    p.add_argument("--entry", choices=["open", "pullback"], default="open")
    p.add_argument("--cost-pct", type=float, default=ExitConfig().cost_pct)
    p.add_argument("--conviction", type=float, default=0.5)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--start", help="Only scan dates >= this (YYYY-MM-DD)")
    p.add_argument("--end", help="Only scan dates <= this (YYYY-MM-DD)")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--capital", type=float, default=PortfolioConfig().starting_capital)
    p.add_argument("--risk-pct", type=float, default=PortfolioConfig().risk_per_trade_pct)
    p.add_argument("--max-positions", type=int, default=PortfolioConfig().max_concurrent_positions)
    p.add_argument("--max-correlated", type=int, default=PortfolioConfig().max_correlated_cluster)
    p.add_argument("--corr-threshold", type=float, default=PortfolioConfig().corr_threshold)
    a = p.parse_args()
    exit_cfg = ExitConfig(horizon=a.horizon, stop_mult=a.stop, target_mult=a.target, entry_style=a.entry, cost_pct=a.cost_pct)
    portfolio_cfg = PortfolioConfig(
        starting_capital=a.capital,
        risk_per_trade_pct=a.risk_pct,
        max_concurrent_positions=a.max_positions,
        max_correlated_cluster=a.max_correlated,
        corr_threshold=a.corr_threshold,
    )
    result = run_portfolio_backtest(
        exit_cfg, portfolio_cfg, limit=a.limit, conviction_min=a.conviction,
        start=a.start, end=a.end, db_path=Path(a.db),
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
