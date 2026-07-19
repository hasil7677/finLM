"""
backtest.py
───────────
Point-in-time backtest of the deterministic core: scanner → alpha models →
ATR stop/target exits. Measures whether the intelligence layer's spine has
any edge before an LLM ever touches it.

Honesty rules (the whole point):
  • Scan day D uses ONLY data ≤ D (rolling stats shifted, no peeking).
  • Bhavcopy is EOD, so the earliest you could act on a scan is the NEXT
    open: entries are at D+1 open, never D close.
  • Stop and target are ATR-at-D distances applied from the actual entry.
  • If stop AND target are both inside one day's range, assume the STOP hit
    first (conservative).
  • If D+1 opens beyond the stop (gap through), you're filled at the open,
    not at your stop price.
  • Baseline: the same scanner candidates held passively for the horizon —
    isolates what the signal models + exits add over pure discovery.

Usage
─────
    llmfin-backtest                     # full history in the local DB
    llmfin-backtest --horizon 10 --conviction 0.5 --limit 10
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from llmfin.data_store import DB_PATH
from llmfin.indicators import compute_indicators
from llmfin.signals import run_models

# Scanner parameters — keep identical to scanner.py defaults so the backtest
# measures the same pipeline the MCP tools expose.
MIN_PRICE = 100.0
MIN_AVG_VOLUME = 500_000
MIN_TURNOVER = 10.0 * 1e7
MIN_VOLUME_RATIO = 1.5
MIN_ABS_CHANGE = 1.0
ATR_STOP, ATR_TARGET = 1.5, 2.5
MIN_LOOKBACK = 60  # trading days of history required before a symbol is scannable


@dataclass
class Trade:
    scan_date: str
    symbol: str
    model: str
    direction: str
    conviction: float
    entry: float
    exit_price: float
    exit_reason: str  # target / stop / time
    days_held: int
    pnl_pct: float


def _load_panel() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, prev_close, volume, turnover "
        "FROM daily_prices ORDER BY symbol, date",
        conn,
    )
    conn.close()
    return df


def _pit_candidates(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Vectorized point-in-time scanner: for every (symbol, date), compute the
    scan filters using only prior data, and rank per date."""
    g = df.groupby("symbol", sort=False)
    # 20-day average volume of the PRIOR 20 sessions (shifted — day D's own
    # volume is known at D close, but the average must not include D itself
    # to match latest_snapshot's rn BETWEEN 2 AND 21 window).
    df = df.assign(
        avg_volume_20=g["volume"].transform(lambda s: s.shift(1).rolling(20).mean()),
        hist_len=g.cumcount(),
    )
    df = df[df["hist_len"] >= MIN_LOOKBACK]
    df = df[(df["prev_close"] > 0) & (df["close"] >= MIN_PRICE)]
    df = df[(df["avg_volume_20"] >= MIN_AVG_VOLUME) & (df["turnover"] >= MIN_TURNOVER)]
    df = df.assign(
        change_pct=(df["close"] - df["prev_close"]) / df["prev_close"] * 100,
        volume_ratio=df["volume"] / df["avg_volume_20"],
    )
    df = df[(df["volume_ratio"] >= MIN_VOLUME_RATIO) & (df["change_pct"].abs() >= MIN_ABS_CHANGE)]
    df = df.assign(score=df["change_pct"].abs() * 0.5 + df["volume_ratio"].clip(upper=10))
    df = df.sort_values(["date", "score"], ascending=[True, False])
    return df.groupby("date", sort=False).head(limit)


def _simulate_exit(
    fwd: pd.DataFrame, direction: str, entry: float, atr: float, horizon: int
) -> tuple[float, str, int]:
    sign = 1 if direction == "BUY" else -1
    stop = entry - sign * ATR_STOP * atr
    target = entry + sign * ATR_TARGET * atr

    for i, r in enumerate(fwd.itertuples(), start=1):
        if i > horizon:
            break
        o, h, l = float(r.open), float(r.high), float(r.low)
        if i == 1 and (sign * (o - stop) <= 0):
            return o, "stop", i  # gapped through the stop at entry-day open
        if direction == "BUY":
            if l <= stop:
                return stop, "stop", i
            if h >= target:
                return target, "target", i
        else:
            if h >= stop:
                return stop, "stop", i
            if l <= target:
                return target, "target", i
    last = fwd.iloc[min(horizon, len(fwd)) - 1]
    return float(last["close"]), "time", min(horizon, len(fwd))


