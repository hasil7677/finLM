"""
market_research.py
──────────────────
Core market-research logic:
  • Fetch OHLCV data from Kite Historical API
  • Compute technical indicators (RSI, MACD, Bollinger Bands, EMA)
  • Detect signals (momentum, trend, mean-reversion)
  • Return a structured ResearchResult with a trade suggestion

This module is intentionally SDK-agnostic inside the analysis layer — it receives
raw DataFrames and returns typed results that the MCP server can serialise easily.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

import pandas as pd
import pandas_ta as ta  # type: ignore[import]
from kiteconnect import KiteConnect

from llmfin.session_manager import get_kite_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate-limiting guard: 3 req/s hard cap, with back-off
# ---------------------------------------------------------------------------
_LAST_CALL: float = 0.0
_MIN_INTERVAL: float = 0.35  # seconds between Kite API calls


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
                logger.warning("Rate limited — retrying in %ss …", wait)
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TechnicalSnapshot:
    rsi_14: Optional[float]
    macd_line: Optional[float]
    macd_signal: Optional[float]
    macd_histogram: Optional[float]
    bb_upper: Optional[float]
    bb_lower: Optional[float]
    bb_mid: Optional[float]
    ema_20: Optional[float]
    ema_50: Optional[float]
    atr_14: Optional[float]
    close: float
    volume: float
    percent_change_1d: Optional[float]
    percent_change_5d: Optional[float]


@dataclass
class TradeSignal:
    direction: Literal["BUY", "SELL", "HOLD"]
    confidence: float          # 0.0 – 1.0
    reasoning: list[str] = field(default_factory=list)
    suggested_entry: Optional[float] = None
    suggested_stop_loss: Optional[float] = None
    suggested_target: Optional[float] = None


@dataclass
class ResearchResult:
    symbol: str
    exchange: str
    as_of: str                 # ISO-8601
    snapshot: TechnicalSnapshot
    signal: TradeSignal
    raw_indicators: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# OHLCV fetcher
# ---------------------------------------------------------------------------

INTERVAL_MAP = {
    "1d":  "day",
    "1h":  "60minute",
    "30m": "30minute",
    "15m": "15minute",
    "5m":  "5minute",
}


def fetch_ohlcv(
    instrument_token: int,
    interval: str = "1d",
    lookback_days: int = 120,
    kite: Optional[KiteConnect] = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles from Kite Historical API.

    Returns a DataFrame with columns: [date, open, high, low, close, volume].
    """
    kite = kite or get_kite_client()
    kite_interval = INTERVAL_MAP.get(interval, "day")

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
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


