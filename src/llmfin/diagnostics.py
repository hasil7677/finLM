"""
diagnostics.py
──────────────
Standing data-quality diagnostics over the local bhavcopy DB. Currently one
surface: `list_data_anomalies`, built from the corporate-action adjustment
log that used to only exist as a one-off scratch script run by hand during
the 2010-2020 backtest loop (see README.md project history). Reusing
backtest.py's full-history panel loader means this reports on exactly the
same adjustment decisions a backtest run would make, not a separate
recomputation that could drift from it.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Optional

from llmfin.backtest import _load_panel
from llmfin.data_store import DB_PATH


def list_data_anomalies(db_path: Path = DB_PATH, symbol: Optional[str] = None) -> dict:
    """Every suspect split/bonus ratio the corporate-action adjuster (see
    corporate_actions.py) found across the full history in `db_path`: which
    ones it back-adjusted, and which ones it flagged but left alone, with the
    reason. Run this after ingesting new history, or whenever a scan/backtest
    number looks suspicious, instead of writing a one-off script to check -
    a real split/bonus/rights issue in a symbol you follow should show up in
    `applied`; anything in `flagged` is either a genuine crash/crisis the
    guards correctly declined to touch, or a misfire worth tuning the guard
    constants for.
    """
    _panel, adjustments = _load_panel(db_path, adjust_splits=True)
    if symbol:
        adjustments = [a for a in adjustments if a["symbol"] == symbol.upper()]
    applied = [a for a in adjustments if a["applied"]]
    flagged = [a for a in adjustments if not a["applied"]]
    return {
        "db_path": str(db_path),
        "symbol_filter": symbol.upper() if symbol else None,
        "total_suspect_ratios": len(adjustments),
        "applied_count": len(applied),
        "flagged_unadjusted_count": len(flagged),
        "flagged_reasons": dict(Counter(a["reason"] for a in flagged)),
        "applied": applied,
        "flagged": flagged,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Report corporate-action adjustment decisions over the local bhavcopy DB")
    p.add_argument("--db", default=str(DB_PATH), help="Path to the SQLite market DB (default: live market.db)")
    p.add_argument("--symbol", help="Only report anomalies for this symbol")
    a = p.parse_args()
    print(json.dumps(list_data_anomalies(db_path=Path(a.db), symbol=a.symbol), indent=2))


if __name__ == "__main__":
    main()
