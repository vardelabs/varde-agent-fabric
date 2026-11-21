"""Stage the named files for one agy write run, then turn the run into a write proposal.

Usage:
  agy_write.py stage WORKDIR JOB MANIFEST
  agy_write.py result STDOUT CLI_STATUS TIMED_OUT MANIFEST JOB MODEL OUTPUT
  agy_write.py prune HOME STDOUT

The named paths come from DISPATCH_WRITE_PATHS, one a line. `stage` copies each
named file that exists into JOB and records its sha256 in MANIFEST. `result`
reads agy's JSON report and JOB, and writes a write-proposal.v1 to OUTPUT. A
usage limit exits 75, a run stopped at its time limit 124, any other failure 1.
`prune` deletes the conversation the run left in the sign-in HOME, named by the
conversation id in agy's report, and only warns when it cannot.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from contracts.implement_contracts import ContractError
from contracts.write_contracts import MAX_RESPONSE_BYTES, WriteProposal, open_parent, read_named, write_paths

LIMIT_REACHED = 75
OUT_OF_TIME = 124
FAILED = 1
WORKER_KIND = "agy"
MAX_REPORT_BYTES = 1024 * 1024
# Only the CLI's own error field is matched: the response is model text and can mention a quota.
LIMIT_MARKERS = ("quota", "rate limit", "rate-limit", "resource_exhausted", "429", "spend cap", "spend-cap",
                 "prepaid balance")
_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
# agy names each run's entries in its data folder by the run's conversation id.
AGY_DATA_DIR = ".gemini/antigravity-cli"
RUN_ENTRIES = (("brain", ("",)),
               ("conversations", (".db", ".db-wal", ".db-shm", ".db-journal")),
               ("presence", (".lock",)))
CONVERSATION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class RunFailed(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status


def _token(value: object) -> str:
    """A report value safe to print: the CLI's own enums pass, anything else is withheld."""
    text = value.lower() if isinstance(value, str) else ""
    return text if 0 < len(text) <= 64 and set(text) <= _TOKEN_CHARS else "unlisted"


def named_paths() -> tuple[str, ...]:
    lines = [line for line in os.environ.get("DISPATCH_WRITE_PATHS", "").split("\n") if line]
    return write_paths(lines)


def stage(workdir: str, job: str, manifest: str) -> None:
    baseline: dict[str, str | None] = {}
    for path in named_paths():
        data = read_named(workdir, path)
        baseline[path] = None if data is None else hashlib.sha256(data).hexdigest()
        if data is not None:
            target = Path(job, path)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(data)
    Path(manifest).write_text(json.dumps(baseline), encoding="utf-8")


