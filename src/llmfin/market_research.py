"""
market_research.py
──────────────────
Per-instrument research: OHLCV → indicators → regime-separated signals.

Data source is chosen automatically:
  • Zerodha Kite historical API when a session exists (any interval), else
  • the local bhavcopy SQLite DB (daily candles, free, no credentials).

The result carries BOTH the trend-following and mean-reversion signals with
their reasoning - deliberately not averaged (see signals.py).
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd
from kiteconnect import KiteConnect

from llmfin.data_store import load_history
from llmfin.indicators import compute_indicators
from llmfin.signals import Signal, TradePlan, run_models, trade_plan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate-limiting guard for Kite: 3 req/s hard cap, with back-off
# ---------------------------------------------------------------------------
_LAST_CALL: float = 0.0
_MIN_INTERVAL: float = 0.35


def _rate_limited_call(fn, *args, **kwargs):
    global _LAST_CALL
    elapsed = time.monotonic() - _LAST_CALL
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_CALL = time.monotonic()
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if "too many requests" in str(exc).lower() and attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited - retrying in %ss ...", wait)
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TechnicalSnapshot:
    rsi_14: Optional[float]
    macd_histogram: Optional[float]
    bb_position: Optional[float]     # 0 = at lower band, 1 = at upper band
    ema_20: Optional[float]
    ema_50: Optional[float]
    atr_14: Optional[float]
    close: float
    volume: float
    percent_change_1d: Optional[float]
    percent_change_5d: Optional[float]


@dataclass
class ResearchResult:
    symbol: str
    exchange: str
    as_of: str
    data_source: str                       # 'kite' or 'bhavcopy'
    snapshot: TechnicalSnapshot
    signals: list[Signal]                  # one per alpha model, NOT averaged
    trade_plans: dict[str, Optional[TradePlan]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# OHLCV fetchers
# ---------------------------------------------------------------------------

INTERVAL_MAP = {
    "1d": "day",
    "1h": "60minute",
    "30m": "30minute",
    "15m": "15minute",
    "5m": "5minute",
}


def fetch_ohlcv_kite(
    kite: KiteConnect,
    instrument_token: int,
    interval: str = "1d",
    lookback_days: int = 120,
) -> pd.DataFrame:
    kite_interval = INTERVAL_MAP.get(interval, "day")
    # Kite caps intraday-interval requests to ~100 days per call.
    if kite_interval != "day":
        lookback_days = min(lookback_days, 90)

    to_dt = datetime.now(tz=timezone.utc)
    from_dt = to_dt - timedelta(days=lookback_days)

    data = _rate_limited_call(
        kite.historical_data,
        instrument_token=instrument_token,
        from_date=from_dt.strftime("%Y-%m-%d"),
        to_date=to_dt.strftime("%Y-%m-%d"),
        interval=kite_interval,
        continuous=False,
    )
    df = pd.DataFrame(data)
    if df.empty:
        raise ValueError(f"No historical data returned for token={instrument_token}")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)[
        ["date", "open", "high", "low", "close", "volume"]
    ]


# ---------------------------------------------------------------------------
# High-level research entry point
# ---------------------------------------------------------------------------

def research_instrument(
    symbol: str,
    instrument_token: Optional[int] = None,
    exchange: str = "NSE",
    interval: str = "1d",
    lookback_days: int = 180,
    kite: Optional[KiteConnect] = None,
) -> ResearchResult:
    """Full research pipeline for one instrument.

    Uses Kite when a client + token are provided; otherwise falls back to the
    local bhavcopy DB (daily candles only).
    """
    df_raw = None
    data_source = "bhavcopy"

    if kite is not None and instrument_token is not None:
        try:
            df_raw = fetch_ohlcv_kite(kite, instrument_token, interval, lookback_days)
            data_source = "kite"
        except Exception as exc:
            logger.warning("Kite fetch failed for %s (%s) - falling back to local DB", symbol, exc)

    if df_raw is None:
        df_raw = load_history(symbol, lookback_days=lookback_days)
        if df_raw.empty:
            raise ValueError(
                f"No data for {symbol}: Kite unavailable and local DB has no rows. "
                "Run `llmfin-ingest` to populate the free bhavcopy DB."
            )
        if interval != "1d":
            logger.info("Local DB is daily-only; ignoring interval=%s for %s", interval, symbol)

    df = compute_indicators(df_raw)

    last = df.iloc[-1]
    prev1 = df.iloc[-2] if len(df) > 1 else last
    prev5 = df.iloc[-6] if len(df) > 5 else df.iloc[0]

    close = float(last["close"])
    pct_1d = round((close - float(prev1["close"])) / float(prev1["close"]) * 100, 2) if float(prev1["close"]) else None
    pct_5d = round((close - float(prev5["close"])) / float(prev5["close"]) * 100, 2) if float(prev5["close"]) else None

    def _sf(col: str) -> Optional[float]:
        v = last.get(col)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(f) else round(f, 4)

    bb_lower, bb_upper = _sf("bb_lower"), _sf("bb_upper")
    bb_position = None
    if bb_lower is not None and bb_upper is not None and bb_upper > bb_lower:
        bb_position = round((close - bb_lower) / (bb_upper - bb_lower), 3)

    snapshot = TechnicalSnapshot(
        rsi_14=_sf("rsi_14"),
        macd_histogram=_sf("macd_hist"),
        bb_position=bb_position,
        ema_20=_sf("ema_20"),
        ema_50=_sf("ema_50"),
        atr_14=_sf("atr_14"),
        close=close,
        volume=float(last["volume"]),
        percent_change_1d=pct_1d,
        percent_change_5d=pct_5d,
    )

    signals = run_models(df)
    plans = {
        s.model: trade_plan(df, s.direction) for s in signals if s.direction != "HOLD"
    }

    as_of = last["date"]
    as_of_str = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)

    return ResearchResult(
        symbol=symbol.upper(),
        exchange=exchange,
        as_of=as_of_str,
        data_source=data_source,
        signals=signals,
        snapshot=snapshot,
        trade_plans=plans,
    )
