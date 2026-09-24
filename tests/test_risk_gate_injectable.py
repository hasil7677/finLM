"""
test_risk_gate_injectable.py
─────────────────────────────
Same red-team suite as test_risk_gate_adversarial.py, ported to the
injectable-parameter call shape check_order() grows for FinLM 2.0's
multi-tenant platform (see plan.md Phase 2): a caller supplies the mandate,
kill-switch state, and today's order-count/value directly instead of
check_order() reaching into global file/SQLite state itself.

This file exists to prove the injectable path enforces exactly the same
attack refusals as the original file-based path - same mandate content, same
attacks, same expected verdicts, just supplied as arguments instead of fixture
files. It does not re-test finLM's file-I/O-specific behavior (unreadable
JSON, non-object mandate) - those are about load_mandate()'s own robustness,
which is untouched by this refactor and stays covered by the original suite.

Written before the refactor existed - runs RED (TypeError: unexpected keyword
argument) against the pre-refactor check_order(), GREEN after.
"""

from __future__ import annotations

import math

import pytest

from llmfin import risk as risk_mod

MANDATE = {
    "max_order_value_inr": 50_000,
    "max_quantity_per_order": 100,
    "max_orders_per_day": 5,
    "allowed_transaction_types": ["BUY", "SELL"],
    "allowed_products": ["CNC"],
    "allowed_exchanges": ["NSE"],
    "symbol_allowlist": [],
    "symbol_blocklist": ["YESBANK"],
}

LTP = 5_000.0


def order(**overrides):
    """A legal baseline order, fully injected: mandate present, kill switch
    off, zero orders/value committed today so far - override to build an
    attack, exactly like test_risk_gate_adversarial.py's helper."""
    params = dict(
        symbol="RELIANCE",
        transaction_type="BUY",
        quantity=5,
        exchange="NSE",
        product="CNC",
        est_price=LTP,
        order_type="MARKET",
        est_price_source="market",
        injected_mandate=MANDATE,
        kill_switch_reason=None,
        orders_today=0,
        value_today=0.0,
    )
    params.update(overrides)
    return risk_mod.check_order(**params)


# ── A. Fail closed ───────────────────────────────────────────────────────────

def test_injected_none_mandate_blocks_orders():
    verdict = order(injected_mandate=None)
    assert not verdict.allowed


def test_baseline_order_is_allowed():
    assert order().allowed


# ── B. Kill switch ───────────────────────────────────────────────────────────

def test_injected_kill_switch_overrides_a_valid_mandate():
    verdict = order(kill_switch_reason="/tmp/KILL_SWITCH")
    assert not verdict.allowed
    assert "KILL SWITCH" in verdict.reasons[0]


def test_injected_kill_switch_checked_before_mandate():
    verdict = order(injected_mandate=None, kill_switch_reason="/tmp/KILL_SWITCH")
    assert "KILL SWITCH" in verdict.reasons[0]


# ── C. Order-value evasion ───────────────────────────────────────────────────

def test_market_order_cannot_be_valued_from_caller_supplied_price():
    verdict = order(quantity=100, est_price=1.0, order_type="MARKET",
                     est_price_source="limit_price")
    assert not verdict.allowed
    assert "does not bound the fill" in " ".join(verdict.reasons)


def test_buy_limit_may_be_valued_from_its_limit_price():
    assert order(quantity=100, est_price=400.0, order_type="LIMIT",
                 est_price_source="limit_price").allowed


def test_buy_limit_over_the_cap_is_still_blocked():
    verdict = order(quantity=100, est_price=600.0, order_type="LIMIT",
                     est_price_source="limit_price")
    assert not verdict.allowed
    assert "exceeds" in " ".join(verdict.reasons)


@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), float("-inf"), -1.0, 0.0])
def test_nonsense_prices_cannot_satisfy_the_value_check(bad_price):
    verdict = order(quantity=100, est_price=bad_price, order_type="LIMIT",
                     est_price_source="limit_price")
    assert not verdict.allowed


# ── D. Quantity abuse ────────────────────────────────────────────────────────

@pytest.mark.parametrize("qty", [-100, -1, 0])
def test_non_positive_quantity_is_rejected(qty):
    verdict = order(quantity=qty)
    assert not verdict.allowed
    assert "positive whole number" in " ".join(verdict.reasons)


