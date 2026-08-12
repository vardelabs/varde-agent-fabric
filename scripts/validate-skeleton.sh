#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  printf 'validation requires a Git worktree\n' >&2
  exit 1
}

for path in \
  README.md \
  CONTRIBUTING.md \
  .gitignore \
  dispatch/README.md \
  contracts/README.md \
  inference/README.md \
  inference/grammars/README.md \
  inference/systemd/README.md \
  eval/README.md \
  docs/README.md \
  tests/README.md; do
  [[ -f "$path" ]] || {
    printf 'missing required skeleton file: %s\n' "$path" >&2
    exit 1
  }
done

grep -Fq 'Public-ready rule' README.md || {
  printf 'README is missing the public-ready rule\n' >&2
  exit 1
}

grep -Fq 'does not receive repository-write, shell, GitHub, or posting authority' \
  inference/README.md || {
  printf 'inference boundary is missing\n' >&2
  exit 1
}

printf 'skeleton layout and documented boundaries: ok\n'
