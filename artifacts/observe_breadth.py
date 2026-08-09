"""OBSERVATION step. Replicates _year_regime_stats exactly, over the live window."""
import sqlite3
import numpy as np, pandas as pd
from llmfin.regime_analysis import _BENCH_LIQUIDITY_MIN_CLOSE, _BENCH_LIQUIDITY_MIN_TURNOVER
from llmfin.data_store import DB_PATH

def stats(db, start, end):
    conn = sqlite3.connect(db)
    df = pd.read_sql_query(
        "SELECT date, close, prev_close, turnover FROM daily_prices "
        "WHERE date BETWEEN ? AND ? AND prev_close > 0 AND close >= ? AND turnover >= ?",
        conn, params=(start, end, _BENCH_LIQUIDITY_MIN_CLOSE, _BENCH_LIQUIDITY_MIN_TURNOVER))
    conn.close()
    if df.empty: return None
    df["ret"] = df["close"] / df["prev_close"] - 1
    daily = df.groupby("date")["ret"].mean()
    return {
        "realized_vol_annualized_pct": round(float(daily.std(ddof=0) * np.sqrt(252) * 100), 2),
        "breadth_pct": round(float((df["ret"] > 0).groupby(df["date"]).mean().mean() * 100), 2),
        "liquid_universe_rows": len(df),
    }

print("thresholds:", _BENCH_LIQUIDITY_MIN_CLOSE, _BENCH_LIQUIDITY_MIN_TURNOVER)
for label, s, e in [("FULL live window", "2025-05-26", "2026-08-03"),
                    ("2025 (partial)",   "2025-05-26", "2025-12-31"),
                    ("2026 (partial)",   "2026-01-01", "2026-08-03")]:
    print(f"{label:18}", stats(DB_PATH, s, e))
