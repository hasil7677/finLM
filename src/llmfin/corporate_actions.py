"""
corporate_actions.py
─────────────────────
Heuristic split/bonus back-adjustment, shared by the backtest engine
(backtest.py) and the live data path (data_store.py).

Raw NSE bhavcopy has no corporate-action adjustment: a stock's close and
prev_close are whatever literally printed that day. A 1:2 split makes the
close halve overnight with nothing else changing — which looks exactly like
a -50% crash to anything that reads prev_close, a trailing average, or an
indicator window spanning the event. That's a false "mover" in the live
scanner and a corrupted history window in the backtest.

Detection: a single-day close/prev_close ratio outside CA_LOW_RATIO..
CA_HIGH_RATIO is "suspect" (NSE circuit filters cap most organic single-day
moves well inside this range). A suspect ratio is only back-adjusted when
ALL THREE hold:
  1. It's close (in log space) to a common split/bonus/rights ratio.
  2. Event-day volume isn't a panic-scale spike vs its trailing 20-day
     average (CA_MAX_VOLUME_MULT) — a real crash can coincidentally land
     near a clean ratio (e.g. FINANTECH's Aug-2013 NSEL-scam crash was
     close to 1/3), but trades at 10-1000x normal volume, not the 2-6x a
     mechanical split shows.
  3. It's isolated — no other suspect ratio for the same symbol within
     CA_CLUSTER_WINDOW trading days. A name mid-crisis (e.g. JETAIRWAYS'
     2019 bankruptcy death-spiral) can pass both checks above on a single
     bad day whose own volume baseline is already crisis-elevated; real
     splits are one-off events, not part of a cluster of anomalies.
All three guards were added after concrete false-positive misfires found
while backtesting NSE 2010-2020 — see that period's diagnostic notes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CA_LOW_RATIO = 0.6
CA_HIGH_RATIO = 1.67
CA_COMMON_RATIOS = [0.1, 0.125, 0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0]
CA_LOG_TOLERANCE = 0.15
CA_MAX_VOLUME_MULT = 15.0  # event-day volume vs trailing 20-day avg, above which it's treated as a real move
CA_CLUSTER_WINDOW = 10  # trading days; another anomaly this close means "crisis", not "split"


def adjust_corporate_actions(panel: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Best-effort back-adjustment for splits/bonuses (see module docstring).

    `panel` needs columns: symbol, date, open, high, low, close, prev_close,
    volume (extra columns pass through untouched). Returns the adjusted
    panel plus a log of every symbol/date it touched (and any anomalies it
    flagged but declined to adjust) — inspect this log when diagnosing a
    misfire, it's the main way to catch one.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(panel["prev_close"] > 0, panel["close"] / panel["prev_close"], np.nan)
    is_suspect = (ratio < CA_LOW_RATIO) | (ratio > CA_HIGH_RATIO)
    if not is_suspect.any():
        return panel, []

    panel = panel.assign(
        _susp=is_suspect,
        _avg_vol_20=panel.groupby("symbol", sort=False)["volume"]
        .transform(lambda s: s.shift(1).rolling(20, min_periods=5).mean()),
    )
    suspect_symbols = set(panel.loc[panel["_susp"], "symbol"].unique())
    rest = panel[~panel["symbol"].isin(suspect_symbols)].drop(columns=["_susp", "_avg_vol_20"])
    susp_panel = panel[panel["symbol"].isin(suspect_symbols)]

    adjustments: list[dict] = []
    parts = [rest]
    for symbol, g in susp_panel.groupby("symbol", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        factors = np.ones(len(g))
        suspect_positions = g.index[g["_susp"]].to_numpy()
        for idx in suspect_positions:
            raw_ratio = float(g.loc[idx, "close"] / g.loc[idx, "prev_close"])
            best = min(CA_COMMON_RATIOS, key=lambda r: abs(np.log(r) - np.log(raw_ratio)))
            dist = abs(np.log(best) - np.log(raw_ratio))
            avg_vol = g.loc[idx, "_avg_vol_20"]
            vol_mult = float(g.loc[idx, "volume"] / avg_vol) if avg_vol and avg_vol > 0 else None
            others = suspect_positions[suspect_positions != idx]
            isolated = bool(len(others) == 0 or np.min(np.abs(others - idx)) > CA_CLUSTER_WINDOW)
            entry = {
                "symbol": symbol,
                "date": str(g.loc[idx, "date"]),
                "raw_ratio": round(raw_ratio, 3),
                "volume_vs_trailing_avg": round(vol_mult, 1) if vol_mult is not None else None,
            }
            ratio_ok = dist <= CA_LOG_TOLERANCE
            volume_ok = vol_mult is not None and vol_mult <= CA_MAX_VOLUME_MULT
            if ratio_ok and volume_ok and isolated:
                factors[:idx] *= best
                entry["applied_ratio"] = best
                entry["applied"] = True
            else:
                entry["applied"] = False
                if not ratio_ok:
                    entry["reason"] = "no close common-ratio match -- left unadjusted, flagged for review"
                elif not isolated:
                    entry["reason"] = (
                        "another anomaly within 10 trading days of this one -- likely a volatile/"
                        "crisis period, not an isolated mechanical split -- left unadjusted, flagged for review"
                    )
                elif vol_mult is None:
                    entry["reason"] = (
                        "ratio matched but no trailing volume baseline (new listing/demerger) "
                        "-- left unadjusted, flagged for review"
                    )
                else:
                    entry["reason"] = (
                        "ratio matched but volume spiked (real move, not a mechanical split) "
                        "-- left unadjusted, flagged for review"
                    )
            adjustments.append(entry)
        for col in ("open", "high", "low", "close", "prev_close"):
            g[col] = g[col] * factors
        g["volume"] = g["volume"] / factors
        parts.append(g.drop(columns=["_susp", "_avg_vol_20"]))

    adjusted = pd.concat(parts, ignore_index=True).sort_values(["symbol", "date"]).reset_index(drop=True)
    return adjusted, adjustments
