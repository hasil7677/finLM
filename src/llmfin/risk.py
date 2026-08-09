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
import math
import os
import sqlite3
import unicodedata
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

# Order types whose `price` argument is a genuine upper bound on the fill.
# A BUY LIMIT/SL fills at or below its price, so the caller-supplied price is a
# safe basis for the order-value cap. Everything else (MARKET, SL-M, and every
# SELL) fills at whatever the market says, so a caller-supplied price there is
# an unverified claim — and the caller is the LLM.
PRICE_BINDING_ORDER_TYPES = {"LIMIT", "SL"}

DEFAULT_MANDATE_EXAMPLE = {
    "max_order_value_inr": 50000,
    "max_daily_value_inr": 150000,
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


def _norm_symbol(s: Any) -> str:
    """Canonicalise a symbol for allowlist/blocklist matching.

    A blocklist is only as good as the comparison behind it, and a symbol is
    attacker-controlled text. Three classes of evasion have to die here:

      "RELIANCE " / "RELI ANCE"  — ordinary whitespace
      "YES​BANK"            — zero-width space, word joiner, soft hyphen:
                                   invisible in a diff, defeats ==
      "ＹＥＳ..."    — full-width forms, which .upper() leaves
                                   full-width so they never match ASCII

    NFKC folds compatibility forms (full-width → ASCII); stripping the space,
    format and control categories removes the invisibles.
    """
    s = unicodedata.normalize("NFKC", str(s))
    s = "".join(
        c for c in s
        if not c.isspace() and unicodedata.category(c) not in ("Cf", "Cc", "Zs", "Zl", "Zp")
    )
    return s.upper()


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


def order_value_today() -> float:
    """Rupees already committed today, summed from the order ledger.

    A per-order cap alone bounds nothing useful: without this, five orders of
    ₹49,999 each clear a ₹50,000 mandate one at a time. The count cap limits
    the damage, but the user's real question is "how much can it spend today",
    and that needs the aggregate.
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _orders_db()
    total = conn.execute(
        "SELECT COALESCE(SUM(est_value), 0) FROM placed_orders WHERE day = ?", (day,)
    ).fetchone()[0]
    conn.close()
    return float(total)


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
    order_type: str = "MARKET",
    est_price_source: str = "unknown",
) -> RiskVerdict:
    """Validate an order against the user's mandate. Fails closed.

    `est_price_source` says where `est_price` came from — "market" (a live
    quote), "limit_price" (supplied by the caller, i.e. the LLM), or
    "unavailable". The gate trusts a caller-supplied price only where the
    order type makes it a real ceiling on the fill; see
    PRICE_BINDING_ORDER_TYPES.
    """
    reasons: list[str] = []

    ks = kill_switch_active()
    if ks:
        return RiskVerdict(False, [f"KILL SWITCH is active at {ks} — delete the file to re-enable trading."])

    try:
        mandate = load_mandate()
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return RiskVerdict(
            False,
            [
                f"Risk mandate at {RISK_FILE} exists but could not be read ({exc}). "
                "Orders are blocked until it is valid JSON."
            ],
        )

    if mandate is None:
        return RiskVerdict(
            False,
            [
                f"No risk mandate found at {RISK_FILE.resolve()}. Orders are blocked until the USER "
                "creates it (this is deliberate — the mandate is your consent, written outside the LLM). "
                f"Example content: {json.dumps(DEFAULT_MANDATE_EXAMPLE)}"
            ],
        )

    if not isinstance(mandate, dict):
        return RiskVerdict(
            False,
            [f"Risk mandate at {RISK_FILE} must be a JSON object, got {type(mandate).__name__}."],
        )

    symbol = _norm_symbol(symbol)

    # Quantity sanity first: a non-positive or fractional quantity makes every
    # downstream cap arithmetic meaningless (a negative quantity trivially
    # "passes" both the quantity cap and the order-value cap).
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        qty_ok = False
    else:
        try:
            qty_ok = math.isfinite(quantity) and quantity > 0 and float(quantity) == int(quantity)
        except (ValueError, OverflowError):
            qty_ok = False
    qty = int(quantity) if qty_ok else 0
    if not qty_ok:
        reasons.append(f"Quantity must be a positive whole number, got {quantity!r}.")

    allowlist = [_norm_symbol(s) for s in mandate.get("symbol_allowlist", [])]
    blocklist = [_norm_symbol(s) for s in mandate.get("symbol_blocklist", [])]
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
    if max_qty is not None and qty_ok and qty > max_qty:
        reasons.append(f"Quantity {qty} exceeds max_quantity_per_order={max_qty}.")

    # A caller-supplied `price` only bounds the fill on a BUY LIMIT/SL. A
    # MARKET/SL-M order ignores it, and a SELL fills at the market however low
    # the limit is — so trusting it there is exactly how a caller talks its way
    # past the rupee caps.
    price_binds = order_type in PRICE_BINDING_ORDER_TYPES and transaction_type == "BUY"
    price_usable = (
        isinstance(est_price, (int, float))
        and not isinstance(est_price, bool)
        and math.isfinite(est_price)
        and est_price > 0
        and not (est_price_source == "limit_price" and not price_binds)
    )
    order_value = qty * est_price if (qty_ok and price_usable) else None

    max_value = mandate.get("max_order_value_inr")
    max_daily_value = mandate.get("max_daily_value_inr")

    if (max_value is not None or max_daily_value is not None) and not price_usable:
        if est_price_source == "limit_price" and not price_binds:
            reasons.append(
                f"Order value for a {transaction_type} {order_type} order cannot be verified from a "
                "caller-supplied price — that price does not bound the fill. A live quote is required."
            )
        elif est_price is None:
            reasons.append(
                "Cannot verify order value against the mandate's rupee caps — no price available. "
                "Pass a limit price or ensure live quotes are reachable."
            )
        else:
            reasons.append(
                f"Price estimate {est_price!r} is not a positive finite number — "
                "cannot verify order value against the mandate's rupee caps."
            )

    if max_value is not None and order_value is not None and order_value > max_value:
        reasons.append(
            f"Estimated order value ₹{order_value:,.0f} exceeds "
            f"max_order_value_inr=₹{max_value:,.0f}."
        )

    if max_daily_value is not None and order_value is not None:
        committed = order_value_today()
        if committed + order_value > max_daily_value:
            reasons.append(
                f"Order value ₹{order_value:,.0f} on top of ₹{committed:,.0f} already committed "
                f"today exceeds max_daily_value_inr=₹{max_daily_value:,.0f}."
            )

    max_daily = mandate.get("max_orders_per_day")
    if max_daily is not None and orders_placed_today() >= max_daily:
        reasons.append(f"Daily order cap reached ({max_daily} orders today).")

    return RiskVerdict(len(reasons) == 0, reasons or ["All mandate checks passed."])
