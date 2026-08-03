"""Tests for the quiet-accumulation long-side screen
(src/llmfin/accumulation_scanner.py). Builds a throwaway sqlite DB shaped
like daily_prices with five synthetic symbols, each engineered to isolate
exactly one filter in the chain, and checks scan_quiet_accumulation() keeps
only the one that should pass all of them.
"""

from __future__ import annotations

import pandas as pd
import pytest

from llmfin import data_store
from llmfin.accumulation_scanner import scan_quiet_accumulation

N_DAYS = 90
BASELINE_START, BASELINE_END = 60, 79  # rows used as the pre-recent volume baseline
RECENT_START, RECENT_END = 80, 89  # last 10 rows: the "now" window


def _rows(symbol: str, closes: list[float], volumes: list[float], dates: pd.DatetimeIndex) -> list[tuple]:
    rows = []
    prev = closes[0]
    for d, c, v in zip(dates, closes, volumes):
        high, low = c + 0.75, c - 0.75
        rows.append((symbol, d, "EQ", c, high, low, c, prev, v, c * v))
        prev = c
    return rows


@pytest.fixture
def synthetic_db(tmp_path, monkeypatch):
    db_path = tmp_path / "market_test.db"
    monkeypatch.setattr(data_store, "DB_PATH", db_path)
    dates = pd.bdate_range("2020-01-01", periods=N_DAYS).strftime("%Y-%m-%d").tolist()

    all_rows: list[tuple] = []

    # QUIETCO: steady mild grind up, tight range, volume rises cleanly across
    # the whole recent window (no single day dominates it) -- should PASS.
    quiet_closes = [150.0 + i * 0.15 for i in range(N_DAYS)]
    quiet_volumes = [100_000.0] * BASELINE_END + [140_000.0] * (N_DAYS - BASELINE_END)
    all_rows += _rows("QUIETCO", quiet_closes, quiet_volumes, dates)

    # MOVERCO: same shape, but one big single-day price spike inside the
    # 20-day lookback -- must be excluded by the no-spike guard.
    mover_closes = list(quiet_closes)
    mover_closes[85] = mover_closes[84] * 1.10  # +10% one-day jump, well past max_single_day_move_pct
    all_rows += _rows("MOVERCO", mover_closes, quiet_volumes, dates)

    # ONEBIGDAY: same price shape as QUIETCO, but the "rising" recent volume
    # is really one huge print, not broad participation -- must be excluded
    # by max_single_day_volume_share even though the 10-day average clears
    # min_volume_ratio.
    onebig_volumes = [100_000.0] * BASELINE_END + [50_000.0] * 9 + [1_000_000.0]
    all_rows += _rows("ONEBIGDAY", quiet_closes, onebig_volumes, dates)

    # DOWNCO: same rising-volume shape, but price is grinding DOWN -- must be
    # excluded by the trend-structure / cumulative-return guard.
    down_closes = [200.0 - i * 0.3 for i in range(N_DAYS)]
    all_rows += _rows("DOWNCO", down_closes, quiet_volumes, dates)

    # ILLIQUIDCO: identical quiet-accumulation shape, but price is below the
    # liquidity floor -- must be excluded at the first filter stage.
    illiquid_closes = [20.0 + i * 0.02 for i in range(N_DAYS)]
    all_rows += _rows("ILLIQUIDCO", illiquid_closes, quiet_volumes, dates)

    conn = data_store._connect()
    conn.executemany(
        """INSERT INTO daily_prices
           (symbol, date, series, open, high, low, close, prev_close, volume, turnover)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        all_rows,
    )
    conn.commit()
    conn.close()
    return db_path


def _run(**overrides):
    params = dict(min_price=50.0, min_avg_volume=50_000, min_turnover_cr=1.0, min_volume_ratio=1.3)
    params.update(overrides)
    return scan_quiet_accumulation(**params)


def test_quiet_accumulation_candidate_is_flagged(synthetic_db):
    hits = _run()
    symbols = {h.symbol for h in hits}
    assert "QUIETCO" in symbols


def test_single_day_price_spike_is_excluded(synthetic_db):
    hits = _run()
    assert "MOVERCO" not in {h.symbol for h in hits}


def test_single_day_volume_print_is_excluded(synthetic_db):
    hits = _run()
    assert "ONEBIGDAY" not in {h.symbol for h in hits}


def test_downtrend_is_excluded(synthetic_db):
    hits = _run()
    assert "DOWNCO" not in {h.symbol for h in hits}


def test_illiquid_name_is_excluded_by_price_floor(synthetic_db):
    hits = _run()
    assert "ILLIQUIDCO" not in {h.symbol for h in hits}


def test_quiet_candidate_hit_fields_are_sane(synthetic_db):
    hits = _run()
    hit = next(h for h in hits if h.symbol == "QUIETCO")
    assert hit.volume_ratio >= 1.3
    assert hit.max_single_day_volume_share <= 0.35
    assert hit.avg_range_pct <= 3.5
    assert hit.max_abs_change_pct <= 6.0
    assert 0.0 <= hit.cum_return_pct <= 12.0
