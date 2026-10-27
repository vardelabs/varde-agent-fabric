"""Give one OpenCode run a private copy of a sign-in database, and keep its token.

Usage:
  opencode_signin.py snapshot SIGNIN_DB RUN_DB
  opencode_signin.py writeback RUN_DB SIGNIN_DB INTEGRATION

OpenCode keeps sign-ins in the same SQLite database as its sessions, and a
session holds the reviewed diff. So each run works on a copy, and only a
refreshed credential value flows back: never a session, and never a credential
the sign-in database does not already hold.
"""
from __future__ import annotations

import os
import sqlite3
import sys

BUSY_TIMEOUT_SECONDS = 30


def _connect(path: str, *, read_only: bool) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'ro' if read_only else 'rw'}"
    return sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_SECONDS)


def snapshot(signin_db: str, run_db: str) -> None:
    """A consistent copy, made by SQLite itself, so a write in progress is never half copied."""
    source = _connect(signin_db, read_only=True)
    descriptor = os.open(run_db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    target = sqlite3.connect(run_db)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def writeback(run_db: str, signin_db: str, integration: str) -> int:
    """Copy each changed credential value of the integration back; return how many changed."""
    source = _connect(run_db, read_only=True)
    try:
        rows = source.execute(
            "SELECT id, value FROM credential WHERE integration_id = ?", (integration,)).fetchall()
    finally:
        source.close()
    target = _connect(signin_db, read_only=False)
    try:
        with target:
            changed = sum(
                target.execute("UPDATE credential SET value = ? WHERE id = ? AND integration_id = ? AND value <> ?",
                               (value, credential_id, integration, value)).rowcount
                for credential_id, value in rows)
    finally:
        target.close()
    return changed


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "snapshot":
        snapshot(argv[1], argv[2])
        return 0
    if len(argv) == 4 and argv[0] == "writeback":
        writeback(argv[1], argv[2], argv[3])
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
