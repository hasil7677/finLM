"""Tests for src/llmfin/regime_analysis.py's building blocks: the per-year
SQL slicing (memory-bounded alternative to loading the full multi-year DB
at once) and the realized-vol/breadth computation. The full
yearly_fade_alpha_vs_regime() pipeline is exercised end-to-end against real
history manually (see CLAUDE.md) rather than in the test suite, since a
meaningful run needs years of realistic multi-symbol data.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from llmfin.regime_analysis import _year_regime_stats, _year_slice_db


def _make_source_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE daily_prices (
            symbol TEXT, date TEXT, series TEXT, open REAL, high REAL, low REAL,
            close REAL, prev_close REAL, volume INTEGER, turnover REAL
        )"""
    )
    conn.executemany(
        "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_year_slice_keeps_target_year_and_buffer_drops_rest(tmp_path):
    source = tmp_path / "source.db"
    rows = [
        ("AAA", "2018-06-01", "EQ", 100, 100, 100, 100, 100, 1000, 100000),  # too far before buffer
        ("AAA", "2018-11-01", "EQ", 100, 100, 100, 100, 100, 1000, 100000),  # inside buffer
        ("AAA", "2019-06-15", "EQ", 100, 100, 100, 100, 100, 1000, 100000),  # inside target year
        ("AAA", "2019-12-31", "EQ", 100, 100, 100, 100, 100, 1000, 100000),  # last day of target year
        ("AAA", "2020-01-02", "EQ", 100, 100, 100, 100, 100, 1000, 100000),  # after target year
    ]
    _make_source_db(source, rows)

    dest = tmp_path / "slice.db"
    _year_slice_db(source, 2019, dest)

    kept = pd.read_sql_query("SELECT date FROM daily_prices ORDER BY date", sqlite3.connect(dest))["date"].tolist()
    assert kept == ["2018-11-01", "2019-06-15", "2019-12-31"]


def test_year_regime_stats_on_known_returns(tmp_path):
    db = tmp_path / "regime.db"
    # A single trading day, two liquid names (both close >= the 100 floor even
    # though one is falling): AAA +2%, BBB -4.5454...%. One date -> realized
    # vol is trivially 0 (std of a single point), so this mainly hand-checks
    # the liquidity filter, the equal-weight averaging, and breadth counting.
    rows = [
        ("AAA", "2020-01-02", "EQ", 102, 102, 102, 102, 100, 1000, 2_000_000_00),
        ("BBB", "2020-01-02", "EQ", 105, 105, 105, 105, 110, 1000, 2_000_000_00),
    ]
    _make_source_db(db, rows)

    stats = _year_regime_stats(db, 2020)
    assert stats["liquid_universe_rows"] == 2
    assert stats["realized_vol_annualized_pct"] == pytest.approx(0.0)
    # Only AAA (1 of 2) advanced that day.
    assert stats["breadth_pct"] == pytest.approx(50.0)


def test_year_regime_stats_empty_when_no_liquid_rows(tmp_path):
    db = tmp_path / "regime_empty.db"
    rows = [("PENNY", "2020-01-02", "EQ", 5, 5, 5, 5, 5, 1000, 5000)]  # below both liquidity floors
    _make_source_db(db, rows)

    stats = _year_regime_stats(db, 2020)
    assert stats["liquid_universe_rows"] == 0
    assert stats["realized_vol_annualized_pct"] is None
