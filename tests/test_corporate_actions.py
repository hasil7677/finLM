"""Tests for the corporate-action back-adjuster (src/llmfin/corporate_actions.py).

Covers the three guards documented in that module's docstring, using synthetic
panels shaped like the real bhavcopy schema (symbol, date, OHLC, prev_close,
volume). Regression cases for FINANTECH (guard 2) and JETAIRWAYS (guard 3) use
the same real-world shapes that originally forced each guard's addition.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from llmfin.corporate_actions import adjust_corporate_actions


def _panel(symbol: str, closes: list[float], volumes: list[float], start: str = "2020-01-01") -> pd.DataFrame:
    """Build a minimal daily panel: `closes[0]` seeds prev_close, one row per
    subsequent close. open/high/low mirror close (irrelevant to the adjuster's
    ratio/volume logic but required columns)."""
    dates = pd.bdate_range(start, periods=len(closes))
    prev = [np.nan] + closes[:-1]
    df = pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates.strftime("%Y-%m-%d"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "prev_close": prev,
            "volume": volumes,
        }
    )
    df.loc[0, "prev_close"] = closes[0]  # first row: no real gap
    return df


def test_no_suspect_ratios_passes_through_unchanged():
    df = _panel("STABLECO", [100, 101, 99, 102, 103], [1000] * 5)
    out, adjustments = adjust_corporate_actions(df)
    assert adjustments == []
    pd.testing.assert_frame_equal(out.reset_index(drop=True), df.reset_index(drop=True))


def test_clean_2for1_split_is_adjusted():
    # 40 steady days at 100, then an overnight 2:1 split (close halves), normal volume after.
    closes = [100.0] * 40 + [50.0] + [50.5, 49.5, 50.2]
    volumes = [10_000] * 40 + [12_000] + [10_000, 10_000, 10_000]
    df = _panel("SPLITCO", closes, volumes)
    out, adjustments = adjust_corporate_actions(df)

    applied = [a for a in adjustments if a["applied"]]
    assert len(applied) == 1
    assert applied[0]["symbol"] == "SPLITCO"
    assert applied[0]["applied_ratio"] == 0.5

    # Pre-split closes should now read ~50 (back-adjusted), not 100.
    pre_split = out[(out["symbol"] == "SPLITCO")].iloc[0]
    assert pre_split["close"] == pytest.approx(50.0)


def test_crash_with_spike_volume_near_split_ratio_is_not_adjusted():
    """FINANTECH-shaped case: a real crash whose ratio (~0.333) coincidentally
    matches a common split ratio (1/3), but at panic-scale volume - guard 2
    (volume-sanity) must block the adjustment."""
    closes = [100.0] * 40 + [33.3] + [33.0, 32.5, 33.8]
    volumes = [10_000] * 40 + [500_000] + [50_000, 40_000, 30_000]  # 50x trailing avg
    df = _panel("FINANTECH", closes, volumes)
    out, adjustments = adjust_corporate_actions(df)

    assert len(adjustments) == 1
    assert adjustments[0]["applied"] is False
    assert "volume" in adjustments[0]["reason"]
    # Unadjusted: the historical close should stay at its raw printed value
    # (volume is float64 either way - the adjuster's factor division always
    # produces floats, even when every factor is 1.0).
    pd.testing.assert_frame_equal(
        out.reset_index(drop=True), df.reset_index(drop=True), check_dtype=False
    )


def test_crisis_cluster_near_split_ratio_is_not_adjusted():
    """JETAIRWAYS-shaped case: a death-spiral day lands near a clean ratio
    (1.899 ~ 2:1) at volume that individually looks acceptable (baseline is
    already crisis-elevated), but another anomaly sits within the cluster
    window - guard 3 (isolation) must block the adjustment."""
    closes = [100.0] * 40 + [40.0, 76.0] + [70.0, 65.0, 60.0]
    #                         ^ crash day    ^ "2:1-ish" bounce, ratio 1.9
    volumes = [10_000] * 40 + [60_000, 65_000] + [60_000, 55_000, 50_000]
    df = _panel("JETAIRWAYS", closes, volumes)
    out, adjustments = adjust_corporate_actions(df)

    applied = [a for a in adjustments if a["applied"]]
    assert applied == []
    reasons = [a["reason"] for a in adjustments if not a["applied"]]
    assert any("another anomaly" in r for r in reasons)


def test_new_listing_with_no_volume_baseline_is_flagged_not_adjusted():
    """A suspect ratio on one of the first few rows (no 20-day trailing volume
    yet) should be flagged for review, not silently adjusted or crashed on."""
    closes = [100.0, 45.0, 46.0]
    volumes = [10_000, 12_000, 11_000]
    df = _panel("NEWCO", closes, volumes)
    out, adjustments = adjust_corporate_actions(df)

    assert len(adjustments) == 1
    assert adjustments[0]["applied"] is False
    assert "baseline" in adjustments[0]["reason"]


def test_known_real_splits_still_adjust_correctly():
    """Regression guard: known-real splits (used to sanity-check the two bug
    fixes during the 2010-2020 backtest) must still get adjusted."""
    for symbol, ratio in [("AJANTPHARM", 0.5), ("BAJFINANCE", 0.2), ("KPIT", 0.1)]:
        closes = [200.0] * 40 + [200.0 * ratio] + [200.0 * ratio * 1.01, 200.0 * ratio * 0.99]
        volumes = [10_000] * 40 + [15_000] + [10_000, 10_000]
        df = _panel(symbol, closes, volumes)
        _out, adjustments = adjust_corporate_actions(df)
        applied = [a for a in adjustments if a["applied"]]
        assert len(applied) == 1, f"{symbol} should have been adjusted"
        assert applied[0]["applied_ratio"] == pytest.approx(ratio)
