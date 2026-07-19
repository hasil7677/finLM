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
from typing import Optional

import numpy as np
import pandas as pd

from llmfin.data_store import DB_PATH
from llmfin.indicators import compute_indicators
from llmfin.signals import run_models

# Scanner parameters — identical to scanner.py defaults so the backtest
# measures the same pipeline the MCP tools expose.
MIN_PRICE = 100.0
MIN_AVG_VOLUME = 500_000
MIN_TURNOVER = 10.0 * 1e7
MIN_VOLUME_RATIO = 1.5
MIN_ABS_CHANGE = 1.0
MIN_LOOKBACK = 60
MAX_FWD = 20  # forward days captured per event


@dataclass
class ExitConfig:
    horizon: int = 10
    stop_mult: float = 1.5
    target_mult: float = 2.5
    entry_style: str = "open"       # 'open' | 'pullback'
    pullback_atr: float = 0.5       # limit = scan close -/+ pullback_atr*ATR
    pullback_wait: int = 3          # days to wait for the fill


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
    """Vectorized point-in-time scanner (see scanner.py for the live version)."""
    g = df.groupby("symbol", sort=False)
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


def _benchmark_index(panel: pd.DataFrame) -> pd.Series:
    """Equal-weight daily-return index of the liquid universe (close>=100,
    turnover>=1cr). Cumulative, indexed by date string."""
    liq = panel[(panel["close"] >= 100) & (panel["turnover"] >= 1e7) & (panel["prev_close"] > 0)]
    daily = liq.groupby("date").apply(
        lambda g: (g["close"] / g["prev_close"] - 1).mean(), include_groups=False
    )
    return (1 + daily).cumprod()


def collect_events(limit: int = 10, conviction_min: float = 0.5) -> dict:
    """The expensive pass: scan point-in-time, run indicators + models, and
    capture forward OHLC windows. Returns everything simulate() needs."""
    panel = _load_panel()
    bench = _benchmark_index(panel)
    candidates = _pit_candidates(panel, limit)
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

    return {"events": events, "bench": bench}


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

        pnl = sign * (exit_price - entry) / entry * 100

        d0, d1 = ev["fwd_dates"][e_idx], ev["fwd_dates"][x_idx]
        try:
            mkt = (bench.loc[d1] / bench.loc[d0] - 1) * 100
        except KeyError:
            mkt = 0.0
        alpha = pnl - sign * mkt               # long alpha = pnl - mkt; short = pnl + mkt

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
                "reason": reason,
                "days_held": x_idx - e_idx + 1,
                "pnl_pct": round(pnl, 3),
                "mkt_pct": round(float(mkt), 3),
                "alpha_pct": round(float(alpha), 3),
            }
        )
    return pd.DataFrame(out)


def stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0}
    pnl, alpha = trades["pnl_pct"].to_numpy(), trades["alpha_pct"].to_numpy()
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    return {
        "trades": len(trades),
        "win_rate_pct": round(float((pnl > 0).mean() * 100), 1),
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
    _cache: dict = {},
) -> dict:
    key = (limit, conviction_min)
    if key not in _cache:
        _cache.clear()
        _cache[key] = collect_events(limit, conviction_min)
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
        "candidates_per_day": limit,
        "conviction_min": conviction_min,
        "window": [start or "begin", end or "latest"],
        "scan_days": int(trades["scan_date"].nunique()) if not trades.empty else 0,
    }
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest the deterministic core on local bhavcopy history")
    p.add_argument("--horizon", type=int, default=10)
    p.add_argument("--stop", type=float, default=1.5, help="ATR multiple for the stop")
    p.add_argument("--target", type=float, default=2.5, help="ATR multiple for the target")
    p.add_argument("--entry", choices=["open", "pullback"], default="open")
    p.add_argument("--conviction", type=float, default=0.5)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--start", help="Only scan dates >= this (YYYY-MM-DD)")
    p.add_argument("--end", help="Only scan dates <= this (YYYY-MM-DD)")
    a = p.parse_args()
    cfg = ExitConfig(horizon=a.horizon, stop_mult=a.stop, target_mult=a.target, entry_style=a.entry)
    print(json.dumps(run(cfg, limit=a.limit, conviction_min=a.conviction, start=a.start, end=a.end), indent=2))


if __name__ == "__main__":
    main()
