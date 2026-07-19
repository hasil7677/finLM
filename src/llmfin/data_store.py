"""
data_store.py
─────────────
The free EOD data spine: NSE bhavcopy → SQLite.

Downloads NSE's daily UDiFF equity bhavcopy (official, free, published every
evening) and ingests it into a local SQLite database. Everything else — the
scanner, EOD review, averages — reads from this DB, so the screening layer
needs NO broker API or paid data subscription.

Usage
─────
    llmfin-ingest              # backfill/refresh the last 60 trading days
    llmfin-ingest --days 120   # deeper backfill

Data lives in ~/.llmfin/market.db (override with LLMFIN_DATA_DIR).
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sqlite3
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("LLMFIN_DATA_DIR", "~/.llmfin")).expanduser()
DB_PATH = DATA_DIR / "market.db"

# NSE's UDiFF common-market bhavcopy archive (format in force since July 2024).
BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# UDiFF column names → our schema
_COLMAP = {
    "TckrSymb": "symbol",
    "SctySrs": "series",
    "TradDt": "date",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "PrvsClsgPric": "prev_close",
    "TtlTradgVol": "volume",
    "TtlTrfVal": "turnover",
}


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol      TEXT NOT NULL,
            date        TEXT NOT NULL,   -- YYYY-MM-DD
            series      TEXT,
            open        REAL, high REAL, low REAL, close REAL,
            prev_close  REAL,
            volume      INTEGER,
            turnover    REAL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(date)")
    return conn


def fetch_bhavcopy(day: date, session: Optional[requests.Session] = None) -> Optional[pd.DataFrame]:
    """Download and parse one day's bhavcopy. Returns None on weekends/holidays (404)."""
    sess = session or requests.Session()
    url = BHAVCOPY_URL.format(ymd=day.strftime("%Y%m%d"))
    resp = sess.get(url, headers=_HEADERS, timeout=30)
    if resp.status_code == 404:
        return None  # weekend / market holiday
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = zf.namelist()[0]
        df = pd.read_csv(zf.open(csv_name))

    missing = [c for c in _COLMAP if c not in df.columns]
    if missing:
        raise ValueError(f"Bhavcopy format changed — missing columns: {missing}")

    df = df[list(_COLMAP)].rename(columns=_COLMAP)
    df = df[df["series"].isin(["EQ", "BE"])].copy()  # cash-equity series only
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def ingest_range(days_back: int = 60, force: bool = False) -> dict:
    """Backfill the last `days_back` calendar days of bhavcopy into SQLite."""
    conn = _connect()
    have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_prices")}
    sess = requests.Session()
    # Warm up NSE's cookie gate; archives host usually doesn't need it, but be safe.
    try:
        sess.get("https://www.nseindia.com/", headers=_HEADERS, timeout=15)
    except requests.RequestException:
        pass

    today = date.today()
    loaded, skipped, holidays = 0, 0, 0
    for offset in range(days_back, -1, -1):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        key = day.strftime("%Y-%m-%d")
        if key in have and not force:
            skipped += 1
            continue
        try:
            df = fetch_bhavcopy(day, sess)
        except Exception as exc:
            logger.warning("Failed %s: %s", key, exc)
            continue
        if df is None:
            holidays += 1
            continue
        df.to_sql("_staging", conn, if_exists="replace", index=False)
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_prices
            (symbol, date, series, open, high, low, close, prev_close, volume, turnover)
            SELECT symbol, date, series, open, high, low, close, prev_close, volume, turnover
            FROM _staging
            """
        )
        conn.commit()
        loaded += 1
        logger.info("Ingested %s (%d rows)", key, len(df))
        time.sleep(0.5)  # be polite to NSE

    conn.execute("DROP TABLE IF EXISTS _staging")
    conn.commit()
    latest = conn.execute("SELECT MAX(date) FROM daily_prices").fetchone()[0]
    n_symbols = conn.execute(
        "SELECT COUNT(DISTINCT symbol) FROM daily_prices WHERE date = ?", (latest,)
    ).fetchone()[0] if latest else 0
    conn.close()
    return {
        "days_loaded": loaded,
        "days_already_present": skipped,
        "weekends_holidays_skipped": holidays,
        "latest_date": latest,
        "symbols_on_latest_date": n_symbols,
        "db_path": str(DB_PATH),
    }


def load_history(symbol: str, lookback_days: int = 250) -> pd.DataFrame:
    """Daily OHLCV history for one symbol from the local DB (oldest→newest)."""
    conn = _connect()
    df = pd.read_sql_query(
        """
        SELECT date, open, high, low, close, volume FROM daily_prices
        WHERE symbol = ? ORDER BY date DESC LIMIT ?
        """,
        conn,
        params=(symbol.upper(), lookback_days),
    )
    conn.close()
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df


def latest_snapshot() -> pd.DataFrame:
    """Latest trading day's rows joined with each symbol's 20-day average volume
    and 20-day high/low — the scanner's raw material."""
    conn = _connect()
    latest = conn.execute("SELECT MAX(date) FROM daily_prices").fetchone()[0]
    if latest is None:
        conn.close()
        raise RuntimeError("Market DB is empty — run `llmfin-ingest` first.")
    df = pd.read_sql_query(
        """
        WITH ranked AS (
            SELECT symbol, date, close, volume,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM daily_prices
        ),
        stats AS (
            SELECT symbol,
                   AVG(volume) AS avg_volume_20,
                   AVG(close)  AS avg_close_20
            FROM ranked WHERE rn BETWEEN 2 AND 21
            GROUP BY symbol
        )
        SELECT d.symbol, d.date, d.open, d.high, d.low, d.close, d.prev_close,
               d.volume, d.turnover, s.avg_volume_20, s.avg_close_20
        FROM daily_prices d
        JOIN stats s ON s.symbol = d.symbol
        WHERE d.date = ?
        """,
        conn,
        params=(latest,),
    )
    conn.close()
    return df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest NSE bhavcopy into the local market DB")
    parser.add_argument("--days", type=int, default=60, help="Calendar days to backfill (default 60)")
    parser.add_argument("--force", action="store_true", help="Re-download days already present")
    args = parser.parse_args()
    summary = ingest_range(days_back=args.days, force=args.force)
    print("\nIngest complete:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
