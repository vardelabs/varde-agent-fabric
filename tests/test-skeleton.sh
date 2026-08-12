#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
"$root/scripts/validate-skeleton.sh"

archive=$(mktemp)
probe=$(mktemp -d)
trap 'rm -f "$archive"; rm -rf "$probe"' EXIT
git -C "$root" archive --format=tar HEAD > "$archive"

make_probe() {
  rm -rf "$probe"
  mkdir -p "$probe"
  tar -xf "$archive" -C "$probe"
  cp "$root/scripts/validate-skeleton.sh" "$probe/scripts/validate-skeleton.sh"
  cp "$root/tests/test-skeleton.sh" "$probe/tests/test-skeleton.sh"
  cp "$root/README.md" "$probe/README.md"
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
p.write_text(p.read_text().replace('repository-write, shell, GitHub, or posting authority', 'repository write authority'))
PY
expect_rejection 'inference boundary is missing'

make_probe
printf '#!/usr/bin/env bash\n' > "$probe/scripts/unindexed.sh"
chmod +x "$probe/scripts/unindexed.sh"
expect_rejection 'scripts index is missing entry: unindexed.sh'

printf 'skeleton layout rejection: ok\n'
