#!/usr/bin/env python3
"""Fail-closed scanner for public-boundary validation."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ANSI_CSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
FORBIDDEN = re.compile(
    rb"(github_pat_[a-z0-9_]+|ghp_[a-z0-9]+|sk-[a-z0-9_-]+|"
    rb"AIza[a-z0-9_-]+|AKIA[a-z0-9_]+|"
    rb"(^|[^a-z0-9_])((openai|anthropic|service)[_-]?)?"
    rb"(api[_-]?(key|token)|secret|password|token)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"']+|"
    rb"/(data|home|srv|opt)/|cluster-[0-9]+|"
    rb"([a-z0-9_-]+\.)*(internal|private)(\.[a-z0-9_-]+)+|"
    rb"([a-z0-9_-]+\.)+(internal|local)([/:\s]|$))",
    re.IGNORECASE | re.MULTILINE,
)


def main() -> int:
    if sys.argv[1:] == ["--stdin"]:
        raw = sys.stdin.buffer.read()
    elif len(sys.argv) == 2:
        try:
            raw = Path(sys.argv[1]).read_bytes()
        except OSError:
            return 2
    else:
        return 2
    return 0 if FORBIDDEN.search(ANSI_CSI.sub(b"", raw)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
