#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

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
  scripts/README.md \
  tests/README.md; do
  [[ -f "$path" && -s "$path" ]] || {
    printf 'missing or empty required skeleton file: %s\n' "$path" >&2
    exit 1
  }
done

grep -Fq '## Public-ready rule' README.md || {
  printf 'README is missing the public-ready rule\n' >&2
  exit 1
}

grep -Eq 'does not receive[[:space:]]+repository-write,[[:space:]]+shell,[[:space:]]+GitHub,[[:space:]]+or posting authority' \
  inference/README.md || {
  printf 'inference boundary is missing\n' >&2
  exit 1
}

while IFS= read -r -d '' script; do
  basename=${script##*/}
  grep -Fq "\`$basename\`" scripts/README.md || {
    printf 'scripts index is missing entry: %s\n' "$basename" >&2
    exit 1
  }
done < <(find scripts -maxdepth 1 -type f ! -name README.md -print0)

printf 'skeleton layout and documented boundaries: ok\n'
