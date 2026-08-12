#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

for path in \
  README.md \
  CONTRIBUTING.md \
  .gitignore \
  .github/README.md \
  .github/workflows/skeleton.yml \
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
done < <(find scripts -maxdepth 1 \( -type f -o -type l \) ! -name README.md -print0)

nested=$(find scripts -mindepth 1 -type d -print -quit)
if [[ -n "$nested" ]]; then
  printf 'scripts directory must remain flat\n' >&2
  exit 1
fi

while IFS= read -r -d '' workflow; do
  basename=${workflow##*/}
  grep -Fq "\`$basename\`" .github/README.md || {
    printf 'CI index is missing entry: %s\n' "$basename" >&2
    exit 1
  }
done < <(find .github/workflows -maxdepth 1 \( -type f -o -type l \) -print0)

printf 'skeleton layout and documented boundaries: ok\n'
