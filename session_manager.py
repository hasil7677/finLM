"""
session_manager.py
──────────────────
Handles Zerodha Kite Connect authentication and persistent token storage.

The Zerodha OAuth flow requires:
  1.  User visits the login URL and approves.
  2.  Zerodha redirects to your redirect URL with a `request_token`.
  3.  You exchange it (with api_secret) for an `access_token` (valid till midnight IST).

In a batch / headless scenario we persist the access token in a local JSON file and
reuse it across process restarts within the same trading day.

Usage
-----
    from llmfin.session_manager import get_kite_client
    kite = get_kite_client()
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN_FILE = Path(os.getenv("LLMFIN_TOKEN_FILE", "~/.llmfin_session.json")).expanduser()
API_KEY: str = os.getenv("KITE_API_KEY", "")
API_SECRET: str = os.getenv("KITE_API_SECRET", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_token() -> Optional[dict]:
    """Load persisted session token from disk (returns None if absent/stale)."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        saved_date = data.get("date")
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if saved_date != today:
            logger.info("Cached token is from %s — expired (today is %s).", saved_date, today)
            return None
        return data
    except (json.JSONDecodeError, KeyError):
        logger.warning("Corrupted token file at %s — ignoring.", TOKEN_FILE)
        return None


def _save_token(access_token: str) -> None:
    """Persist the access token to disk tagged with today's date."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    TOKEN_FILE.write_text(json.dumps({"access_token": access_token, "date": today}, indent=2))
    logger.info("Access token saved to %s.", TOKEN_FILE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_login_url() -> str:
    """Return the Zerodha login URL so the user can authenticate manually."""
    if not API_KEY:
        raise EnvironmentError("KITE_API_KEY is not set. Check your .env file.")
    kite = KiteConnect(api_key=API_KEY)
    return kite.login_url()


def exchange_request_token(request_token: str) -> str:
    """Exchange a request_token for an access_token and persist it."""
    if not API_KEY or not API_SECRET:
        raise EnvironmentError("KITE_API_KEY / KITE_API_SECRET not set. Check your .env file.")
    kite = KiteConnect(api_key=API_KEY)
    session = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token: str = session["access_token"]
    _save_token(access_token)
    return access_token


def get_kite_client() -> KiteConnect:
    """
    Return an authenticated KiteConnect instance.

    Priority:
        1. Cached token from today (disk).
        2. KITE_ACCESS_TOKEN env var (useful for CI / scripted refresh).
        3. Raises RuntimeError guiding user to run the auth flow.
    """
    if not API_KEY:
        raise EnvironmentError("KITE_API_KEY is not set. Check your .env file.")

    kite = KiteConnect(api_key=API_KEY)

    # 1. Try disk cache
    cached = _load_token()
    if cached:
        kite.set_access_token(cached["access_token"])
        logger.info("Using cached access token.")
        return kite

    # 2. Try env var
    env_token = os.getenv("KITE_ACCESS_TOKEN")
    if env_token:
        kite.set_access_token(env_token)
        _save_token(env_token)  # persist for the rest of the day
        logger.info("Using KITE_ACCESS_TOKEN from environment.")
        return kite

    # 3. Guide user
    login_url = kite.login_url()
    raise RuntimeError(
        "No valid Zerodha session found.\n"
        f"1. Visit this URL to log in: {login_url}\n"
        "2. After redirect, copy the `request_token` from the URL.\n"
        "3. Run: python -m llmfin.auth --request-token <TOKEN>"
    )
