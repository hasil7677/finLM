"""
regime_analysis.py
───────────────────
Investigates why the mean_reversion SELL (fade) alpha found across 2010-2020
varies year to year (roughly +1.1% to +2.5%/trade per the historical
backtest loop) instead of being flat, and whether that variation tracks a
regime signal (realized volatility, market breadth) closely enough to be
worth sizing positions on.

Runs one year at a time rather than loading the full 2010-2020 history into
memory at once (backtest.py's _load_panel has no date filter in its SQL
query -- 11 years x ~2500 symbols is ~4.15M rows, which is too much for this
machine's ~6GB RAM once corporate_actions.py's intermediate copies are
accounted for). Each year gets its own small on-disk SQLite slice (that
year plus a ~150-calendar-day lookback buffer for indicator warmup),
built with a pure-SQL ATTACH/INSERT so the slicing itself never touches
Python memory, then backtest.run() runs against that slice exactly as it
would against the full DB.

Usage
─────
    llmfin-regime-analysis --db ~/.llmfin/market_historical.db --start-year 2010 --end-year 2020
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from llmfin.backtest import DB_PATH, ExitConfig, ScanConfig, run

# A ~150-calendar-day (~100 trading day) buffer covers backtest.py's
# MIN_LOOKBACK=60 gate plus every indicator's own warmup (ATR/RSI need 14,
# EMA50 converges well before that) without paying for a full year of extra
# rows per slice.
LOOKBACK_BUFFER_DAYS = 150

_BENCH_LIQUIDITY_MIN_CLOSE = 100.0
_BENCH_LIQUIDITY_MIN_TURNOVER = 1e7


def _year_slice_db(source_db: Path, year: int, dest_db: Path) -> None:
    """Copy `year` plus its lookback buffer from source_db into dest_db via
    pure SQL (no pandas round-trip) so the slicing step itself stays cheap."""
    window_start = (date(year, 1, 1) - timedelta(days=LOOKBACK_BUFFER_DAYS)).isoformat()
    window_end = f"{year}-12-31"
    if dest_db.exists():
        dest_db.unlink()
    conn = sqlite3.connect(dest_db)
    conn.execute(f"ATTACH DATABASE '{source_db.as_posix()}' AS src")
    conn.execute(
        """CREATE TABLE daily_prices (
            symbol TEXT, date TEXT, series TEXT, open REAL, high REAL, low REAL,
            close REAL, prev_close REAL, volume INTEGER, turnover REAL
        )"""
    )
    conn.execute(
        "INSERT INTO daily_prices SELECT symbol, date, series, open, high, low, close, "
        "prev_close, volume, turnover FROM src.daily_prices WHERE date BETWEEN ? AND ?",
        (window_start, window_end),
    )
    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.close()


def _year_regime_stats(db_path: Path, year: int) -> dict:
    """Realized volatility and breadth for `year`, computed directly over
    the same liquid universe backtest.py's benchmark index uses, so both
    are measuring the same "market" the fade trades were scored against."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, close, prev_close, turnover FROM daily_prices "
        "WHERE date BETWEEN ? AND ? AND prev_close > 0 AND close >= ? AND turnover >= ?",
        conn,
        params=(f"{year}-01-01", f"{year}-12-31", _BENCH_LIQUIDITY_MIN_CLOSE, _BENCH_LIQUIDITY_MIN_TURNOVER),
    )
    conn.close()
    if df.empty:
        return {"realized_vol_annualized_pct": None, "breadth_pct": None, "liquid_universe_rows": 0}

    df["ret"] = df["close"] / df["prev_close"] - 1
    daily = df.groupby("date")["ret"].mean()  # equal-weight daily return, same convention as _benchmark_index
    realized_vol = float(daily.std(ddof=0) * np.sqrt(252) * 100)

    breadth = float((df["ret"] > 0).groupby(df["date"]).mean().mean() * 100)  # avg daily % of names advancing

    return {
        "realized_vol_annualized_pct": round(realized_vol, 2),
        "breadth_pct": round(breadth, 2),
        "liquid_universe_rows": len(df),
    }


def yearly_fade_alpha_vs_regime(
    source_db: Path,
    start_year: int,
    end_year: int,
    exit_cfg: Optional[ExitConfig] = None,
    scan_cfg: Optional[ScanConfig] = None,
) -> dict:
    """Per-year mean_reversion SELL alpha next to realized volatility and
    breadth for that year, plus the Pearson correlation across years between
    fade alpha and each regime metric.

    NOTE ON STATISTICAL POWER: this is n=11 (one point per year). Treat any
    correlation here as a hypothesis to investigate further (e.g. a monthly
    or rolling-window version, which would have far more points), not as a
    validated sizing signal -- README.md's own working style is to state
    n plainly rather than dress up a small sample as a strong finding.
    """
    exit_cfg = exit_cfg or ExitConfig(entry_style="pullback", stop_mult=2.0, target_mult=2.5, horizon=10)
    scan_cfg = scan_cfg or ScanConfig(
        min_price=20.0, min_avg_volume=200_000, min_turnover=3e7, min_volume_ratio=1.5, min_abs_change=1.0
    )

    rows = []
    with tempfile.TemporaryDirectory(prefix="llmfin_regime_") as tmp:
        for year in range(start_year, end_year + 1):
            # Distinct filename per year, not reused-and-overwritten -- backtest.run()
            # caches collect_events() results keyed on (db_path, ...), so if every year
            # shared one path the cache would silently keep serving 2010's events for
            # every later year even after the file on disk changed underneath it.
            slice_db = Path(tmp) / f"year_slice_{year}.db"
            _year_slice_db(source_db, year, slice_db)
            result = run(
                exit_cfg,
                limit=10,
                conviction_min=0.5,
                start=f"{year}-01-01",
                end=f"{year}-12-31",
                db_path=slice_db,
                scan_cfg=scan_cfg,
            )
            fade = result["by_bucket"].get("mean_reversion SELL", {"trades": 0})
            regime = _year_regime_stats(slice_db, year)
            rows.append(
                {
                    "year": year,
                    "fade_trades": fade.get("trades", 0),
                    "fade_avg_alpha_pct": fade.get("avg_alpha_pct"),
                    "fade_win_rate_pct": fade.get("win_rate_pct"),
                    **regime,
                }
            )

    table = pd.DataFrame(rows).dropna(subset=["fade_avg_alpha_pct", "realized_vol_annualized_pct", "breadth_pct"])
    corr_vol = float(table["fade_avg_alpha_pct"].corr(table["realized_vol_annualized_pct"])) if len(table) >= 3 else None
    corr_breadth = float(table["fade_avg_alpha_pct"].corr(table["breadth_pct"])) if len(table) >= 3 else None

    return {
        "years": rows,
        "n_years_with_data": len(table),
        "corr_fade_alpha_vs_realized_vol": round(corr_vol, 3) if corr_vol is not None else None,
        "corr_fade_alpha_vs_breadth": round(corr_breadth, 3) if corr_breadth is not None else None,
        "config": {"exit": asdict(exit_cfg), "scan": asdict(scan_cfg)},
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Per-year fade alpha vs. realized volatility / breadth")
    p.add_argument("--db", default=str(DB_PATH), help="Source DB with multi-year history (e.g. market_historical.db)")
    p.add_argument("--start-year", type=int, required=True)
    p.add_argument("--end-year", type=int, required=True)
    a = p.parse_args()
    result = yearly_fade_alpha_vs_regime(Path(a.db), a.start_year, a.end_year)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
