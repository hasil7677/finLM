"""
indicators.py
─────────────
Hand-rolled technical indicators in pure pandas/numpy.

Replaces pandas_ta (unmaintained, breaks on numpy>=2.0). All functions take a
DataFrame with columns [open, high, low, close, volume] and return Series
aligned to the input index.
"""

from __future__ import annotations

import pandas as pd


def ema(close: pd.Series, length: int) -> pd.Series:
    return close.ewm(span=length, adjust=False).mean()


def sma(close: pd.Series, length: int) -> pd.Series:
    return close.rolling(length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    # When avg_loss is 0 the instrument only went up: RSI is 100 by convention.
    out = out.where(avg_loss != 0, 100.0)
    out[avg_gain.isna() | avg_loss.isna()] = float("nan")
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Returns DataFrame with columns [macd, signal, hist]."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "hist": macd_line - signal_line}
    )


def bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Returns DataFrame with columns [lower, mid, upper]."""
    mid = sma(close, length)
    dev = close.rolling(length).std(ddof=0) * std
    return pd.DataFrame({"lower": mid - dev, "mid": mid, "upper": mid + dev})


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Wilder's Average True Range. Expects columns [high, low, close]."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add the standard indicator columns used by the signal models."""
    df = df.copy()
    close = df["close"]

    df["rsi_14"] = rsi(close, 14)

    macd_df = macd(close)
    df["macd"] = macd_df["macd"]
    df["macd_signal"] = macd_df["signal"]
    df["macd_hist"] = macd_df["hist"]

    bb = bbands(close)
    df["bb_lower"] = bb["lower"]
    df["bb_mid"] = bb["mid"]
    df["bb_upper"] = bb["upper"]

    df["ema_20"] = ema(close, 20)
    df["ema_50"] = ema(close, 50)

    df["atr_14"] = atr(df, 14)

    return df
