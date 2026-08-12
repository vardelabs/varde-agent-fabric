#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if ! git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'skeleton test requires a Git worktree\n' >&2
  exit 1
fi

"$root/scripts/validate-skeleton.sh"

expect_ignored() {
  local path=$1
  git -C "$root" check-ignore --no-index -q -- "$path" || {
    printf 'expected ignored path is trackable: %s\n' "$path" >&2
    exit 1
  }
}

expect_trackable() {
  local path=$1
  if git -C "$root" check-ignore --no-index -q -- "$path"; then
    printf 'expected trackable path is ignored: %s\n' "$path" >&2
    exit 1
  fi
}

expect_ignored 'logs/keep.txt'
expect_ignored 'audit/keep.txt'
expect_ignored 'runtime/keep.txt'
expect_ignored 'sockets/keep.txt'
expect_ignored 'weights/keep.txt'
expect_ignored 'private-overlay/keep.yaml'
expect_ignored 'private-overlay.example'
expect_ignored 'dispatch/private-overlay'
expect_ignored 'dispatch/audit/keep.txt'
expect_ignored 'dispatch/runtime/keep.txt'
expect_ignored 'dispatch/sockets/keep.txt'
expect_ignored 'dispatch/private-overlay/keep.txt'
expect_ignored 'inference/audit/keep.txt'
expect_ignored 'inference/runtime/keep.txt'
expect_ignored 'inference/sockets/keep.txt'
expect_ignored 'inference/private-overlay/keep.txt'
expect_ignored 'inference/private-overlay'
expect_ignored 'eval/private-overlay/deploy.yaml'
expect_ignored 'inference/logs/broker.log.1'
expect_ignored 'inference/weights/config.json'
expect_ignored 'eval/fixtures/broker.sock'
expect_ignored 'eval/fixtures/broker.log'
expect_trackable 'docs/audit/threat-model.md'
expect_trackable 'eval/fixtures/runtime/case.json'
expect_trackable 'eval/fixtures/models/config.json'

workflow=$root/.github/workflows/skeleton.yml
grep -Fq 'empty_tree=$(git hash-object -t tree /dev/null)' "$workflow" || {
  printf 'workflow fallback does not compute the empty tree\n' >&2
  exit 1
}
grep -Fq 'git diff --check "$empty_tree" HEAD' "$workflow" || {
  printf 'workflow fallback does not check against the empty tree\n' >&2
  exit 1
}
if grep -Fq 'git diff --check --root HEAD' "$workflow"; then
  printf 'workflow fallback uses the ineffective --root form\n' >&2
  exit 1
fi

archive=$(mktemp)
probe=$(mktemp -d)
whitespace_probe=$(mktemp -d)
trap 'rm -f "$archive"; rm -rf "$probe" "$whitespace_probe"' EXIT
git -C "$whitespace_probe" init -q
git -C "$whitespace_probe" config user.name fixture
git -C "$whitespace_probe" config user.email fixture@example.invalid
printf 'clean\ntrailing \n<<<<<<< HEAD\n' > "$whitespace_probe/file.txt"
git -C "$whitespace_probe" add file.txt
git -C "$whitespace_probe" commit -qm fixture
empty_tree=$(git -C "$whitespace_probe" hash-object -t tree /dev/null)
if output=$(git -C "$whitespace_probe" diff --check "$empty_tree" HEAD 2>&1); then
  printf 'empty-tree whitespace check accepted malformed initial commit\n' >&2
  exit 1
fi
[[ "$output" == *'trailing whitespace'* ]] || {
  printf 'empty-tree whitespace check missed trailing whitespace\n' >&2
  exit 1
}
[[ "$output" == *'leftover conflict marker'* ]] || {
  printf 'empty-tree whitespace check missed conflict marker\n' >&2
  exit 1
}

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

make_probe
(cd "$probe" && ./scripts/validate-skeleton.sh)

for required in \
  README.md CONTRIBUTING.md .gitignore .github/README.md \
  .github/workflows/skeleton.yml dispatch/README.md contracts/README.md \
  inference/README.md inference/grammars/README.md inference/systemd/README.md \
  eval/README.md docs/README.md scripts/README.md tests/README.md; do
  make_probe
  rm "$probe/$required"
  expect_rejection "missing or empty required skeleton file: $required"
done

make_probe
: > "$probe/CONTRIBUTING.md"
expect_rejection 'missing or empty required skeleton file: CONTRIBUTING.md'

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

make_probe
ln -s /bin/echo "$probe/scripts/unindexed.sh"
expect_rejection 'scripts index is missing entry: unindexed.sh'

make_probe
mkdir "$probe/scripts/lib"
printf '#!/usr/bin/env bash\n' > "$probe/scripts/lib/helper.sh"
expect_rejection 'scripts directory must remain flat'

make_probe
printf 'name: unindexed\n' > "$probe/.github/workflows/unindexed.yml"
expect_rejection 'CI index is missing entry: unindexed.yml'

make_probe
ln -s /bin/echo "$probe/.github/workflows/unindexed.yml"
expect_rejection 'CI index is missing entry: unindexed.yml'

printf 'skeleton layout rejection: ok\n'
