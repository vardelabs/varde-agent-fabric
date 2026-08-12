#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if ! git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'skeleton test requires a Git worktree\n' >&2
  exit 1
fi

"$root/scripts/validate-skeleton.sh"

archive=$(mktemp)
probe=$(mktemp -d)
trap 'rm -f "$archive"; rm -rf "$probe"' EXIT
(
  cd "$root"
  git ls-files -z | tar --null -T - -cf "$archive"
)

make_probe() {
  rm -rf "$probe"
  mkdir -p "$probe"
  tar -xf "$archive" -C "$probe"
}

expect_rejection() {
  local expected=$1 output
  if output=$(cd "$probe" && ./scripts/validate-skeleton.sh 2>&1); then
    printf 'validator accepted malformed skeleton\n' >&2
    exit 1
  fi
  case "$output" in
    *"$expected"*) ;;
    *) printf 'validator rejected probe for the wrong reason\n' >&2; exit 1 ;;
  esac
}

for required in \
  README.md CONTRIBUTING.md .gitignore \
  dispatch/README.md contracts/README.md inference/README.md \
  inference/grammars/README.md inference/systemd/README.md \
  eval/README.md docs/README.md scripts/README.md tests/README.md; do
  make_probe
  rm "$probe/$required"
  expect_rejection "missing or empty required skeleton file: $required"
done

make_probe
rm "$probe/docs/README.md"
mkdir "$probe/docs/README.md"
expect_rejection 'missing or empty required skeleton file: docs/README.md'

make_probe
python3 - "$probe/README.md" <<'PY'
from pathlib import Path
p = Path(__import__('sys').argv[1])
p.write_text(p.read_text().replace('## Public-ready rule', '## Publication rule'))
PY
expect_rejection 'README is missing the public-ready rule'

make_probe
python3 - "$probe/inference/README.md" <<'PY'
from pathlib import Path
p = Path(__import__('sys').argv[1])
p.write_text(p.read_text().replace(
    'does not receive repository-write, shell, GitHub, or posting authority',
    'receives repository-write, shell, GitHub, or posting authority',
))
PY
expect_rejection 'inference boundary is missing'

make_probe
printf '#!/usr/bin/env python3\n' > "$probe/scripts/unindexed.py"
expect_rejection 'scripts index is missing entry: unindexed.py'

printf 'skeleton layout rejection: ok\n'
