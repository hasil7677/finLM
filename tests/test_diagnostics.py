"""Tests for the standing data-anomalies diagnostic (src/llmfin/diagnostics.py),
which turns the corporate-action adjustment log into a queryable report
instead of a one-off scratch script."""

from __future__ import annotations

import pandas as pd
import pytest

from llmfin import data_store
from llmfin.diagnostics import list_data_anomalies


def _rows(symbol: str, closes: list[float], volumes: list[float], dates: list[str]) -> list[tuple]:
    rows = []
    prev = closes[0]
    for d, c, v in zip(dates, closes, volumes):
        rows.append((symbol, d, "EQ", c, c, c, c, prev, v, c * v))
        prev = c
    return rows


@pytest.fixture
def synthetic_db(tmp_path, monkeypatch):
    db_path = tmp_path / "market_test.db"
    monkeypatch.setattr(data_store, "DB_PATH", db_path)
    dates = pd.bdate_range("2020-01-01", periods=45).strftime("%Y-%m-%d").tolist()

    # SPLITCO: a clean, isolated 2:1 split at normal volume -- gets applied.
    split_closes = [100.0] * 40 + [50.0, 50.5, 49.5, 50.2, 50.1]
    split_volumes = [10_000] * 40 + [12_000, 10_000, 10_000, 10_000, 10_000]

    # CRASHCO: a real crash near a common ratio at panic volume -- flagged, not applied.
    crash_closes = [100.0] * 40 + [33.3, 33.0, 32.5, 33.8, 33.5]
    crash_volumes = [10_000] * 40 + [500_000, 50_000, 40_000, 30_000, 30_000]

    conn = data_store._connect()
    conn.executemany(
        """INSERT INTO daily_prices
           (symbol, date, series, open, high, low, close, prev_close, volume, turnover)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        _rows("SPLITCO", split_closes, split_volumes, dates) + _rows("CRASHCO", crash_closes, crash_volumes, dates),
    )
    conn.commit()
    conn.close()
    return db_path


def test_reports_applied_and_flagged_separately(synthetic_db):
    result = list_data_anomalies(db_path=synthetic_db)
    assert result["total_suspect_ratios"] == 2
    assert result["applied_count"] == 1
    assert result["flagged_unadjusted_count"] == 1
    applied_symbols = {a["symbol"] for a in result["applied"]}
    flagged_symbols = {a["symbol"] for a in result["flagged"]}
    assert applied_symbols == {"SPLITCO"}
    assert flagged_symbols == {"CRASHCO"}


def test_symbol_filter_narrows_report(synthetic_db):
    result = list_data_anomalies(db_path=synthetic_db, symbol="splitco")
    assert result["symbol_filter"] == "SPLITCO"
    assert result["total_suspect_ratios"] == 1
    assert result["applied"][0]["symbol"] == "SPLITCO"


def test_flagged_reasons_are_summarized(synthetic_db):
    result = list_data_anomalies(db_path=synthetic_db)
    assert sum(result["flagged_reasons"].values()) == result["flagged_unadjusted_count"]
