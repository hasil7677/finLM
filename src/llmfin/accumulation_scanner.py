"""
accumulation_scanner.py
────────────────────────
Long-side discovery: the deliberate opposite of scan_market.

scan_market finds explosions — big single-day gap/volume moves. §6's
backtest evidence (README.md) says explosions have no long edge: chasing
them loses in every configuration tested across 11 years of NSE history: the
only edge in that candidate stream is fading them (a sell). §7 names the fix
directly: a long strategy needs a DIFFERENT screen — rising volume, tight
range, no spike — the quiet phase that in theory precedes a move, rather
than the day of the move itself.

Filter chain:
  1. Liquidity floor       — same shape as scan_market (price/volume/turnover)
  2. No spike               — today's |change%| AND the largest single-day
                               |change%| anywhere in the lookback window stay
                               small. This is explicitly NOT a mover scan; if
                               anything in the window looks like scan_market
                               would flag it, this screen excludes the name.
  3. Rising participation   — recent average volume clears the prior baseline
                               average by min_volume_ratio, AND no single day
                               in the recent window accounts for more than
                               max_single_day_volume_share of that window's
                               total volume — otherwise "rising volume" is one
                               big print, not real accumulation.
  4. Tight range             — average daily (high-low)/prev_close over the
                               lookback window stays under max_avg_range_pct.
  5. Steady mild uptrend      — close above EMA20 above EMA50 (basic healthy
                               structure, not a falling knife), but the
                               cumulative return over the lookback window is
                               small and positive — a name that already ran
                               is scan_market's job, not this one.
  6. Rank                     — by participation increase, tie-broken by
                               tighter range (more conviction it's genuinely
                               quiet, not just less liquid).

IMPORTANT — this is an UNVALIDATED hypothesis screen. §6's alpha evidence
only covers the mover-fade / mover-chase axis; nothing in this module has
been backtested. Treat hits as research candidates, not a proven edge — do
not present this with the confidence of the mover-fade result until it has
been through the same point-in-time backtest loop that result went through.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from llmfin.data_store import _recent_panel
from llmfin.indicators import ema

LOOKBACK_DAYS = 20  # window for the range/no-spike/cumulative-return checks
RECENT_DAYS = 10  # "now" window for the volume-rising comparison
BASELINE_DAYS = 20  # "before" window for the volume-rising comparison
CALENDAR_DAYS_BACK = 90  # raw pull depth: BASELINE+RECENT plus EMA50 warmup


@dataclass
class AccumulationHit:
    symbol: str
    date: str
    close: float
    volume_ratio: float  # recent 10d avg volume / prior 20d avg volume
    max_single_day_volume_share: float  # biggest single day's share of the recent window's total volume
    avg_range_pct: float  # mean (high-low)/prev_close over the lookback window
    max_abs_change_pct: float  # largest single-day |change%| in the lookback window
    cum_return_pct: float  # close vs close LOOKBACK_DAYS trading days ago
    turnover_cr: float
    score: float


def scan_quiet_accumulation(
    min_price: float = 100.0,
    min_avg_volume: int = 500_000,
    min_turnover_cr: float = 10.0,
    min_volume_ratio: float = 1.3,
    max_single_day_volume_share: float = 0.35,
    max_avg_range_pct: float = 3.5,
    max_single_day_move_pct: float = 6.0,
    min_cum_return_pct: float = 0.0,
    max_cum_return_pct: float = 12.0,
    limit: int = 15,
) -> list[AccumulationHit]:
    """Screen the whole NSE universe for quiet-accumulation candidates (see
    module docstring for the full filter chain and its rationale). Uses the
    local bhavcopy DB — run ingest_market_data first if it errors."""
    df, latest = _recent_panel(CALENDAR_DAYS_BACK)
    df = df[df["prev_close"] > 0].copy()
    if df.empty:
        return []

    df["change_pct"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
    df["range_pct"] = (df["high"] - df["low"]) / df["prev_close"] * 100

    g = df.groupby("symbol", sort=False)
    df["recent_vol_avg"] = g["volume"].transform(lambda s: s.rolling(RECENT_DAYS).mean())
    df["recent_vol_max"] = g["volume"].transform(lambda s: s.rolling(RECENT_DAYS).max())
    df["baseline_vol_avg"] = g["volume"].transform(lambda s: s.shift(RECENT_DAYS).rolling(BASELINE_DAYS).mean())
    df["avg_range_pct"] = g["range_pct"].transform(lambda s: s.rolling(LOOKBACK_DAYS).mean())
    df["max_abs_change_pct"] = g["change_pct"].transform(lambda s: s.abs().rolling(LOOKBACK_DAYS).max())
    df["cum_return_pct"] = g["close"].transform(lambda s: (s / s.shift(LOOKBACK_DAYS) - 1) * 100)
    df["avg_volume_20"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=5).mean())
    df["ema20"] = g["close"].transform(lambda s: ema(s, 20))
    df["ema50"] = g["close"].transform(lambda s: ema(s, 50))

    snap = df[df["date"] == latest].copy()
    if snap.empty:
        return []

    # 1. Liquidity floor
    snap = snap[snap["close"] >= min_price]
    snap = snap[snap["avg_volume_20"] >= min_avg_volume]
    snap = snap[snap["turnover"] >= min_turnover_cr * 1e7]  # crore -> rupees

    # 2. No spike (comparisons against NaN below are False, so rows without
    # a full lookback window are naturally excluded rather than erroring)
    snap = snap[snap["change_pct"].abs() <= max_single_day_move_pct]
    snap = snap[snap["max_abs_change_pct"] <= max_single_day_move_pct]

    # 3. Rising participation, not one big print
    snap = snap[snap["baseline_vol_avg"] > 0]
    snap = snap.assign(
        volume_ratio=snap["recent_vol_avg"] / snap["baseline_vol_avg"],
        max_single_day_volume_share=snap["recent_vol_max"] / (snap["recent_vol_avg"] * RECENT_DAYS),
    )
    snap = snap[snap["volume_ratio"] >= min_volume_ratio]
    snap = snap[snap["max_single_day_volume_share"] <= max_single_day_volume_share]

    # 4. Tight range
    snap = snap[snap["avg_range_pct"] <= max_avg_range_pct]

    # 5. Steady mild uptrend
    snap = snap[(snap["close"] > snap["ema20"]) & (snap["ema20"] > snap["ema50"])]
    snap = snap[snap["cum_return_pct"].between(min_cum_return_pct, max_cum_return_pct)]

    if snap.empty:
        return []

    # 6. Rank — favour bigger participation increase, tie-broken by tighter range.
    snap = snap.assign(score=snap["volume_ratio"] * 2.0 - snap["avg_range_pct"] * 0.3)
    snap = snap.sort_values("score", ascending=False).head(limit)

    return [
        AccumulationHit(
            symbol=r.symbol,
            date=str(r.date),
            close=round(r.close, 2),
            volume_ratio=round(r.volume_ratio, 2),
            max_single_day_volume_share=round(r.max_single_day_volume_share, 2),
            avg_range_pct=round(r.avg_range_pct, 2),
            max_abs_change_pct=round(r.max_abs_change_pct, 2),
            cum_return_pct=round(r.cum_return_pct, 2),
            turnover_cr=round(r.turnover / 1e7, 1),
            score=round(r.score, 2),
        )
        for r in snap.itertuples()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan the latest trading day for quiet-accumulation candidates")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--min-price", type=float, default=100.0)
    parser.add_argument("--min-volume-ratio", type=float, default=1.3)
    parser.add_argument("--max-avg-range-pct", type=float, default=3.5)
    args = parser.parse_args()
    hits = scan_quiet_accumulation(
        limit=args.limit,
        min_price=args.min_price,
        min_volume_ratio=args.min_volume_ratio,
        max_avg_range_pct=args.max_avg_range_pct,
    )
    print(json.dumps([asdict(h) for h in hits], indent=2))


if __name__ == "__main__":
    main()
