"""
signals.py
──────────
Alpha models — each one analyzes an indicator DataFrame and returns a Signal
with a conviction score in [-1, +1] plus written reasoning.

Design borrowed from ai-hedge-fund v2 / Vibe-Trading: signal sources share one
interface and are NEVER averaged into a single vote across regimes. Trend
following and mean reversion are opposing philosophies — a strong uptrend is a
BUY to one and an overbought SELL to the other. Present both; let the ranking
layer (or the human) weigh them by regime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import pandas as pd


@dataclass
class Signal:
    model: str
    direction: Literal["BUY", "SELL", "HOLD"]
    conviction: float  # -1.0 (max SELL) .. +1.0 (max BUY)
    reasoning: list[str] = field(default_factory=list)


@dataclass
class TradePlan:
    """ATR-derived entry/stop/target for a directional signal."""
    entry: float
    stop_loss: float
    target: float
    risk_reward: float


def _last_float(row: pd.Series, col: str) -> Optional[float]:
    val = row.get(col)
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


class TrendFollowingModel:
    """Buys strength: price above rising EMAs with positive momentum."""

    name = "trend_following"

    def predict(self, df: pd.DataFrame) -> Signal:
        row = df.iloc[-1]
        reasons: list[str] = []
        score = 0.0

        close = float(row["close"])
        ema20 = _last_float(row, "ema_20")
        ema50 = _last_float(row, "ema_50")
        macd_hist = _last_float(row, "macd_hist")
        rsi = _last_float(row, "rsi_14")

        if ema50 is not None:
            if close > ema50:
                score += 0.35
                reasons.append(f"Close {close:.2f} above EMA50 {ema50:.2f} — uptrend intact")
            else:
                score -= 0.35
                reasons.append(f"Close {close:.2f} below EMA50 {ema50:.2f} — downtrend")

        if ema20 is not None and ema50 is not None:
            if ema20 > ema50:
                score += 0.15
                reasons.append("EMA20 above EMA50 — short-term momentum aligned with trend")
            else:
                score -= 0.15
                reasons.append("EMA20 below EMA50 — short-term momentum against trend")

        if macd_hist is not None:
            if macd_hist > 0:
                score += 0.30
                reasons.append(f"MACD histogram {macd_hist:.4f} positive — bullish momentum")
            else:
                score -= 0.30
                reasons.append(f"MACD histogram {macd_hist:.4f} negative — bearish momentum")

        # For a trend follower, healthy RSI is 45-75; >80 is exhaustion risk,
        # <40 means there is no uptrend to follow.
        if rsi is not None:
            if 45 <= rsi <= 75:
                score += 0.20 if score > 0 else -0.20
                reasons.append(f"RSI {rsi:.1f} in healthy trend range (45-75)")
            elif rsi > 80:
                score -= 0.10
                reasons.append(f"RSI {rsi:.1f} — trend extended, exhaustion risk")
            elif rsi < 40:
                score -= 0.10
                reasons.append(f"RSI {rsi:.1f} — momentum too weak for trend entry")

        direction: Literal["BUY", "SELL", "HOLD"]
        if score >= 0.5:
            direction = "BUY"
        elif score <= -0.5:
            direction = "SELL"
        else:
            direction = "HOLD"

        return Signal(self.name, direction, round(max(-1.0, min(1.0, score)), 2), reasons)


class MeanReversionModel:
    """Buys washed-out weakness in otherwise healthy names; sells euphoria.

    Only takes long mean-reversion entries when the longer trend is not
    broken (close within ~10% of EMA50) — otherwise oversold is a falling
    knife, not a dip.
    """

    name = "mean_reversion"

    def predict(self, df: pd.DataFrame) -> Signal:
        row = df.iloc[-1]
        reasons: list[str] = []
        score = 0.0

        close = float(row["close"])
        rsi = _last_float(row, "rsi_14")
        bb_lower = _last_float(row, "bb_lower")
        bb_upper = _last_float(row, "bb_upper")
        ema50 = _last_float(row, "ema_50")

        bb_pos = None
        if bb_lower is not None and bb_upper is not None and bb_upper > bb_lower:
            bb_pos = (close - bb_lower) / (bb_upper - bb_lower)

        if rsi is not None:
            if rsi < 30:
                score += 0.40
                reasons.append(f"RSI {rsi:.1f} — deeply oversold (<30)")
            elif rsi < 35:
                score += 0.25
                reasons.append(f"RSI {rsi:.1f} — oversold (<35)")
            elif rsi > 70:
                score -= 0.40
                reasons.append(f"RSI {rsi:.1f} — overbought (>70)")
            elif rsi > 65:
                score -= 0.25
                reasons.append(f"RSI {rsi:.1f} — stretched (>65)")

        if bb_pos is not None:
            if bb_pos < 0.15:
                score += 0.35
                reasons.append(f"Price at {bb_pos:.0%} of Bollinger range — at/below lower band")
            elif bb_pos < 0.25:
                score += 0.20
                reasons.append(f"Price at {bb_pos:.0%} of Bollinger range — near lower band")
            elif bb_pos > 0.85:
                score -= 0.35
                reasons.append(f"Price at {bb_pos:.0%} of Bollinger range — at/above upper band")
            elif bb_pos > 0.75:
                score -= 0.20
                reasons.append(f"Price at {bb_pos:.0%} of Bollinger range — near upper band")

        # Falling-knife guard: don't buy oversold names in broken trends.
        if score > 0 and ema50 is not None and close < 0.90 * ema50:
            score = 0.0
            reasons.append(
                f"VETO: close {close:.2f} is >10% below EMA50 {ema50:.2f} — "
                "broken trend, oversold is a falling knife not a dip"
            )

        direction: Literal["BUY", "SELL", "HOLD"]
        if score >= 0.45:
            direction = "BUY"
        elif score <= -0.45:
            direction = "SELL"
        else:
            direction = "HOLD"

        return Signal(self.name, direction, round(max(-1.0, min(1.0, score)), 2), reasons)


ALL_MODELS = [TrendFollowingModel(), MeanReversionModel()]


def run_models(df: pd.DataFrame) -> list[Signal]:
    return [m.predict(df) for m in ALL_MODELS]


def trade_plan(df: pd.DataFrame, direction: str, atr_stop: float = 1.5, atr_target: float = 2.5) -> Optional[TradePlan]:
    """ATR-based entry/stop/target for BUY or SELL signals."""
    row = df.iloc[-1]
    atr = _last_float(row, "atr_14")
    if atr is None or direction not in ("BUY", "SELL"):
        return None
    close = float(row["close"])
    if direction == "BUY":
        stop, target = close - atr_stop * atr, close + atr_target * atr
    else:
        stop, target = close + atr_stop * atr, close - atr_target * atr
    return TradePlan(
        entry=round(close, 2),
        stop_loss=round(stop, 2),
        target=round(target, 2),
        risk_reward=round(atr_target / atr_stop, 2),
    )
