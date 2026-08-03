"""Tests for run_portfolio() (src/llmfin/portfolio_backtest.py) -- sequencing
backtest.py's independent per-trade results through one shared, capital- and
slot-constrained book: risk-based sizing, a concurrency cap, a correlation
cap, and the resulting equity curve / drawdown.

Each test isolates one mechanism by constructing a minimal synthetic trades
DataFrame (the shape backtest.simulate() produces) plus a minimal price
panel (only needed for the correlation cap).
"""

from __future__ import annotations

import pandas as pd
import pytest

from llmfin.portfolio_backtest import PortfolioConfig, run_portfolio

EMPTY_PANEL = pd.DataFrame(columns=["symbol", "date", "close"])


def _trade(symbol, entry_date, exit_date, pnl_pct, conviction=0.8, stop_distance_pct=2.0):
    return {
        "symbol": symbol,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "direction": "BUY",
        "conviction": conviction,
        "pnl_pct": pnl_pct,
        "stop_distance_pct": stop_distance_pct,
    }


def test_position_sizing_is_risk_based_and_capped():
    trades = pd.DataFrame([_trade("AAA", "2020-01-01", "2020-01-05", pnl_pct=4.0, stop_distance_pct=2.0)])
    cfg = PortfolioConfig(starting_capital=100_000, risk_per_trade_pct=1.0, max_position_pct=20.0)
    result = run_portfolio(trades, EMPTY_PANEL, cfg)

    # risk_dollars=1000, stop 2% -> naive size 50,000, but capped at 20% of equity = 20,000.
    assert result["accepted"] == 1
    assert result["equity_curve"][-1]["equity"] == pytest.approx(100_000 + 20_000 * 0.04)
    assert result["final_equity"] == pytest.approx(100_800.0)
    assert result["total_return_pct"] == pytest.approx(0.8)


def test_concurrency_cap_rejects_excess_same_day_entries():
    trades = pd.DataFrame(
        [
            _trade("AAA", "2020-01-01", "2020-01-05", pnl_pct=1.0, conviction=0.9),
            _trade("BBB", "2020-01-01", "2020-01-05", pnl_pct=1.0, conviction=0.8),
            _trade("CCC", "2020-01-01", "2020-01-05", pnl_pct=1.0, conviction=0.7),
        ]
    )
    cfg = PortfolioConfig(starting_capital=100_000, max_concurrent_positions=2)
    result = run_portfolio(trades, EMPTY_PANEL, cfg)

    assert result["accepted"] == 2
    assert result["rejected_concurrency_cap"] == 1
    assert result["rejected_correlation_cap"] == 0


def test_correlation_cap_rejects_a_second_highly_correlated_position():
    dates = pd.bdate_range("2019-11-01", periods=20).strftime("%Y-%m-%d").tolist()
    closes = [100.0 + i for i in range(20)]  # identical, perfectly-correlated ramp for both symbols
    panel = pd.DataFrame(
        [{"symbol": "AAA", "date": d, "close": c} for d, c in zip(dates, closes)]
        + [{"symbol": "BBB", "date": d, "close": c} for d, c in zip(dates, closes)]
    )
    trades = pd.DataFrame(
        [
            _trade("AAA", dates[-1], "2020-01-05", pnl_pct=1.0, conviction=0.9),
            _trade("BBB", dates[-1], "2020-01-05", pnl_pct=1.0, conviction=0.8),
        ]
    )
    cfg = PortfolioConfig(max_correlated_cluster=1, corr_threshold=0.6, corr_lookback_days=10)
    result = run_portfolio(trades, panel, cfg)

    assert result["accepted"] == 1
    assert result["rejected_correlation_cap"] == 1


def test_uncorrelated_symbols_are_not_rejected():
    # Two independent random walks (numpy Generator(seed=42), verified offline to have
    # a ~0.19 return correlation -- comfortably under the 0.6 threshold below).
    dates = pd.bdate_range("2019-11-01", periods=20).strftime("%Y-%m-%d").tolist()
    aaa = [100.405, 99.465, 100.315, 101.356, 99.505, 98.303, 98.53, 98.314, 98.397, 97.644,
           98.624, 99.501, 99.668, 100.895, 101.462, 100.703, 101.172, 100.313, 101.291, 101.341]
    bbb = [99.815, 99.134, 100.357, 100.202, 99.774, 99.422, 99.954, 100.32, 100.732, 101.163,
           103.305, 102.898, 102.386, 101.572, 102.188, 103.317, 103.203, 102.363, 101.539, 102.189]
    panel = pd.DataFrame(
        [{"symbol": "AAA", "date": d, "close": c} for d, c in zip(dates, aaa)]
        + [{"symbol": "BBB", "date": d, "close": c} for d, c in zip(dates, bbb)]
    )
    trades = pd.DataFrame(
        [
            _trade("AAA", dates[-1], "2020-01-05", pnl_pct=1.0, conviction=0.9),
            _trade("BBB", dates[-1], "2020-01-05", pnl_pct=1.0, conviction=0.8),
        ]
    )
    cfg = PortfolioConfig(max_correlated_cluster=1, corr_threshold=0.6, corr_lookback_days=10)
    result = run_portfolio(trades, panel, cfg)

    assert result["accepted"] == 2
    assert result["rejected_correlation_cap"] == 0


def test_drawdown_reflects_a_losing_trade_before_a_winner():
    trades = pd.DataFrame(
        [
            _trade("AAA", "2020-01-01", "2020-01-03", pnl_pct=-10.0, stop_distance_pct=5.0),
            _trade("BBB", "2020-01-04", "2020-01-06", pnl_pct=5.0, stop_distance_pct=5.0),
        ]
    )
    cfg = PortfolioConfig(starting_capital=100_000, risk_per_trade_pct=2.0, max_position_pct=100.0)
    result = run_portfolio(trades, EMPTY_PANEL, cfg)

    assert result["max_drawdown_pct"] < 0
    # equity dips after the first (losing) trade, then partially recovers -- final
    # equity should still be below the running peak set before either trade closed.
    equities = [pt["equity"] for pt in result["equity_curve"]]
    assert min(equities) < equities[0]
