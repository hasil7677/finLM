"""Integration test: verifies corporate-action back-adjustment is actually
wired into the live data path (data_store.load_history / latest_snapshot),
not just available in the standalone corporate_actions module.

Builds a throwaway sqlite DB shaped like daily_prices, points data_store at
it via monkeypatch, and checks a synthetic 2:1 split comes out adjusted.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from llmfin import data_store


@pytest.fixture
def synthetic_db(tmp_path, monkeypatch):
    db_path = tmp_path / "market_test.db"
    monkeypatch.setattr(data_store, "DB_PATH", db_path)

    dates = pd.bdate_range("2020-01-01", periods=44).strftime("%Y-%m-%d").tolist()
    # 40 steady days at 100, then an overnight 2:1 split, three normal days after.
    closes = [100.0] * 40 + [50.0, 50.5, 49.5, 50.2]
    prev_closes = [100.0] + closes[:-1]
    volumes = [10_000] * 40 + [12_000, 10_000, 10_000, 10_000]

    conn = data_store._connect()  # creates the daily_prices schema
    rows = [
        ("SPLITCO", d, "EQ", c, c, c, c, pc, v, c * v)
        for d, c, pc, v in zip(dates, closes, prev_closes, volumes)
    ]
    conn.executemany(
        """INSERT INTO daily_prices
           (symbol, date, series, open, high, low, close, prev_close, volume, turnover)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def test_load_history_back_adjusts_split(synthetic_db):
    df = data_store.load_history("SPLITCO", lookback_days=44)
    # Pre-split rows should now read ~50 (adjusted), not the raw 100 printed at the time.
    pre_split = df.iloc[0]
    assert pre_split["close"] == pytest.approx(50.0)
    # Post-split rows are untouched.
    assert df.iloc[-1]["close"] == pytest.approx(50.2)


def test_latest_snapshot_uses_adjusted_prev_close(synthetic_db):
    snap = data_store.latest_snapshot()
    row = snap[snap["symbol"] == "SPLITCO"].iloc[0]
    # Latest day is 50.2 vs an adjusted prior close (~49.5), not the raw
    # cross-split prev_close of 50.0 -- both are close to 50 so this mainly
    # guards against a NaN/zero prev_close breaking change_pct entirely.
    assert row["prev_close"] == pytest.approx(49.5)
    assert abs(row["close"] - row["prev_close"]) / row["prev_close"] * 100 < 5
