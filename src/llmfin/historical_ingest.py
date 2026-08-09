"""
historical_ingest.py
─────────────────────
Backfills pre-2024 NSE bhavcopy history into a SEPARATE local database, for
backtesting eras the live pipeline can't reach.

`data_store.py` reads NSE's UDiFF bhavcopy format, which has only existed
since July 2024 - it 404s for anything older. NSE's older per-day archive
(the classic "cm...bhav.csv.zip" format) covers 2010-2020 fine but lives at a
different URL with a different column schema, hence this separate module.

Deliberately writes to its own DB file (default `market_historical.db`, next
to the live `market.db`) instead of merging into the live store - the live DB
backs real trading decisions (journal, risk gate) and has no business being
mixed with a decade of backfilled history. `backtest.py --db <path>` points
at whichever store you want to test against.

Usage
─────
    python -m llmfin.historical_ingest --start 2010-01-01 --end 2015-12-31
    python -m llmfin.historical_ingest --start 2016-01-01 --end 2020-12-31 --force

Resumable: reruns skip dates already present unless --force is passed, same
as data_store.ingest_range, so an interrupted or rate-limited run just picks
up where it left off.
"""

from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from llmfin.data_store import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_HIST_DB_PATH = DATA_DIR / "market_historical.db"

# NSE's classic per-day equity bhavcopy archive (in force roughly 2000s → Jul 2024,
# superseded by the UDiFF format data_store.py reads). Confirmed reachable for
# 2010-2020 by direct request; column set gained TOTALTRADES/ISIN partway through
# but the columns we need (_COLMAP) stayed stable throughout.
OLD_BHAVCOPY_URL = (
    "https://archives.nseindia.com/content/historical/EQUITIES/"
    "{yyyy}/{mmm}/cm{ddmmmyyyy}bhav.csv.zip"
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

# Old-format column names → our schema (same daily_prices table data_store.py uses).
_COLMAP = {
    "SYMBOL": "symbol",
    "SERIES": "series",
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "PREVCLOSE": "prev_close",
    "TOTTRDQTY": "volume",
    "TOTTRDVAL": "turnover",
    "TIMESTAMP": "date",
}


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
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


def fetch_bhavcopy_old(day: date, session: Optional[requests.Session] = None) -> Optional[pd.DataFrame]:
    """Download and parse one day's old-format bhavcopy. None on weekends/holidays (404)."""
    sess = session or requests.Session()
    url = OLD_BHAVCOPY_URL.format(
        yyyy=day.strftime("%Y"),
        mmm=day.strftime("%b").upper(),
        ddmmmyyyy=day.strftime("%d%b%Y").upper(),
    )
    resp = sess.get(url, headers=_HEADERS, timeout=30)
    if resp.status_code == 404:
        return None  # weekend / market holiday
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = zf.namelist()[0]
        df = pd.read_csv(zf.open(csv_name))
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in _COLMAP if c not in df.columns]
    if missing:
        raise ValueError(f"Old bhavcopy format changed for {day} - missing columns: {missing}")

    df = df[list(_COLMAP)].rename(columns=_COLMAP)
    df = df[df["series"].isin(["EQ", "BE"])].copy()  # cash-equity series only, same as data_store
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y").dt.strftime("%Y-%m-%d")
    return df


def ingest_historical_range(
    start: date,
    end: date,
    db_path: Path = DEFAULT_HIST_DB_PATH,
    force: bool = False,
) -> dict:
    """Backfill [start, end] (inclusive) of old-format bhavcopy into db_path."""
    conn = _connect(db_path)
    have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_prices")}
    sess = requests.Session()
    try:
        sess.get("https://www.nseindia.com/", headers=_HEADERS, timeout=15)
    except requests.RequestException:
        pass  # best-effort cookie warm-up; archives host works without it too

    loaded, skipped, holidays, failed = 0, 0, 0, []
    day = start
    while day <= end:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        key = day.strftime("%Y-%m-%d")
        if key in have and not force:
            skipped += 1
            day += timedelta(days=1)
            continue
        try:
            df = fetch_bhavcopy_old(day, sess)
        except Exception as exc:
            logger.warning("Failed %s: %s", key, exc)
            failed.append(key)
            day += timedelta(days=1)
            time.sleep(1.0)
            continue
        if df is None:
            holidays += 1
        else:
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
        day += timedelta(days=1)
        time.sleep(0.6)  # be polite to NSE's archive host

    conn.execute("DROP TABLE IF EXISTS _staging")
    conn.commit()
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM daily_prices").fetchone()
    n_dates = conn.execute("SELECT COUNT(DISTINCT date) FROM daily_prices").fetchone()[0]
    conn.close()
    return {
        "days_loaded": loaded,
        "days_already_present": skipped,
        "weekends_holidays_skipped": holidays,
        "failed_dates": failed,
        "db_date_range": date_range,
        "total_dates_in_db": n_dates,
        "db_path": str(db_path),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill pre-2024 NSE bhavcopy into a historical DB")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--db", default=str(DEFAULT_HIST_DB_PATH), help="Path to the historical SQLite DB")
    parser.add_argument("--force", action="store_true", help="Re-download dates already present")
    args = parser.parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    summary = ingest_historical_range(start, end, db_path=Path(args.db), force=args.force)
    print("\nHistorical ingest complete:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