def _report(stdout_path: str) -> dict:
    with open(stdout_path, "rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise RunFailed(FAILED, "agy report is over 1 MiB")
    try:
        report = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise RunFailed(FAILED, "agy wrote no JSON report") from exc
    if not isinstance(report, dict):
        raise RunFailed(FAILED, "agy report is malformed")
    return report


def check_finished(report: dict, cli_status: int) -> None:
    error = report.get("error")
    error_text = error.lower() if isinstance(error, str) else ""
    if error_text and any(marker in error_text for marker in LIMIT_MARKERS):
        raise RunFailed(LIMIT_REACHED, "usage limit reached")
    if report.get("status") != "SUCCESS" or error_text or cli_status != 0:
        raise RunFailed(FAILED, f"agy exited with status {cli_status}: status={_token(report.get('status'))} "
                                f"finished_reason={_token(report.get('finished_reason'))}")
    denied = report.get("denied_actions")
    if denied:
        count = len(denied) if isinstance(denied, list) else 1
        raise RunFailed(FAILED, f"agy tried {count} action(s) its policy denies; the proposal is discarded")


def stray_entries(job: str, paths: tuple[str, ...]) -> list[str]:
    """Everything in the job folder that is not a named file or a folder on the way to one."""
    allowed_dirs = {"/".join(path.split("/")[:index]) for path in paths for index in range(1, path.count("/") + 1)}
    stray = []
    for folder, dirs, files in os.walk(job):
        relative = os.path.relpath(folder, job)
        prefix = "" if relative == "." else relative + "/"
        for name in dirs:
            entry = prefix + name
            if os.path.islink(os.path.join(folder, name)) or entry not in allowed_dirs:
                stray.append(entry)
        dirs[:] = [name for name in dirs if prefix + name in allowed_dirs]
        stray.extend(prefix + name for name in files if prefix + name not in paths)
    return stray


def collect_changes(job: str, paths: tuple[str, ...], baseline: dict[str, str | None]) -> list[dict]:
    changes = []
    for path in paths:
        try:
            data = read_named(job, path)
        except (OSError, UnicodeError, ContractError) as exc:
            raise RunFailed(FAILED, "agy left a named path that is not a bounded UTF-8 regular file") from exc
        after = None if data is None else hashlib.sha256(data).hexdigest()
        if after != baseline[path]:
            changes.append({"path": path, "before_sha256": baseline[path],
                            "after_text": None if data is None else data.decode("utf-8")})
    return changes


def _bounded_response(report: dict) -> str:
    response = report.get("response")
    text = response if isinstance(response, str) else ""
    return text.encode("utf-8")[:MAX_RESPONSE_BYTES].decode("utf-8", errors="ignore").replace("\x00", "")


def result(stdout_path: str, cli_status: int, timed_out: bool, manifest: str, job: str, model: str) -> dict:
    if timed_out:
        raise RunFailed(OUT_OF_TIME, "agy ran out of time")
    report = _report(stdout_path)
    check_finished(report, cli_status)
    paths = named_paths()
    stray = stray_entries(job, paths)
    if stray:
        raise RunFailed(FAILED, f"agy left {len(stray)} entr(ies) outside the named paths; the proposal is discarded")
    baseline = json.loads(Path(manifest).read_text(encoding="utf-8"))
    proposal = WriteProposal(WORKER_KIND, model, paths, tuple(collect_changes(job, paths, baseline)),
                             _bounded_response(report))
    return proposal.to_dict()


def _run_conversation_id(stdout_path: str) -> str | None:
    """The run's conversation id from agy's report, or None when the report names no well-formed one."""
    try:
        report = _report(stdout_path)
    except (OSError, RunFailed):
        return None
    conversation_id = report.get("conversation_id")
    if isinstance(conversation_id, str) and CONVERSATION_ID.fullmatch(conversation_id):
        return conversation_id
    return None


def _open_folder(parent_fd: int, name: str) -> int | None:
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return None


def _remove_entry(parent_fd: int, name: str) -> None:
    """Remove a file, a link, or a whole folder without following any link inside it."""
    try:
        folder_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno not in (errno.ELOOP, errno.ENOTDIR):
            raise
        os.unlink(name, dir_fd=parent_fd)
        return
    try:
        for child in list(os.scandir(folder_fd)):
            _remove_entry(folder_fd, child.name)
    finally:
        os.close(folder_fd)
    os.rmdir(name, dir_fd=parent_fd)


def prune(home: str, stdout_path: str) -> None:
    """Delete only this run's conversation from the sign-in home, so repeated runs do not fill it.

    Other conversations are never touched: the entries are named by the id the run's own report gives.
    """
    conversation_id = _run_conversation_id(stdout_path)
    if conversation_id is None:
        return
    home_fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        data_fd = open_parent(home_fd, AGY_DATA_DIR + "/")
        if data_fd is None:
            return
        try:
            for folder, suffixes in RUN_ENTRIES:
                folder_fd = _open_folder(data_fd, folder)
                if folder_fd is None:
                    continue
                try:
                    for suffix in suffixes:
                        _remove_entry(folder_fd, conversation_id + suffix)
                finally:
                    os.close(folder_fd)
        finally:
            os.close(data_fd)
    finally:
        os.close(home_fd)


def write_private(output: str, text: str) -> None:
    """The proposal holds private file text, so it is owner-only whatever the caller's umask."""
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(fd, 0o600)
        stream.write(text)


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["stage"] and len(argv) == 4:
            stage(*argv[1:])
            return 0
        if argv[:1] == ["result"] and len(argv) == 8:
            stdout_path, status, timed_out, manifest, job, model, output = argv[1:]
            proposal = result(stdout_path, int(status), timed_out == "1", manifest, job, model)
            write_private(output, json.dumps(proposal, ensure_ascii=False))
            return 0
        if argv[:1] == ["prune"] and len(argv) == 3:
            try:
                prune(*argv[1:])
            except OSError as exc:
                # The proposal stands either way; a leftover conversation costs only disk.
                print(f"agy: could not delete this run's conversation: {exc.strerror}", file=sys.stderr)
            return 0
    except RunFailed as exc:
        print(f"agy: {exc}", file=sys.stderr)
        return exc.status
    except (OSError, UnicodeError, ContractError) as exc:
        print(f"agy: {exc}", file=sys.stderr)
        return FAILED
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
