"""
test_tool_contract.py
─────────────────────
Does what the project *says* it exposes match what it actually exposes?

Borrowed from a failure mode documented in the TradingAgents A-share fork: a
prompt advertised access to dragon-tiger seat data that the underlying tool
never returned, and the model confabulated the gap rather than reporting it.
An LLM handed a capability list it can't actually call will invent the
difference - so the capability list is a contract, and contracts get tested.

Two things are checked here:
  1. Every tool the server registers is described in its own docstring tool map.
  2. Every tool-count claim in the docs matches the real number.

Both had drifted when this test was written: the server registered 15 tools,
its docstring header said 15 but enumerated only 14 (get_risk_status was
missing), and the README still said 13.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

server = pytest.importorskip("llmfin.server")

REPO_ROOT = Path(server.__file__).resolve().parents[2]  # .../finLM/src/llmfin/server.py
DOCS_CLAIMING_A_COUNT = ["README.md", "PROJECT_DEEP_DIVE.md"]


@pytest.fixture(scope="module")
def registered() -> list[str]:
    return sorted(t.name for t in asyncio.run(server.mcp.list_tools()))


def test_every_registered_tool_is_in_the_server_tool_map(registered):
    """The module docstring is what a reader (and a reviewer) treats as the
    capability list. A tool missing from it is an undocumented capability."""
    tool_map = server.__doc__ or ""
    missing = [name for name in registered if name not in tool_map]
    assert not missing, (
        f"registered but absent from server.py's tool map docstring: {missing}"
    )


def test_tool_map_advertises_nothing_that_does_not_exist(registered):
    """The inverse, and the one that actually causes confabulation: a tool map
    promising something the server cannot do."""
    tool_map = server.__doc__ or ""
    # Indented two-space entries in the tool map are tool names.
    advertised = set(re.findall(r"^ {4}(\w+) {2,}", tool_map, re.MULTILINE))
    phantom = sorted(advertised - set(registered))
    assert not phantom, f"tool map advertises non-existent tools: {phantom}"


def test_server_docstring_count_matches_reality(registered):
    header = re.search(r"Tool map \((\d+) tools", server.__doc__ or "")
    assert header, "server.py docstring no longer states a tool count"
    assert int(header.group(1)) == len(registered)


def test_unvalidated_screen_declares_itself_in_its_payload():
    """A tool with no evidence behind it must say so in the DATA it returns,
    not only in its docstring.

    An LLM handed a clean list of stock picks will present them as picks. The
    docstring is documentation for a human reading the source; the payload is
    what actually reaches the model's context. This is the same failure the
    tool-contract tests above guard against, one level down."""
    import inspect

    src = inspect.getsource(server.scan_accumulation)
    assert "UNVALIDATED" in src.split('"""')[-1], (
        "scan_accumulation's UNVALIDATED status appears only in its docstring, "
        "not in the payload it returns"
    )


@pytest.mark.parametrize("doc", DOCS_CLAIMING_A_COUNT)
def test_documented_tool_counts_match_reality(doc, registered):
    """Catches the README quietly becoming fiction as tools get added."""
    path = REPO_ROOT / doc
    if not path.exists():
        pytest.skip(f"{doc} not present")
    text = path.read_text(encoding="utf-8", errors="ignore")
    claims = {int(n) for n in re.findall(r"(\d+)\s+tools\b", text)}
    wrong = {n for n in claims if n != len(registered)}
    assert not wrong, (
        f"{doc} claims {sorted(wrong)} tools; the server registers {len(registered)}"
    )
