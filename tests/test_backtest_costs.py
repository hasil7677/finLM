"""Tests for explicit transaction-cost modeling in backtest.simulate()/stats()
(ExitConfig.cost_pct) -- replaces the "mental haircut" previously applied by
hand when reading backtest output (see README.md §7)."""

from __future__ import annotations

import pandas as pd
import pytest

from llmfin.backtest import ExitConfig, simulate, stats


def _target_hit_buy_event(scan_close: float = 100.0, atr: float = 2.0) -> dict:
    """A BUY that opens at scan_close and hits its target intraday on the
    very first forward day -- entry 100, target 105 under the default
    stop/target multiples, so gross pnl is a clean, hand-checkable 5%."""
    return {
        "scan_date": "2020-01-01",
        "symbol": "TESTCO",
        "scan_close": scan_close,
        "change_pct": 2.0,
        "volume_ratio": 2.0,
        "atr": atr,
        "fwd_open": [scan_close],
        "fwd_high": [105.0],
        "fwd_low": [99.0],
        "fwd_close": [104.0],
        "fwd_dates": ["2020-01-02"],
        "model": "trend_following",
        "direction": "BUY",
        "conviction": 0.8,
    }


@pytest.fixture
def flat_bench() -> pd.Series:
    return pd.Series({"2020-01-02": 1.0})


def test_cost_pct_is_subtracted_from_gross_pnl(flat_bench):
    cfg = ExitConfig(entry_style="open", cost_pct=0.4)
    trades = simulate([_target_hit_buy_event()], flat_bench, cfg)
    row = trades.iloc[0]
    assert row["gross_pnl_pct"] == pytest.approx(5.0)
    assert row["pnl_pct"] == pytest.approx(5.0 - 0.4)
    assert row["alpha_pct"] == pytest.approx(row["pnl_pct"])  # flat benchmark here


def test_zero_cost_reproduces_gross_pnl(flat_bench):
    cfg = ExitConfig(entry_style="open", cost_pct=0.0)
    trades = simulate([_target_hit_buy_event()], flat_bench, cfg)
    row = trades.iloc[0]
    assert row["pnl_pct"] == pytest.approx(row["gross_pnl_pct"])


def test_cost_can_flip_a_marginal_gross_winner_into_a_net_loser(flat_bench):
    """The whole point of modeling cost explicitly: a bucket whose gross edge
    is thinner than the round-trip cost must show up as unprofitable, not
    require a mental haircut applied after reading the number."""
    ev = _target_hit_buy_event()
    ev["fwd_high"] = [100.2]  # never reaches the 105 target
    ev["fwd_low"] = [99.9]
    ev["fwd_close"] = [100.2]
    cfg = ExitConfig(horizon=1, entry_style="open", cost_pct=0.4)
    trades = simulate([ev], flat_bench, cfg)
    row = trades.iloc[0]
    assert row["gross_pnl_pct"] > 0
    assert row["pnl_pct"] < 0


def test_stats_reports_both_gross_and_net(flat_bench):
    cfg = ExitConfig(entry_style="open", cost_pct=0.4)
    trades = simulate([_target_hit_buy_event(), _target_hit_buy_event()], flat_bench, cfg)
    result = stats(trades)
    assert result["avg_gross_pnl_pct"] == pytest.approx(5.0)
    assert result["avg_pnl_pct"] == pytest.approx(4.6)
    assert result["win_rate_pct"] == 100.0