def test_quantity_cap_still_enforced():
    verdict = order(quantity=101, est_price=1.0, order_type="LIMIT",
                     est_price_source="limit_price")
    assert not verdict.allowed
    assert "max_quantity_per_order" in " ".join(verdict.reasons)


# ── E. Symbol-list evasion ───────────────────────────────────────────────────

@pytest.mark.parametrize("spelling", [
    "YESBANK", "yesbank", " YESBANK", "YES​BANK", "ＹＥＳＢＡＮＫ",
])
def test_blocklist_cannot_be_evaded_by_case_whitespace_or_unicode(spelling):
    verdict = order(symbol=spelling)
    assert not verdict.allowed
    assert "blocklist" in " ".join(verdict.reasons)


def test_allowlist_excludes_everything_not_named():
    mandate = {**MANDATE, "symbol_allowlist": ["RELIANCE"]}
    assert order(injected_mandate=mandate, symbol="RELIANCE").allowed
    assert not order(injected_mandate=mandate, symbol="TATASTEEL").allowed


# ── F. Privilege escalation ──────────────────────────────────────────────────

@pytest.mark.parametrize("product", ["MIS", "NRML", "mis", "CNC "])
def test_product_outside_the_mandate_is_rejected(product):
    assert not order(product=product).allowed


@pytest.mark.parametrize("exchange", ["BSE", "NFO", "MCX", "nse"])
def test_exchange_outside_the_mandate_is_rejected(exchange):
    assert not order(exchange=exchange).allowed


# ── G. Daily cap ─────────────────────────────────────────────────────────────

def test_daily_order_cap_is_enforced_via_injection():
    verdict = order(orders_today=MANDATE["max_orders_per_day"])
    assert not verdict.allowed
    assert "Daily order cap" in " ".join(verdict.reasons)


def test_aggregate_daily_value_cap_is_enforced_via_injection():
    mandate = {**MANDATE, "max_daily_value_inr": 120_000, "max_orders_per_day": 50}
    legal = dict(injected_mandate=mandate, quantity=10, est_price=4_999.0,
                 est_price_source="market")
    assert order(**legal, value_today=0.0).allowed
    verdict = order(**legal, value_today=99_980.0)  # third order takes the day to ~150k
    assert not verdict.allowed
    assert "max_daily_value_inr" in " ".join(verdict.reasons)


# ── Precedence: injected values must be used, not the global fallback ───────

def test_injected_mandate_is_used_instead_of_the_global_file(tmp_path, monkeypatch):
    """The whole point of this refactor: a caller that injects a mandate must
    not have it silently overridden by whatever risk_limits.json happens to
    say on disk. Point RISK_FILE at an empty tmp dir (no file -> global path
    would say 'no mandate') and confirm the injected mandate still governs."""
    monkeypatch.setattr(risk_mod, "RISK_FILE", tmp_path / "risk_limits.json")
    assert not (tmp_path / "risk_limits.json").exists()
    assert order().allowed  # injected_mandate=MANDATE, not the (nonexistent) file


def test_injected_kill_switch_is_used_instead_of_the_global_file(tmp_path, monkeypatch):
    monkeypatch.setattr(risk_mod, "KILL_SWITCH_LOCATIONS", [tmp_path / "KILL_SWITCH"])
    assert not (tmp_path / "KILL_SWITCH").exists()
    # Global kill switch file absent, but an injected reason must still block.
    verdict = order(kill_switch_reason="tenant-specific-kill-switch")
    assert not verdict.allowed
    assert "KILL SWITCH" in verdict.reasons[0]


def test_omitting_injected_params_falls_back_to_global_state(tmp_path, monkeypatch):
    """Default (no injection) callers - finLM's own CLI/MCP server - must be
    completely unaffected: omitting the new parameters falls back to reading
    the same global file/SQLite state as before."""
    monkeypatch.setattr(risk_mod, "RISK_FILE", tmp_path / "risk_limits.json")
    monkeypatch.setattr(risk_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(risk_mod, "KILL_SWITCH_LOCATIONS", [tmp_path / "KILL_SWITCH"])
    # No mandate file on disk, no injected params at all -> fails closed via
    # the original global-state path, exactly as test_no_mandate_blocks_orders
    # in the adversarial suite already proves.
    verdict = risk_mod.check_order(
        symbol="RELIANCE", transaction_type="BUY", quantity=5, exchange="NSE",
        product="CNC", est_price=LTP, order_type="MARKET", est_price_source="market",
    )
    assert not verdict.allowed
    assert "No risk mandate found" in verdict.reasons[0]