def run_backtest(horizon: int = 10, conviction_min: float = 0.5, limit: int = 10) -> dict:
    panel = _load_panel()
    dates = sorted(panel["date"].unique())
    candidates = _pit_candidates(panel, limit)

    # Per-symbol frames for indicator runs and forward walks
    by_symbol = {s: sdf.reset_index(drop=True) for s, sdf in panel.groupby("symbol", sort=False)}
    date_index = {d: i for i, d in enumerate(dates)}

    trades: list[Trade] = []
    baseline: list[float] = []

    for row in candidates.itertuples():
        d_idx = date_index[row.date]
        if d_idx + 1 >= len(dates):
            continue  # no next day yet — can't enter

        sdf = by_symbol[row.symbol]
        pos = sdf.index[sdf["date"] == row.date]
        if len(pos) == 0:
            continue
        pos = int(pos[0])
        fwd = sdf.iloc[pos + 1 :]
        if fwd.empty:
            continue

        entry = float(fwd.iloc[0]["open"])
        if entry <= 0:
            continue

        # Baseline: passive hold of every candidate, long, no stops
        bl_exit = float(fwd.iloc[min(horizon, len(fwd)) - 1]["close"])
        baseline.append((bl_exit - entry) / entry * 100)

        # Signals from history ≤ scan date only
        hist = sdf.iloc[max(0, pos - 250) : pos + 1][["date", "open", "high", "low", "close", "volume"]]
        ind = compute_indicators(hist.reset_index(drop=True))
        atr = float(ind.iloc[-1]["atr_14"]) if not np.isnan(ind.iloc[-1]["atr_14"]) else None
        if not atr:
            continue

        for sig in run_models(ind):
            if sig.direction == "HOLD" or abs(sig.conviction) < conviction_min:
                continue
            exit_price, reason, days = _simulate_exit(fwd, sig.direction, entry, atr, horizon)
            sign = 1 if sig.direction == "BUY" else -1
            pnl = sign * (exit_price - entry) / entry * 100
            trades.append(
                Trade(
                    scan_date=str(row.date),
                    symbol=row.symbol,
                    model=sig.model,
                    direction=sig.direction,
                    conviction=sig.conviction,
                    entry=round(entry, 2),
                    exit_price=round(exit_price, 2),
                    exit_reason=reason,
                    days_held=days,
                    pnl_pct=round(pnl, 2),
                )
            )

    def _stats(pnls: list[float], wins: Optional[int] = None) -> dict:
        if not pnls:
            return {"trades": 0}
        arr = np.array(pnls)
        gains, losses = arr[arr > 0].sum(), -arr[arr <= 0].sum()
        return {
            "trades": len(arr),
            "win_rate_pct": round(float((arr > 0).mean() * 100), 1),
            "avg_pnl_pct": round(float(arr.mean()), 2),
            "median_pnl_pct": round(float(np.median(arr)), 2),
            "total_pnl_pct": round(float(arr.sum()), 1),
            "profit_factor": round(float(gains / losses), 2) if losses > 0 else None,
            "best": round(float(arr.max()), 2),
            "worst": round(float(arr.min()), 2),
        }

    by_bucket: dict[str, dict] = {}
    for key_fn, name in [
        (lambda t: f"{t.model}", "model"),
        (lambda t: f"{t.model} · {t.direction}", "model+direction"),
    ]:
        for t in trades:
            by_bucket.setdefault(key_fn(t), [])
        for t in trades:
            by_bucket[key_fn(t)].append(t.pnl_pct)
    bucket_stats = {k: _stats(v) for k, v in by_bucket.items()}

    exit_mix: dict[str, int] = {}
    for t in trades:
        exit_mix[t.exit_reason] = exit_mix.get(t.exit_reason, 0) + 1

    return {
        "config": {
            "horizon_days": horizon,
            "conviction_min": conviction_min,
            "candidates_per_day": limit,
            "scan_days": int(candidates["date"].nunique()),
            "date_range": [str(dates[0]), str(dates[-1])],
        },
        "all_trades": _stats([t.pnl_pct for t in trades]),
        "by_bucket": bucket_stats,
        "exit_mix": exit_mix,
        "baseline_passive_hold_all_candidates": _stats(baseline),
        "trades_sample": [t.__dict__ for t in sorted(trades, key=lambda t: t.pnl_pct)[:3]]
        + [t.__dict__ for t in sorted(trades, key=lambda t: t.pnl_pct)[-3:]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the deterministic core on local bhavcopy history")
    parser.add_argument("--horizon", type=int, default=10, help="Max holding days (default 10)")
    parser.add_argument("--conviction", type=float, default=0.5, help="Min |conviction| to trade (default 0.5)")
    parser.add_argument("--limit", type=int, default=10, help="Scanner candidates per day (default 10)")
    args = parser.parse_args()
    result = run_backtest(horizon=args.horizon, conviction_min=args.conviction, limit=args.limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
