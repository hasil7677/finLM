"""
auth.py
───────
CLI helper to complete the Zerodha OAuth flow.

Usage
─────
    # Step 1 - Get the login URL
    python -m llmfin.auth

    # Step 2 - After login, paste the request_token from the redirect URL
    python -m llmfin.auth --request-token <TOKEN>
"""

from __future__ import annotations

import argparse
import sys

from llmfin.session_manager import exchange_request_token, get_login_url


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zerodha Kite Connect OAuth helper for llmfin"
    )
    parser.add_argument(
        "--request-token",
        metavar="TOKEN",
        help="Exchange this request_token for an access_token",
    )
    args = parser.parse_args()

    if args.request_token:
        print("Exchanging request_token ...")
        access_token = exchange_request_token(args.request_token)
        print(f"\n✅ Access token obtained and cached.\n   Token: {access_token[:8]}...{access_token[-8:]}")
        print("\nYou can now run the batch runner or MCP server.")
    else:
        url = get_login_url()
        print("\n🔐 Zerodha Login Flow")
        print("─" * 50)
        print("1. Open this URL in your browser:")
        print(f"\n   {url}\n")
        print("2. Log in and approve the permissions.")
        print("3. You will be redirected to your redirect URL.")
        print("   Copy the `request_token` query parameter from the URL.")
        print("4. Run:")
        print("   python -m llmfin.auth --request-token <PASTE_TOKEN_HERE>\n")


if __name__ == "__main__":
    main()
