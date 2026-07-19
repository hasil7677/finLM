"""
scanner.py
──────────
Market discovery: cut the full NSE universe (~2000 names) down to the 10-15
that are actually worth a look today, using deterministic math over the local
bhavcopy DB. No broker API, no LLM, no subscription.

Filter chain (classic intraday-desk recipe):
  1. Liquidity floor   — price, average volume, turnover
  2. Activity          — gap % vs previous close, volume ratio vs 20-day avg
  3. Rank              — by a composite of |gap| and volume ratio

The LLM's job starts AFTER this: explain WHY the survivors are moving
(research_symbol) and rank them into a watchlist.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Literal, Optional

from llmfin.data_store import latest_snapshot


@dataclass
class ScanHit:
    symbol: str
    date: str
    close: float
    prev_close: float
    gap_pct: float          # open vs previous close
    change_pct: float       # close vs previous close
    volume: int
    volume_ratio: float     # today's volume / 20-day average
    turnover_cr: float      # turnover in ₹ crore
    range_pct: float        # (high-low)/prev_close — intraday travel
    score: float


def scan_market(
    min_price: float = 100.0,
    max_price: Optional[float] = None,
    min_avg_volume: int = 500_000,
    min_turnover_cr: float = 10.0,
    min_gap_pct: float = 0.0,
    min_volume_ratio: float = 1.5,
    min_abs_change_pct: float = 1.0,
    direction: Literal["up", "down", "both"] = "both",
    limit: int = 15,
) -> list[ScanHit]:
    """Run the deterministic screen over the latest ingested trading day."""
    df = latest_snapshot()

    # 1. Liquidity floor
    df = df[df["close"] >= min_price]
    if max_price:
        df = df[df["close"] <= max_price]
    df = df[df["avg_volume_20"] >= min_avg_volume]
    df = df[df["turnover"] >= min_turnover_cr * 1e7]  # crore → rupees

    df = df[df["prev_close"] > 0].copy()

    # 2. Activity
    df["gap_pct"] = (df["open"] - df["prev_close"]) / df["prev_close"] * 100
    df["change_pct"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
    df["volume_ratio"] = df["volume"] / df["avg_volume_20"]
    df["range_pct"] = (df["high"] - df["low"]) / df["prev_close"] * 100

    df = df[df["volume_ratio"] >= min_volume_ratio]
    df = df[df["change_pct"].abs() >= min_abs_change_pct]
    if min_gap_pct > 0:
        df = df[df["gap_pct"].abs() >= min_gap_pct]
    if direction == "up":
        df = df[df["change_pct"] > 0]
    elif direction == "down":
        df = df[df["change_pct"] < 0]

    # 3. Rank — favour big participation over big % move (vol ratio capped so
    # one illiquid spike can't dominate).
    df["score"] = df["change_pct"].abs() * 0.5 + df["volume_ratio"].clip(upper=10) * 1.0

    df = df.sort_values("score", ascending=False).head(limit)

    return [
        ScanHit(
            symbol=r.symbol,
            date=str(r.date),
            close=round(r.close, 2),
            prev_close=round(r.prev_close, 2),
            gap_pct=round(r.gap_pct, 2),
            change_pct=round(r.change_pct, 2),
            volume=int(r.volume),
            volume_ratio=round(r.volume_ratio, 2),
            turnover_cr=round(r.turnover / 1e7, 1),
            range_pct=round(r.range_pct, 2),
            score=round(r.score, 2),
        )
        for r in df.itertuples()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan the latest trading day for movers")
    parser.add_argument("--direction", choices=["up", "down", "both"], default="both")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--min-price", type=float, default=100.0)
    parser.add_argument("--min-volume-ratio", type=float, default=1.5)
    args = parser.parse_args()
    hits = scan_market(
        direction=args.direction,
        limit=args.limit,
        min_price=args.min_price,
        min_volume_ratio=args.min_volume_ratio,
    )
    print(json.dumps([asdict(h) for h in hits], indent=2))


if __name__ == "__main__":
    main()
