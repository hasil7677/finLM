"""
provenance.py
─────────────
Every result-producing run writes a stamped artifact. Non-optional.

Why this exists: on 2026-08-09 the §6 headline backtest figures (935 trades,
52.6% win, +1.2% alpha, PF 1.18) could not be reproduced. Eighteen parameter
combinations were tested and none matched; the entry logic, filter constants and
exit defaults were verified byte-identical to the commit that produced them. The
numbers were not wrong because the code broke - they were unreconstructable
because nothing linked them to the run that produced them. Someone read a number
off a terminal and typed it into a markdown file.

A config dump alone would not have been enough: reconstructing that run also
needs to know *which DB snapshot* it saw. So an artifact stamps the code (git
SHA + dirty flag), the data (row count, date range, size, digest), the
environment (interpreter and library versions), and the full config, alongside
the result.

The point is to make this structural rather than remembered. `write_artifact` is
called by the CLI unconditionally - there is no --no-artifact flag, because the
2am-before-a-deadline version of anyone will use it.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ARTIFACT_DIR = Path(os.getenv("LLMFIN_ARTIFACT_DIR", "artifacts"))


def git_sha() -> dict[str, Any]:
    """Current commit and whether the tree is dirty. A dirty tree means the
    SHA alone does not identify the code that ran - which is exactly the hole
    that produced the unreproducible headline."""
    repo = Path(__file__).resolve().parents[2]

    def _git(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(
                ["git", *args], cwd=repo, capture_output=True, text=True, timeout=10
            )
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    sha = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    return {
        "sha": sha,
        "dirty": bool(status) if status is not None else None,
        "dirty_files": status.splitlines()[:20] if status else [],
    }


def file_digest(path: Path, chunk: int = 1 << 20) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def redact_path(p: Any) -> str:
    """Replace the user's home directory with ~ so artifacts are publishable.

    Artifacts are meant to be committed and cited, so they must not carry the
    machine's username around in absolute paths.
    """
    s = str(p)
    try:
        home = str(Path.home())
        for variant in (home, home.replace("\\", "/")):
            if variant and variant in s:
                s = s.replace(variant, "~")
    except (OSError, RuntimeError):
        pass
    return s.replace("\\", "/")


def db_fingerprint(db_path: Path, table: str = "daily_prices") -> dict[str, Any]:
    """Identify the data snapshot, not just its filename."""
    db_path = Path(db_path)
    fp: dict[str, Any] = {"path": redact_path(db_path), "exists": db_path.exists()}
    if not db_path.exists():
        return fp
    fp["size_bytes"] = db_path.stat().st_size
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows, dmin, dmax = conn.execute(
            f"SELECT COUNT(*), MIN(date), MAX(date) FROM {table}"
        ).fetchone()
        fp.update(rows=rows, date_min=dmin, date_max=dmax)
        fp["distinct_symbols"] = conn.execute(
            f"SELECT COUNT(DISTINCT symbol) FROM {table}"
        ).fetchone()[0]
        conn.close()
    except sqlite3.Error as exc:
        fp["error"] = str(exc)
    fp["sha256"] = file_digest(db_path)
    return fp


def environment() -> dict[str, Any]:
    versions: dict[str, Optional[str]] = {}
    for mod in ("pandas", "numpy"):
        try:
            versions[mod] = __import__(mod).__version__
        except Exception:
            versions[mod] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": versions,
    }


def write_artifact(
    kind: str,
    config: dict[str, Any],
    result: dict[str, Any],
    db_path: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Write a stamped run artifact and return its path.

    Cite this file in docs instead of transcribing numbers out of it.
    """
    out_dir = Path(out_dir or ARTIFACT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    artifact = {
        "kind": kind,
        "created_utc": now.isoformat(timespec="seconds"),
        "git": git_sha(),
        "environment": environment(),
        "data": db_fingerprint(Path(db_path)) if db_path else None,
        "config": config,
        "result": result,
        **(extra or {}),
    }
    dest = out_dir / f"{kind}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = json.dumps(artifact, indent=2, default=str)
    # Belt and braces: redact any home-directory path that reached the config or
    # result dicts by another route (e.g. a caller passing an absolute path).
    try:
        home = str(Path.home())
        for variant in (home, home.replace("\\", "\\\\"), home.replace("\\", "/")):
            if variant:
                payload = payload.replace(variant, "~")
    except (OSError, RuntimeError):
        pass
    dest.write_text(payload, encoding="utf-8")
    return dest
