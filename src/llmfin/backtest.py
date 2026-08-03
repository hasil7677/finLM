"""
backtest.py
───────────
Point-in-time backtest of the deterministic core: scanner → alpha models →
exits. Measures whether the intelligence layer's spine has any edge before an
LLM ever touches it.

Honesty rules (the whole point):
  • Scan day D uses ONLY data ≤ D (rolling stats shifted, no peeking).
  • Bhavcopy is EOD, so the earliest you could act on a scan is the NEXT
    open: entries are at D+1 open (or a pullback limit), never D close.
  • Stop and target are ATR-at-D distances applied from the actual entry.
  • If stop AND target are both inside one day's range, assume the STOP hit
    first (conservative).
  • If the entry day opens beyond the stop (gap through), you're filled at
    the open, not at your stop price.
  • Every trade is also reported as ALPHA vs an equal-weight liquid-universe
    benchmark over the same window — a "short edge" that is just the whole
    market falling is beta, not skill.

Architecture: collect_events() runs the expensive part (scan + indicators +
signals) once; simulate() replays exits under any ExitConfig cheaply, so a
parameter sweep doesn't re-scan. Use --start/--end for train/test splits.

Usage
─────
    llmfin-backtest
    llmfin-backtest --horizon 5 --stop 1.0 --target 3.0 --entry pullback
    llmfin-backtest --end 2026-01-31            # train window
    llmfin-backtest --start 2026-02-01          # held-out test window
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from llmfin.corporate_actions import adjust_corporate_actions
from llmfin.data_store import DB_PATH
from llmfin.indicators import compute_indicators
from llmfin.signals import run_models

MIN_LOOKBACK = 60
MAX_FWD = 20  # forward days captured per event


@dataclass
class ScanConfig:
    """Point-in-time scanner thresholds — identical to scanner.py's defaults,
    but exposed as a config so old eras (much lower NSE turnover/prices) can
    be recalibrated without editing source."""
    min_price: float = 100.0
    min_avg_volume: float = 500_000
    min_turnover: float = 10.0 * 1e7
    min_volume_ratio: float = 1.5
    min_abs_change: float = 1.0


@dataclass
class ExitConfig:
    horizon: int = 10
    stop_mult: float = 1.5
    target_mult: float = 2.5
    entry_style: str = "open"       # 'open' | 'pullback'
    pullback_atr: float = 0.5       # limit = scan close -/+ pullback_atr*ATR
    pullback_wait: int = 3          # days to wait for the fill
    cost_pct: float = 0.4           # round-trip cost (brokerage + STT + slippage), see
                                     # CLAUDE.md §7 -- subtracted from every trade's pnl
                                     # so pnl_pct/alpha_pct are what a trade would actually
                                     # keep, not a mental haircut applied after the fact


def _load_panel(db_path: Path = DB_PATH, adjust_splits: bool = True) -> tuple[pd.DataFrame, list[dict]]:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, prev_close, volume, turnover "
        "FROM daily_prices ORDER BY symbol, date",
        conn,
    )
    conn.close()
    if adjust_splits:
        df, adjustments = adjust_corporate_actions(df)
    else:
        adjustments = []
    return df, adjustments


def _pit_candidates(df: pd.DataFrame, limit: int, cfg: ScanConfig) -> pd.DataFrame:
    """Vectorized point-in-time scanner (see scanner.py for the live version)."""
    g = df.groupby("symbol", sort=False)
    df = df.assign(
        avg_volume_20=g["volume"].transform(lambda s: s.shift(1).rolling(20).mean()),
        hist_len=g.cumcount(),
    )
    df = df[df["hist_len"] >= MIN_LOOKBACK]
    df = df[(df["prev_close"] > 0) & (df["close"] >= cfg.min_price)]
    df = df[(df["avg_volume_20"] >= cfg.min_avg_volume) & (df["turnover"] >= cfg.min_turnover)]
    df = df.assign(
        change_pct=(df["close"] - df["prev_close"]) / df["prev_close"] * 100,
        volume_ratio=df["volume"] / df["avg_volume_20"],
    )
    df = df[(df["volume_ratio"] >= cfg.min_volume_ratio) & (df["change_pct"].abs() >= cfg.min_abs_change)]
    df = df.assign(score=df["change_pct"].abs() * 0.5 + df["volume_ratio"].clip(upper=10))
    df = df.sort_values(["date", "score"], ascending=[True, False])
    return df.groupby("date", sort=False).head(limit)


def _benchmark_index(panel: pd.DataFrame) -> pd.Series:
    """Equal-weight daily-return index of the liquid universe (close>=100,
    turnover>=1cr). Cumulative, indexed by date string."""
    liq = panel[(panel["close"] >= 100) & (panel["turnover"] >= 1e7) & (panel["prev_close"] > 0)]
    daily = liq.groupby("date").apply(
        lambda g: (g["close"] / g["prev_close"] - 1).mean(), include_groups=False
    )
    return (1 + daily).cumprod()


def collect_events(
    limit: int = 10,
    conviction_min: float = 0.5,
    db_path: Path = DB_PATH,
    adjust_splits: bool = True,
    scan_cfg: Optional[ScanConfig] = None,
) -> dict:
    """The expensive pass: scan point-in-time, run indicators + models, and
    capture forward OHLC windows. Returns everything simulate() needs."""
    scan_cfg = scan_cfg or ScanConfig()
    panel, adjustments = _load_panel(db_path, adjust_splits)
    bench = _benchmark_index(panel)
    candidates = _pit_candidates(panel, limit, scan_cfg)
    by_symbol = {s: sdf.reset_index(drop=True) for s, sdf in panel.groupby("symbol", sort=False)}

    events: list[dict] = []
    for row in candidates.itertuples():
        sdf = by_symbol[row.symbol]
        pos = sdf.index[sdf["date"] == row.date]
        if len(pos) == 0:
            continue
        pos = int(pos[0])
        fwd = sdf.iloc[pos + 1 : pos + 1 + MAX_FWD]
        if fwd.empty:
            continue

        hist = sdf.iloc[max(0, pos - 250) : pos + 1][["date", "open", "high", "low", "close", "volume"]]
        ind = compute_indicators(hist.reset_index(drop=True))
        atr = float(ind.iloc[-1]["atr_14"])
        if np.isnan(atr) or atr <= 0:
            continue

        sigs = [s for s in run_models(ind) if s.direction != "HOLD" and abs(s.conviction) >= conviction_min]
        if not sigs:
            continue

        ev_base = {
            "scan_date": str(row.date),
            "symbol": row.symbol,
            "scan_close": float(row.close),
            "change_pct": float(row.change_pct),
            "volume_ratio": float(row.volume_ratio),
            "atr": atr,
            "fwd_open": fwd["open"].to_numpy(float),
            "fwd_high": fwd["high"].to_numpy(float),
            "fwd_low": fwd["low"].to_numpy(float),
            "fwd_close": fwd["close"].to_numpy(float),
            "fwd_dates": fwd["date"].tolist(),
        }
        for s in sigs:
            events.append({**ev_base, "model": s.model, "direction": s.direction, "conviction": s.conviction})

    return {"events": events, "bench": bench, "panel": panel, "corporate_action_adjustments": adjustments}


def _entry_fill(ev: dict, cfg: ExitConfig, sign: int) -> Optional[tuple[float, int]]:
    """Returns (entry_price, fwd_index_of_entry_day) or None if never filled."""
    if cfg.entry_style == "open":
        return float(ev["fwd_open"][0]), 0
    # Pullback: limit inside the move — below scan close for longs, above for
    # shorts — wait up to pullback_wait days for the market to come to you.
    limit = ev["scan_close"] - sign * cfg.pullback_atr * ev["atr"]
    n = min(cfg.pullback_wait, len(ev["fwd_open"]))
    for i in range(n):
        o = ev["fwd_open"][i]
        if sign * (o - limit) <= 0:      # opened at/through the limit — filled at open
            return float(o), i
        if sign == 1 and ev["fwd_low"][i] <= limit:
            return float(limit), i
        if sign == -1 and ev["fwd_high"][i] >= limit:
            return float(limit), i
    return None


def simulate(events: list[dict], bench: pd.Series, cfg: ExitConfig) -> pd.DataFrame:
    """Replay exits for every event under one config. Returns a trades frame."""
    out = []
    for ev in events:
        sign = 1 if ev["direction"] == "BUY" else -1
        fill = _entry_fill(ev, cfg, sign)
        if fill is None:
            continue
        entry, e_idx = fill
        if entry <= 0:
            continue
        stop = entry - sign * cfg.stop_mult * ev["atr"]
        target = entry + sign * cfg.target_mult * ev["atr"]

        exit_price, reason, x_idx = None, "time", None
        last = min(e_idx + cfg.horizon, len(ev["fwd_close"])) - 1
        for i in range(e_idx, last + 1):
            o, h, l = ev["fwd_open"][i], ev["fwd_high"][i], ev["fwd_low"][i]
            if i == e_idx and sign * (o - stop) <= 0:
                exit_price, reason, x_idx = o, "stop", i
                break
            hit_stop = l <= stop if sign == 1 else h >= stop
            hit_target = h >= target if sign == 1 else l <= target
            if hit_stop:                       # stop-first assumption
                exit_price, reason, x_idx = stop, "stop", i
                break
            if hit_target:
                exit_price, reason, x_idx = target, "target", i
                break
        if exit_price is None:
            exit_price, x_idx = float(ev["fwd_close"][last]), last

        gross_pnl = sign * (exit_price - entry) / entry * 100
        pnl = gross_pnl - cfg.cost_pct          # net of round-trip transaction cost

        d0, d1 = ev["fwd_dates"][e_idx], ev["fwd_dates"][x_idx]
        try:
            mkt = (bench.loc[d1] / bench.loc[d0] - 1) * 100
        except KeyError:
            mkt = 0.0
        alpha = pnl - sign * mkt               # long alpha = pnl - mkt; short = pnl + mkt (net of cost)

        out.append(
            {
                "scan_date": ev["scan_date"],
                "symbol": ev["symbol"],
                "model": ev["model"],
                "direction": ev["direction"],
                "conviction": ev["conviction"],
                "change_pct": ev["change_pct"],
                "volume_ratio": ev["volume_ratio"],
                "entry": round(entry, 2),
                "exit": round(float(exit_price), 2),
                "entry_date": d0,
                "exit_date": d1,
                # ATR-stop distance as % of entry -- the position-sizing input a
                # portfolio layer needs to size "risk X% of equity per trade".
                "stop_distance_pct": round(cfg.stop_mult * ev["atr"] / entry * 100, 3),
                "reason": reason,
                "days_held": x_idx - e_idx + 1,
                "gross_pnl_pct": round(gross_pnl, 3),
                "pnl_pct": round(pnl, 3),
                "mkt_pct": round(float(mkt), 3),
                "alpha_pct": round(float(alpha), 3),
            }
        )
    return pd.DataFrame(out)


def stats(trades: pd.DataFrame) -> dict:
    """All *_pct figures except avg_gross_pnl_pct are net of ExitConfig.cost_pct
    -- win_rate and profit_factor use net pnl, so a bucket whose gross edge is
    smaller than the round-trip cost correctly shows as unprofitable rather
    than requiring a mental haircut applied after the fact."""
    if trades.empty:
        return {"trades": 0}
    pnl, alpha = trades["pnl_pct"].to_numpy(), trades["alpha_pct"].to_numpy()
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    return {
        "trades": len(trades),
        "win_rate_pct": round(float((pnl > 0).mean() * 100), 1),
        "avg_gross_pnl_pct": round(float(trades["gross_pnl_pct"].mean()), 2),
        "avg_pnl_pct": round(float(pnl.mean()), 2),
        "avg_alpha_pct": round(float(alpha.mean()), 2),
        "median_pnl_pct": round(float(np.median(pnl)), 2),
        "profit_factor": round(float(gains / losses), 2) if losses > 0 else None,
        "avg_mkt_pct": round(float(trades["mkt_pct"].mean()), 2),
    }


def summarize(trades: pd.DataFrame) -> dict:
    buckets = {}
    for (model, direction), g in trades.groupby(["model", "direction"]):
        buckets[f"{model} {direction}"] = stats(g)
    exit_mix = trades["reason"].value_counts().to_dict() if not trades.empty else {}
    return {"all_trades": stats(trades), "by_bucket": buckets, "exit_mix": exit_mix}


def run(
    cfg: ExitConfig,
    limit: int = 10,
    conviction_min: float = 0.5,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db_path: Path = DB_PATH,
    adjust_splits: bool = True,
    scan_cfg: Optional[ScanConfig] = None,
    _cache: dict = {},
) -> dict:
    scan_cfg = scan_cfg or ScanConfig()
    key = (str(db_path), limit, conviction_min, adjust_splits, tuple(scan_cfg.__dict__.values()))
    if key not in _cache:
        _cache.clear()
        _cache[key] = collect_events(limit, conviction_min, db_path, adjust_splits, scan_cfg)
    data = _cache[key]
    events = data["events"]
    if start:
        events = [e for e in events if e["scan_date"] >= start]
    if end:
        events = [e for e in events if e["scan_date"] <= end]
    trades = simulate(events, data["bench"], cfg)
    result = summarize(trades)
    result["config"] = {
        **cfg.__dict__,
        **scan_cfg.__dict__,
        "candidates_per_day": limit,
        "conviction_min": conviction_min,
        "window": [start or "begin", end or "latest"],
        "scan_days": int(trades["scan_date"].nunique()) if not trades.empty else 0,
        "db_path": str(db_path),
    }
    adjustments = data["corporate_action_adjustments"]
    applied = [a for a in adjustments if a.get("applied")]
    flagged = [a for a in adjustments if not a.get("applied")]
    result["corporate_action_adjustments"] = {
        "applied_count": len(applied),
        "flagged_unadjusted_count": len(flagged),
        "applied_sample": applied[:20],
        "flagged_sample": flagged[:20],
    }
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest the deterministic core on local bhavcopy history")
    p.add_argument("--horizon", type=int, default=10)
    p.add_argument("--stop", type=float, default=1.5, help="ATR multiple for the stop")
    p.add_argument("--target", type=float, default=2.5, help="ATR multiple for the target")
    p.add_argument("--entry", choices=["open", "pullback"], default="open")
    p.add_argument("--cost-pct", type=float, default=ExitConfig().cost_pct, help="Round-trip transaction cost, as a percent of entry price")
    p.add_argument("--conviction", type=float, default=0.5)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--start", help="Only scan dates >= this (YYYY-MM-DD)")
    p.add_argument("--end", help="Only scan dates <= this (YYYY-MM-DD)")
    p.add_argument("--db", default=str(DB_PATH), help="Path to the SQLite market DB (default: live market.db)")
    p.add_argument("--min-price", type=float, default=ScanConfig().min_price)
    p.add_argument("--min-avg-volume", type=float, default=ScanConfig().min_avg_volume)
    p.add_argument("--min-turnover", type=float, default=ScanConfig().min_turnover, help="Rupees, not crore")
    p.add_argument("--min-volume-ratio", type=float, default=ScanConfig().min_volume_ratio)
    p.add_argument("--min-abs-change", type=float, default=ScanConfig().min_abs_change)
    p.add_argument("--no-adjust-splits", action="store_true", help="Disable corporate-action back-adjustment")
    a = p.parse_args()
    cfg = ExitConfig(horizon=a.horizon, stop_mult=a.stop, target_mult=a.target, entry_style=a.entry, cost_pct=a.cost_pct)
    scan_cfg = ScanConfig(
        min_price=a.min_price,
        min_avg_volume=a.min_avg_volume,
        min_turnover=a.min_turnover,
        min_volume_ratio=a.min_volume_ratio,
        min_abs_change=a.min_abs_change,
    )
    result = run(
        cfg,
        limit=a.limit,
        conviction_min=a.conviction,
        start=a.start,
        end=a.end,
        db_path=Path(a.db),
        adjust_splits=not a.no_adjust_splits,
        scan_cfg=scan_cfg,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
