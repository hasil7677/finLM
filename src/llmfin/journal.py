"""
journal.py
──────────
Decision journal + EOD review — the feedback loop that separates an
intelligence layer from an indicator toy.

Every pick is logged WITH the reasoning at decision time. eod_review then
scores past decisions against what the market actually did (from the local
bhavcopy DB), so after a few weeks you have hard hit-rate data instead of
vibes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from llmfin.data_store import DATA_DIR, load_history

JOURNAL_DB = DATA_DIR / "journal.db"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(JOURNAL_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc      TEXT NOT NULL,
            trade_date  TEXT NOT NULL,      -- YYYY-MM-DD the decision targets
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,      -- BUY / SELL / AVOID
            entry       REAL,
            stop_loss   REAL,
            target      REAL,
            quantity    INTEGER,
            thesis      TEXT,               -- the reasoning, verbatim
            source      TEXT,               -- e.g. 'scanner+news', 'manual'
            outcome     TEXT,               -- filled by eod_review
            outcome_pct REAL
        )
        """
    )
    return conn


def log_decision(
    symbol: str,
    direction: str,
    thesis: str,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    quantity: Optional[int] = None,
    source: str = "llm",
    trade_date: Optional[str] = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    trade_date = trade_date or now.strftime("%Y-%m-%d")
    conn = _connect()
    cur = conn.execute(
        """
        INSERT INTO decisions
        (ts_utc, trade_date, symbol, direction, entry, stop_loss, target, quantity, thesis, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now.isoformat(timespec="seconds"),
            trade_date,
            symbol.upper(),
            direction.upper(),
            entry,
            stop_loss,
            target,
            quantity,
            thesis,
            source,
        ),
    )
    conn.commit()
    decision_id = cur.lastrowid
    conn.close()
    return {"decision_id": decision_id, "symbol": symbol.upper(), "trade_date": trade_date}


def _score_decision(row: sqlite3.Row) -> tuple[Optional[str], Optional[float]]:
    """Score one decision against bhavcopy data from its trade date onward."""
    hist = load_history(row["symbol"], lookback_days=30)
    if hist.empty:
        return None, None
    after = hist[hist["date"] >= row["trade_date"]]
    if after.empty:
        return None, None

    entry = row["entry"] or float(after.iloc[0]["open"])
    last_close = float(after.iloc[-1]["close"])
    direction = row["direction"]

    if direction not in ("BUY", "SELL"):
        # AVOID calls: outcome is what you dodged — % move since the call.
        pct = (last_close - entry) / entry * 100
        return "avoided", round(pct, 2)

    sign = 1 if direction == "BUY" else -1
    pct = sign * (last_close - entry) / entry * 100

    stop, target = row["stop_loss"], row["target"]
    outcome = "open"
    for r in after.itertuples():
        if direction == "BUY":
            if stop and r.low <= stop:
                return "stopped_out", round(sign * (stop - entry) / entry * 100, 2)
            if target and r.high >= target:
                return "target_hit", round(sign * (target - entry) / entry * 100, 2)
        else:
            if stop and r.high >= stop:
                return "stopped_out", round(sign * (stop - entry) / entry * 100, 2)
            if target and r.low <= target:
                return "target_hit", round(sign * (target - entry) / entry * 100, 2)
    return outcome, round(pct, 2)


def eod_review(trade_date: Optional[str] = None, rescore_open: bool = True) -> dict[str, Any]:
    """Score journal decisions against actual market data. With no date given,
    scores every un-scored (or still-open) decision."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    if trade_date:
        rows = conn.execute("SELECT * FROM decisions WHERE trade_date = ?", (trade_date,)).fetchall()
    elif rescore_open:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE outcome IS NULL OR outcome = 'open'"
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM decisions WHERE outcome IS NULL").fetchall()

    reviewed = []
    for row in rows:
        outcome, pct = _score_decision(row)
        if outcome is None:
            continue
        conn.execute(
            "UPDATE decisions SET outcome = ?, outcome_pct = ? WHERE id = ?",
            (outcome, pct, row["id"]),
        )
        reviewed.append(
            {
                "decision_id": row["id"],
                "symbol": row["symbol"],
                "direction": row["direction"],
                "trade_date": row["trade_date"],
                "outcome": outcome,
                "outcome_pct": pct,
                "thesis": (row["thesis"] or "")[:200],
            }
        )
    conn.commit()

    stats = conn.execute(
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN outcome = 'target_hit' THEN 1 ELSE 0 END) AS targets,
               SUM(CASE WHEN outcome = 'stopped_out' THEN 1 ELSE 0 END) AS stops,
               ROUND(AVG(outcome_pct), 2) AS avg_pct
        FROM decisions
        WHERE direction IN ('BUY','SELL') AND outcome IS NOT NULL AND outcome != 'open'
        """
    ).fetchone()
    conn.close()

    return {
        "reviewed_now": reviewed,
        "all_time": {
            "closed_decisions": stats["n"],
            "targets_hit": stats["targets"],
            "stopped_out": stats["stops"],
            "avg_outcome_pct": stats["avg_pct"],
        },
    }


def get_journal(limit: int = 50) -> list[dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
