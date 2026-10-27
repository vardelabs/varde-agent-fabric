"""Give one OpenCode run a private copy of a sign-in database, and keep its token.

Usage:
  opencode_signin.py snapshot SIGNIN_DB RUN_DB VALUES_FILE INTEGRATION
  opencode_signin.py writeback RUN_DB SIGNIN_DB VALUES_FILE INTEGRATION

OpenCode keeps sign-ins in the same SQLite database as its sessions, and a
session holds the reviewed diff. So each run works on a copy, and only a
credential value the run itself refreshed flows back: never a session, a new
credential, or another provider's. A value is written back only while the
sign-in database still holds what the run started from, so a run that started
earlier never overwrites a token another run refreshed since.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

BUSY_TIMEOUT_SECONDS = 30


def _connect(path: str, *, read_only: bool) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'ro' if read_only else 'rw'}"
    return sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_SECONDS)


def _values(connection: sqlite3.Connection, integration: str) -> dict[str, str]:
    rows = connection.execute("SELECT id, value FROM credential WHERE integration_id = ?", (integration,))
    return dict(rows.fetchall())


def _create_private(path: str) -> None:
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))


def snapshot(signin_db: str, run_db: str, values_file: str, integration: str) -> None:
    """A consistent copy, made by SQLite itself, and the credential values it started from."""
    source = _connect(signin_db, read_only=True)
    _create_private(run_db)
    target = sqlite3.connect(run_db)
    try:
        source.backup(target)
        started_from = _values(target, integration)
    finally:
        target.close()
        source.close()
    _create_private(values_file)
    with open(values_file, "w", encoding="utf-8") as stream:
        json.dump(started_from, stream)


def writeback(run_db: str, signin_db: str, values_file: str, integration: str) -> int:
    """Copy back each credential value the run changed; return how many were written."""
    with open(values_file, encoding="utf-8") as stream:
        started_from = json.load(stream)
    source = _connect(run_db, read_only=True)
    try:
        refreshed = {credential_id: value for credential_id, value in _values(source, integration).items()
                     if credential_id in started_from and value != started_from[credential_id]}
    finally:
        source.close()
    target = _connect(signin_db, read_only=False)
    try:
        with target:
            return sum(
                target.execute("UPDATE credential SET value = ? WHERE id = ? AND integration_id = ? AND value = ?",
                               (value, credential_id, integration, started_from[credential_id])).rowcount
                for credential_id, value in refreshed.items())
    finally:
        target.close()


def main(argv: list[str]) -> int:
    if len(argv) == 5 and argv[0] == "snapshot":
        snapshot(*argv[1:])
        return 0
    if len(argv) == 5 and argv[0] == "writeback":
        writeback(*argv[1:])
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
