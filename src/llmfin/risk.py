"""
risk.py
───────
Server-side risk gate — the Vibe-Trading "mandate" pattern.

The old `confirmed=true` flag was theater: the LLM sets that flag itself, so
it guards nothing. Real guardrails live HERE, in code the model cannot talk
its way around:

  • risk_limits.json  — a mandate the USER writes (order caps, daily limits,
    product allowlist). Missing file ⇒ orders are blocked, not defaulted open.
  • KILL_SWITCH       — touch a file named KILL_SWITCH next to the mandate
    (or in LLMFIN_DATA_DIR) and every order is rejected until it's deleted.
  • Daily order count — tracked in SQLite, enforced per calendar day.

`place_order` calls check_order() and refuses to reach Kite unless it passes.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from llmfin.data_store import DATA_DIR

RISK_FILE = Path(os.getenv("LLMFIN_RISK_FILE", "risk_limits.json"))
KILL_SWITCH_LOCATIONS = [
    Path("KILL_SWITCH"),
    DATA_DIR / "KILL_SWITCH",
]

DEFAULT_MANDATE_EXAMPLE = {
    "max_order_value_inr": 50000,
    "max_quantity_per_order": 100,
    "max_orders_per_day": 5,
    "allowed_transaction_types": ["BUY", "SELL"],
    "allowed_products": ["CNC"],
    "allowed_exchanges": ["NSE"],
    "symbol_allowlist": [],
    "symbol_blocklist": [],
}


@dataclass
class RiskVerdict:
    allowed: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reasons": self.reasons}


def load_mandate() -> Optional[dict[str, Any]]:
    if not RISK_FILE.exists():
        return None
    return json.loads(RISK_FILE.read_text())


def kill_switch_active() -> Optional[str]:
    for loc in KILL_SWITCH_LOCATIONS:
        if loc.exists():
            return str(loc.resolve())
    return None


def _orders_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "orders.db")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS placed_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT, day TEXT, symbol TEXT, transaction_type TEXT,
            quantity INTEGER, est_value REAL, kite_order_id TEXT
        )
        """
    )
    return conn


def orders_placed_today() -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _orders_db()
    n = conn.execute("SELECT COUNT(*) FROM placed_orders WHERE day = ?", (day,)).fetchone()[0]
    conn.close()
    return n


def record_order(symbol: str, transaction_type: str, quantity: int, est_value: float, kite_order_id: str) -> None:
    now = datetime.now(timezone.utc)
    conn = _orders_db()
    conn.execute(
        "INSERT INTO placed_orders (ts_utc, day, symbol, transaction_type, quantity, est_value, kite_order_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now.isoformat(timespec="seconds"), now.strftime("%Y-%m-%d"), symbol.upper(),
         transaction_type, quantity, est_value, kite_order_id),
    )
    conn.commit()
    conn.close()


def check_order(
    symbol: str,
    transaction_type: str,
    quantity: int,
    exchange: str,
    product: str,
    est_price: Optional[float],
) -> RiskVerdict:
    """Validate an order against the user's mandate. Fails closed."""
    reasons: list[str] = []

    ks = kill_switch_active()
    if ks:
        return RiskVerdict(False, [f"KILL SWITCH is active at {ks} — delete the file to re-enable trading."])

    mandate = load_mandate()
    if mandate is None:
        return RiskVerdict(
            False,
            [
                f"No risk mandate found at {RISK_FILE.resolve()}. Orders are blocked until the USER "
                "creates it (this is deliberate — the mandate is your consent, written outside the LLM). "
                f"Example content: {json.dumps(DEFAULT_MANDATE_EXAMPLE)}"
            ],
        )

    symbol = symbol.upper()

    allowlist = [s.upper() for s in mandate.get("symbol_allowlist", [])]
    blocklist = [s.upper() for s in mandate.get("symbol_blocklist", [])]
    if blocklist and symbol in blocklist:
        reasons.append(f"{symbol} is on your symbol_blocklist.")
    if allowlist and symbol not in allowlist:
        reasons.append(f"{symbol} is not on your symbol_allowlist ({allowlist}).")

    if transaction_type not in mandate.get("allowed_transaction_types", ["BUY", "SELL"]):
        reasons.append(f"Transaction type {transaction_type} not allowed by mandate.")
    if product not in mandate.get("allowed_products", ["CNC"]):
        reasons.append(f"Product {product} not allowed by mandate (allowed: {mandate.get('allowed_products')}).")
    if exchange not in mandate.get("allowed_exchanges", ["NSE"]):
        reasons.append(f"Exchange {exchange} not allowed by mandate.")

    max_qty = mandate.get("max_quantity_per_order")
    if max_qty is not None and quantity > max_qty:
        reasons.append(f"Quantity {quantity} exceeds max_quantity_per_order={max_qty}.")

    max_value = mandate.get("max_order_value_inr")
    if max_value is not None:
        if est_price is None:
            reasons.append(
                "Cannot verify order value against max_order_value_inr — no price available. "
                "Pass a limit price or ensure live quotes are reachable."
            )
        elif quantity * est_price > max_value:
            reasons.append(
                f"Estimated order value ₹{quantity * est_price:,.0f} exceeds "
                f"max_order_value_inr=₹{max_value:,.0f}."
            )

    max_daily = mandate.get("max_orders_per_day")
    if max_daily is not None and orders_placed_today() >= max_daily:
        reasons.append(f"Daily order cap reached ({max_daily} orders today).")

    return RiskVerdict(len(reasons) == 0, reasons or ["All mandate checks passed."])