# ---------------------------------------------------------------------------
# Technical indicator computation
# ---------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical indicator columns to the OHLCV DataFrame."""
    df = df.copy()

    # RSI
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    # MACD
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        df["macd"] = macd_df.iloc[:, 0]
        df["macd_signal"] = macd_df.iloc[:, 2]
        df["macd_hist"] = macd_df.iloc[:, 1]

    # Bollinger Bands
    bb_df = ta.bbands(df["close"], length=20, std=2)
    if bb_df is not None and not bb_df.empty:
        df["bb_lower"] = bb_df.iloc[:, 0]
        df["bb_mid"] = bb_df.iloc[:, 1]
        df["bb_upper"] = bb_df.iloc[:, 2]

    # EMA
    df["ema_20"] = ta.ema(df["close"], length=20)
    df["ema_50"] = ta.ema(df["close"], length=50)

    # ATR
    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    return df


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    try:
        f = float(val)
        return None if pd.isna(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def generate_signal(df: pd.DataFrame) -> TradeSignal:
    """
    Multi-factor signal generator.

    Rules (each contributes to a score):
      +1 / -1  RSI momentum  (RSI < 35 → BUY bias; RSI > 65 → SELL bias)
      +1 / -1  MACD crossover (histogram positive → BUY; negative → SELL)
      +1 / -1  BB mean-reversion (close near lower → BUY; near upper → SELL)
      +1 / -1  Trend alignment (close > EMA50 → BUY; below → SELL)

    confidence = |score| / 4
    """
    row = df.iloc[-1]
    reasons: list[str] = []
    score = 0

    rsi = _safe_float(row.get("rsi_14"))
    if rsi is not None:
        if rsi < 35:
            score += 1
            reasons.append(f"RSI={rsi:.1f} — oversold (< 35)")
        elif rsi > 65:
            score -= 1
            reasons.append(f"RSI={rsi:.1f} — overbought (> 65)")
        else:
            reasons.append(f"RSI={rsi:.1f} — neutral zone")

    macd_hist = _safe_float(row.get("macd_hist"))
    if macd_hist is not None:
        if macd_hist > 0:
            score += 1
            reasons.append(f"MACD histogram={macd_hist:.4f} — bullish momentum")
        else:
            score -= 1
            reasons.append(f"MACD histogram={macd_hist:.4f} — bearish momentum")

    close = float(row["close"])
    bb_lower = _safe_float(row.get("bb_lower"))
    bb_upper = _safe_float(row.get("bb_upper"))
    bb_mid = _safe_float(row.get("bb_mid"))
    if bb_lower and bb_upper:
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            position = (close - bb_lower) / bb_range
            if position < 0.25:
                score += 1
                reasons.append(f"Price near BB lower band ({position:.0%} of range) — oversold")
            elif position > 0.75:
                score -= 1
                reasons.append(f"Price near BB upper band ({position:.0%} of range) — overbought")

    ema_50 = _safe_float(row.get("ema_50"))
    if ema_50:
        if close > ema_50:
            score += 1
            reasons.append(f"Price ({close}) above EMA50 ({ema_50:.2f}) — uptrend")
        else:
            score -= 1
            reasons.append(f"Price ({close}) below EMA50 ({ema_50:.2f}) — downtrend")

    direction: Literal["BUY", "SELL", "HOLD"]
    if score >= 2:
        direction = "BUY"
    elif score <= -2:
        direction = "SELL"
    else:
        direction = "HOLD"

    confidence = round(abs(score) / 4, 2)

    # Risk levels using ATR
    atr = _safe_float(row.get("atr_14"))
    entry = suggested_sl = suggested_tgt = None
    if atr:
        entry = round(close, 2)
        if direction == "BUY":
            suggested_sl = round(close - 1.5 * atr, 2)
            suggested_tgt = round(close + 2.5 * atr, 2)
        elif direction == "SELL":
            suggested_sl = round(close + 1.5 * atr, 2)
            suggested_tgt = round(close - 2.5 * atr, 2)

    return TradeSignal(
        direction=direction,
        confidence=confidence,
        reasoning=reasons,
        suggested_entry=entry,
        suggested_stop_loss=suggested_sl,
        suggested_target=suggested_tgt,
    )


# ---------------------------------------------------------------------------
# High-level research entry point
# ---------------------------------------------------------------------------

def research_instrument(
    symbol: str,
    instrument_token: int,
    exchange: str = "NSE",
    interval: str = "1d",
    lookback_days: int = 120,
    kite: Optional[KiteConnect] = None,
) -> ResearchResult:
    """Full research pipeline for one instrument."""
    kite = kite or get_kite_client()

    df_raw = fetch_ohlcv(instrument_token, interval=interval, lookback_days=lookback_days, kite=kite)
    df = compute_indicators(df_raw)

    last = df.iloc[-1]
    prev1 = df.iloc[-2] if len(df) > 1 else last
    prev5 = df.iloc[-6] if len(df) > 5 else df.iloc[0]

    close = float(last["close"])
    pct_1d = round((close - float(prev1["close"])) / float(prev1["close"]) * 100, 2) if float(prev1["close"]) else None
    pct_5d = round((close - float(prev5["close"])) / float(prev5["close"]) * 100, 2) if float(prev5["close"]) else None

    snapshot = TechnicalSnapshot(
        rsi_14=_safe_float(last.get("rsi_14")),
        macd_line=_safe_float(last.get("macd")),
        macd_signal=_safe_float(last.get("macd_signal")),
        macd_histogram=_safe_float(last.get("macd_hist")),
        bb_upper=_safe_float(last.get("bb_upper")),
        bb_lower=_safe_float(last.get("bb_lower")),
        bb_mid=_safe_float(last.get("bb_mid")),
        ema_20=_safe_float(last.get("ema_20")),
        ema_50=_safe_float(last.get("ema_50")),
        atr_14=_safe_float(last.get("atr_14")),
        close=close,
        volume=float(last["volume"]),
        percent_change_1d=pct_1d,
        percent_change_5d=pct_5d,
    )

    signal = generate_signal(df)

    return ResearchResult(
        symbol=symbol,
        exchange=exchange,
        as_of=last["date"].isoformat(),
        snapshot=snapshot,
        signal=signal,
    )
