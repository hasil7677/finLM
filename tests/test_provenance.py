"""
test_provenance.py
──────────────────
The run artifact is the fix for the 2026-08-09 reproducibility failure, so it
gets tests like any other load-bearing component. In particular: an artifact
that silently omits the git SHA or the data fingerprint is worse than none,
because it looks like provenance without being it.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from llmfin import provenance


@pytest.fixture
def fake_db(tmp_path) -> Path:
    db = tmp_path / "market.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE daily_prices (symbol TEXT, date TEXT, close REAL)"
    )
    conn.executemany(
        "INSERT INTO daily_prices VALUES (?, ?, ?)",
        [("RELIANCE", "2026-01-01", 100.0), ("RELIANCE", "2026-01-02", 101.0),
         ("TCS", "2026-01-01", 200.0)],
    )
    conn.commit()
    conn.close()
    return db


def test_db_fingerprint_identifies_the_snapshot_not_just_the_path(fake_db):
    """Path + filename is not identity — two different snapshots live at the
    same path over time. Rows, date range and digest are what pin it."""
    fp = provenance.db_fingerprint(fake_db)
    assert fp["rows"] == 3
    assert fp["date_min"] == "2026-01-01" and fp["date_max"] == "2026-01-02"
    assert fp["distinct_symbols"] == 2
    assert fp["sha256"] and len(fp["sha256"]) == 64


def test_db_fingerprint_changes_when_the_data_changes(fake_db):
    before = provenance.db_fingerprint(fake_db)
    conn = sqlite3.connect(fake_db)
    conn.execute("INSERT INTO daily_prices VALUES ('INFY', '2026-01-03', 50.0)")
    conn.commit()
    conn.close()
    after = provenance.db_fingerprint(fake_db)
    assert after["rows"] == before["rows"] + 1
    assert after["sha256"] != before["sha256"]


def test_db_fingerprint_handles_a_missing_db(tmp_path):
    fp = provenance.db_fingerprint(tmp_path / "nope.db")
    assert fp["exists"] is False


def test_artifact_records_code_data_environment_and_config(fake_db, tmp_path):
    dest = provenance.write_artifact(
        kind="backtest",
        config={"horizon": 10, "cost_pct": 0.4},
        result={"trades": 754, "avg_alpha_pct": 0.58},
        db_path=fake_db,
        out_dir=tmp_path / "artifacts",
    )
    a = json.loads(dest.read_text(encoding="utf-8"))
    for key in ("kind", "created_utc", "git", "environment", "data", "config", "result"):
        assert key in a, f"artifact missing {key}"
    assert a["config"]["cost_pct"] == 0.4
    assert a["result"]["trades"] == 754
    assert a["data"]["rows"] == 3
    assert a["environment"]["python"]
    assert "sha" in a["git"] and "dirty" in a["git"]


def test_artifact_git_sha_matches_head(fake_db, tmp_path):
    """A SHA that doesn't match HEAD is provenance theatre."""
    repo = Path(provenance.__file__).resolve().parents[2]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable")
    if head.returncode != 0:
        pytest.skip("not a git repository")

    dest = provenance.write_artifact(
        kind="backtest", config={}, result={}, db_path=fake_db,
        out_dir=tmp_path / "artifacts",
    )
    a = json.loads(dest.read_text(encoding="utf-8"))
    assert a["git"]["sha"] == head.stdout.strip()


def test_each_run_gets_its_own_artifact(fake_db, tmp_path):
    """Artifacts accumulate rather than overwrite — the history of what was run
    is the thing that was missing."""
    out = tmp_path / "artifacts"
    a = provenance.write_artifact("backtest", {}, {"n": 1}, fake_db, out)
    b = provenance.write_artifact("regime", {}, {"n": 2}, fake_db, out)
    assert a != b
    assert len(list(out.glob("*.json"))) == 2


def test_backtest_cli_writes_an_artifact_unconditionally():
    """Structural, not disciplined: there must be no flag that suppresses it."""
    import llmfin.backtest as bt

    src = Path(bt.__file__).read_text(encoding="utf-8")
    assert "write_artifact(" in src, "backtest.main() no longer writes an artifact"
    for flag in ("--no-artifact", "--skip-artifact", "no_artifact"):
        assert flag not in src, f"an artifact opt-out ({flag}) was added"
