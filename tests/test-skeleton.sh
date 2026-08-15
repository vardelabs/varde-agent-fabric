#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
script_path=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")

if ! git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'skeleton test requires a Git worktree\n' >&2
  exit 1
fi

if [[ ${SKELETON_TEST_CONFIG_INJECTION:-0} != 1 ]]; then
  if ! injected_output=$(env \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0=core.excludesFile \
    GIT_CONFIG_VALUE_0=/dev/null \
    SKELETON_TEST_CONFIG_INJECTION=1 \
    "$script_path" 2>&1); then
    printf '%s\n' "$injected_output" >&2
    printf 'skeleton test is not isolated from injected Git config\n' >&2
    exit 1
  fi
fi

"$root/scripts/validate-skeleton.sh"

git_hermetic() {
  env \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_SYSTEM=/dev/null \
    GIT_CONFIG_COUNT=0 \
    git "$@"
}

archive=$(mktemp)
probe=$(mktemp -d)
whitespace_probe=$(mktemp -d)
ignore_probe=$(mktemp -d)
cleanup() {
  rm -f "$archive"
  rm -rf "$probe" "$whitespace_probe" "$ignore_probe"
}
trap cleanup EXIT

git_hermetic -c init.templateDir= init -q "$ignore_probe"
cp "$root/.gitignore" "$ignore_probe/.gitignore"
mkdir -p "$ignore_probe/.git/info"
printf 'docs/audit/info-probe.md\n' > "$ignore_probe/.git/info/exclude"
if ! git_hermetic -C "$ignore_probe" check-ignore --no-index -q -- docs/audit/info-probe.md; then
  printf 'local Git info exclude probe did not mask its path\n' >&2
  exit 1
fi
: > "$ignore_probe/.git/info/exclude"
if git_hermetic -C "$ignore_probe" -c core.excludesFile=/dev/null check-ignore --no-index -q -- docs/audit/info-probe.md; then
  printf 'local Git info exclude was not cleared before repository probes\n' >&2
  exit 1
fi

personal_ignore="$ignore_probe/personal-ignore"
personal_config="$ignore_probe/personal.gitconfig"
printf 'docs/personal-global-probe.txt\nlogs/keep.txt\ndocs/audit/threat-model.md\n' > "$personal_ignore"
printf '[core]\n\texcludesFile = %s\n' "$personal_ignore" > "$personal_config"
if ! GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL="$personal_config" GIT_CONFIG_COUNT=0 \
   git -C "$ignore_probe" check-ignore --no-index -q -- docs/personal-global-probe.txt; then
  printf 'global Git excludesFile probe did not mask its path\n' >&2
  exit 1
fi

expect_ignored() {
  local path=$1
  git_hermetic -C "$ignore_probe" check-ignore --no-index -q -- "$path" || {
    printf 'expected ignored path is trackable: %s\n' "$path" >&2
    exit 1
  }
}

expect_trackable() {
  local path=$1
  if git_hermetic -C "$ignore_probe" check-ignore --no-index -q -- "$path"; then
    printf 'expected trackable path is ignored: %s\n' "$path" >&2
    exit 1
  fi
}

expect_ignored 'logs/keep.txt'
expect_ignored 'models/keep.txt'
expect_ignored 'audit/keep.txt'
expect_ignored 'runtime/keep.txt'
expect_ignored 'sockets/keep.txt'
expect_ignored 'weights/keep.txt'
expect_ignored 'private-overlay/keep.yaml'
expect_ignored 'private-overlay.example'
expect_ignored '.env'
expect_ignored '.env.local'
expect_ignored 'inference/.env'
expect_ignored '.envrc'
expect_ignored '.secrets'
expect_ignored 'id_rsa'
expect_ignored 'dispatch/id_ecdsa'
expect_ignored 'inference/deploy.p8'
expect_ignored 'inference/server.jks'
expect_ignored 'inference/models/model.pth'
expect_ignored 'inference/models/model.h5'
expect_ignored 'inference/models/model.npz'
expect_ignored 'inference/models/model.tflite'
expect_trackable '.env.example'
expect_trackable 'docs/.env.example'
expect_ignored 'dispatch/private-overlay'
expect_ignored 'dispatch/private-overlay.yaml'
expect_ignored 'dispatch/audit/keep.txt'
expect_ignored 'dispatch/runtime/keep.txt'
expect_ignored 'dispatch/sockets/keep.txt'
expect_ignored 'dispatch/logs/keep.txt'
expect_ignored 'dispatch/weights/keep.txt'
expect_ignored 'dispatch/private-overlay/keep.txt'
expect_ignored 'inference/audit/keep.txt'
expect_ignored 'inference/runtime/keep.txt'
expect_ignored 'inference/sockets/keep.txt'
expect_ignored 'inference/logs/keep.txt'
expect_ignored 'inference/weights/keep.txt'
expect_ignored 'inference/private-overlay/keep.txt'
expect_ignored 'inference/private-overlay'
expect_ignored 'inference/private-overlay.yaml'
expect_ignored 'eval/private-overlay'
expect_ignored 'eval/private-overlay/deploy.yaml'
expect_ignored 'eval/private-overlay.yaml'
expect_ignored 'eval/audit/run.json'
expect_ignored 'eval/runtime/state.json'
expect_ignored 'eval/sockets/broker.sock'
expect_ignored 'eval/logs/run.log'
expect_ignored 'eval/weights/model.pth'
expect_ignored 'inference/logs/broker.log.1'
expect_ignored 'inference/weights/config.json'
expect_ignored 'eval/fixtures/broker.sock'
expect_ignored 'eval/fixtures/broker.log'
expect_ignored 'eval/fixtures/synthetic.bin'
expect_ignored 'eval/fixtures/synthetic.gguf'
expect_ignored 'eval/fixtures/synthetic.pem'
expect_ignored 'eval/fixtures/synthetic.key'
expect_trackable 'docs/audit/threat-model.md'
expect_trackable 'docs/audit/info-probe.md'
expect_trackable 'docs/logs/design.md'
expect_trackable 'docs/weights/rationale.md'
expect_trackable 'contracts/models/schema.json'
expect_trackable 'contracts/weights/schema.json'
expect_trackable 'eval/fixtures/runtime/case.json'
expect_trackable 'eval/fixtures/logs/case.json'
expect_trackable 'eval/fixtures/weights/tiny.json'
expect_trackable 'tests/fixtures/logs/sample.txt'
expect_trackable 'eval/fixtures/models/config.json'

workflow=$root/.github/workflows/skeleton.yml
grep -Fq '      - run: ./scripts/validate-skeleton.sh' "$workflow" || {
  printf 'workflow does not run the skeleton validator\n' >&2
  exit 1
}
grep -Fq '      - run: ./tests/test-skeleton.sh' "$workflow" || {
  printf 'workflow does not run the skeleton test suite\n' >&2
  exit 1
}
grep -Fq 'empty_tree=$(git hash-object -t tree /dev/null)' "$workflow" || {
  printf 'workflow fallback does not compute the empty tree\n' >&2
  exit 1
}
grep -Fq 'git diff --check "$empty_tree" HEAD' "$workflow" || {
  printf 'workflow fallback does not check against the empty tree\n' >&2
  exit 1
}
grep -Fq 'git diff --check "$PR_BASE_SHA" HEAD' "$workflow" || {
  printf 'workflow does not check pull request changes\n' >&2
  exit 1
}
grep -Fq 'git diff --check "$EVENT_BEFORE" HEAD' "$workflow" || {
  printf 'workflow does not check push changes\n' >&2
  exit 1
}
grep -Fq 'git cat-file -e "$EVENT_BEFORE^{commit}"' "$workflow" || {
  printf 'workflow does not guard missing push bases\n' >&2
  exit 1
}
grep -Fq 'git cat-file -e "$PR_BASE_SHA^{commit}"' "$workflow" || {
  printf 'workflow does not guard missing pull request bases\n' >&2
  exit 1
}
if grep -Fq 'git diff --check --root HEAD' "$workflow"; then
  printf 'workflow fallback uses the ineffective --root form\n' >&2
  exit 1
fi

git_hermetic -c init.templateDir= -C "$whitespace_probe" init -q
git_hermetic -C "$whitespace_probe" config user.name fixture
git_hermetic -C "$whitespace_probe" config user.email fixture@example.invalid
hooks="$whitespace_probe/hooks"
mkdir -p "$hooks"
printf '#!/usr/bin/env bash\nexit 1\n' > "$hooks/pre-commit"
chmod +x "$hooks/pre-commit"
git_hermetic -C "$whitespace_probe" config commit.gpgsign true
git_hermetic -C "$whitespace_probe" config core.hooksPath "$hooks"
printf 'clean\ntrailing \n<<<<<<< HEAD\n' > "$whitespace_probe/file.txt"
git_hermetic -C "$whitespace_probe" add file.txt
git_hermetic -C "$whitespace_probe" -c commit.gpgsign=false -c core.hooksPath=/dev/null commit -qm fixture
empty_tree=$(git_hermetic -C "$whitespace_probe" hash-object -t tree /dev/null)
if output=$(git_hermetic -C "$whitespace_probe" diff --check "$empty_tree" HEAD 2>&1); then
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

missing_before=1111111111111111111111111111111111111111
if [[ -n "$missing_before" && "$missing_before" != "0000000000000000000000000000000000000000" ]] && \
   git_hermetic -C "$whitespace_probe" cat-file -e "$missing_before^{commit}" 2>/dev/null; then
  printf 'missing push base was incorrectly treated as present\n' >&2
  exit 1
else
  empty_tree=$(git_hermetic -C "$whitespace_probe" hash-object -t tree /dev/null)
  if output=$(git_hermetic -C "$whitespace_probe" diff --check "$empty_tree" HEAD 2>&1); then
    printf 'missing push base did not fall back to the empty tree\n' >&2
    exit 1
  fi
fi

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

expect_gitignore_rule_rejection() {
  local rule=$1
  make_probe
  python3 - "$probe/.gitignore" "$rule" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
rule = sys.argv[2]
lines = p.read_text().splitlines()
lines.remove(rule)
p.write_text('\n'.join(lines) + '\n')
PY
  expect_rejection "gitignore is missing required rule: $rule"
}

make_probe
(cd "$probe" && ./scripts/validate-skeleton.sh)

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('      - run: ./scripts/validate-skeleton.sh\n', ''))
PY
expect_rejection 'workflow does not run the skeleton validator'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('      - run: ./tests/test-skeleton.sh\n', ''))
PY
expect_rejection 'workflow does not run the skeleton test suite'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('git cat-file -e "$EVENT_BEFORE^{commit}"', 'git cat-file -e "$EVENT_BEFORE^{missing}"'))
PY
expect_rejection 'workflow does not guard missing push bases'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('  pull_request:\n', ''))
PY
expect_rejection 'workflow does not declare pull_request under on'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('  push:\n', ''))
PY
expect_rejection 'workflow does not declare push under on'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('  contents: read\n', ''))
PY
expect_rejection 'workflow permissions are not read-only'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    '    runs-on: ubuntu-latest\n',
    '    runs-on: ubuntu-latest\n    permissions:\n      contents: write\n      actions: write\n',
    1,
))
PY
expect_rejection 'workflow contains a job-level permissions block'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
if 'on:\n' not in text or '  pull_request:\n' not in text:
    raise SystemExit('workflow trigger fixture did not match')
text = text.replace('on:\n', 'on:\n  issues:\n', 1)
text = text.replace('  pull_request:\n', 'unrelated:\n  pull_request:\n', 1)
p.write_text(text)
PY
expect_rejection 'workflow does not declare pull_request under on'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
old = '  pull_request:\n'
new = "  pull_request:\n    paths:\n      - '**/never-match'\n"
if old not in text:
    raise SystemExit('workflow filter fixture did not match')
p.write_text(text.replace(old, new, 1))
PY
expect_rejection 'workflow pull_request trigger must not have filters'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
old = 'permissions:\n  contents: read\n\non:'
new = 'permissions:\n  actions: write\nunrelated:\n  contents: read\n\non:'
if old not in text:
    raise SystemExit('workflow permissions fixture did not match')
p.write_text(text.replace(old, new, 1))
PY
expect_rejection 'workflow permissions are not read-only'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('    steps:\n', '    env:\n', 1))
PY
expect_rejection 'workflow validate job is missing its steps'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('  pull_request:\n', '  pull_request_target:\n'))
PY
expect_rejection 'workflow must not use pull_request_target'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('  validate:\n', '  validate:\n    if: false\n'))
PY
expect_rejection 'workflow gate contains a conditional'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    '  validate:\n',
    '  validate:\n    if : false\n',
    1,
))
PY
expect_rejection 'workflow gate contains a conditional'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    '      - run: ./tests/test-skeleton.sh\n',
    '      - run: ./tests/test-skeleton.sh\n        continue-on-error: true\n',
))
PY
expect_rejection 'workflow gate allows continue-on-error'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    '      - run: ./tests/test-skeleton.sh\n',
    '      - run: ./tests/test-skeleton.sh\n        continue-on-error : true\n',
    1,
))
PY
expect_rejection 'workflow gate allows continue-on-error'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('  validate:\n', '  other:\n'))
PY
expect_rejection 'workflow validate job is missing'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('git cat-file -e "$EVENT_BEFORE^{commit}"', 'git cat-file -e "$EVENT_BEFORE^{missing}"'))
PY
expect_rejection 'workflow does not guard missing push bases'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('git cat-file -e "$PR_BASE_SHA^{commit}"', 'git cat-file -e "$PR_BASE_SHA^{missing}"'))
PY
expect_rejection 'workflow does not guard missing pull request bases'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('git diff --check "$PR_BASE_SHA" HEAD', 'git diff --check', 1))
PY
expect_rejection 'workflow does not check pull request changes'

make_probe
python3 - "$probe/.github/workflows/skeleton.yml" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace('git diff --check "$EVENT_BEFORE" HEAD', 'git diff --check', 1))
PY
expect_rejection 'workflow does not check push changes'

make_probe
python3 - "$probe/.github/README.md" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    'Keep workflows read-only by default and pin third-party actions to reviewed commit SHAs.',
    'Workflows may request broad permissions.',
))
PY
expect_rejection 'workflow documentation is missing read-only rule'

make_probe
python3 - "$probe/.github/README.md" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    'Do not use `pull_request_target` for untrusted pull-request code.',
    'Use any pull request trigger.',
))
PY
expect_rejection 'workflow documentation is missing pull_request_target rule'

for rule in \
  '.env' '.env.*' '!.env.example' '.envrc' '.secrets' \
  'id_rsa' 'id_dsa' 'id_ecdsa' 'id_ed25519' '*.p8' '*.jks' \
  '*.pth' '*.h5' '*.npz' '*.tflite' \
  '/eval/audit/' '/eval/runtime/' '/eval/sockets/' '/eval/logs/' '/eval/weights/'; do
  expect_gitignore_rule_rejection "$rule"
done

make_probe
python3 - "$probe/.gitignore" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    '.env\n.env.*\n!.env.example\n',
    '.env\n!.env.example\n.env.*\n',
))
PY
expect_rejection 'gitignore .env.example exception must follow .env.*'
for required in \
  README.md CONTRIBUTING.md .gitignore .github/README.md \
  .github/workflows/skeleton.yml dispatch/README.md contracts/README.md \
  inference/README.md inference/grammars/README.md inference/systemd/README.md \
  eval/README.md docs/README.md scripts/README.md tests/README.md \
  tests/test-skeleton.sh; do
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
python3 - "$probe/README.md" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    'Every reachable commit must be safe to publish.',
    'Every commit may contain private material.',
))
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
python3 - "$probe/CONTRIBUTING.md" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    'Do not grant dispatch adapters or inference services platform-posting, repository-write, or arbitrary shell authority.',
    'Adapters may hold platform authority.',
))
PY
expect_rejection 'contributor boundary is missing'

make_probe
python3 - "$probe/CONTRIBUTING.md" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    'If you add a component root, document its runtime-state boundary and add matching ignore and regression coverage.',
    'New roots need no runtime policy.',
))
PY
expect_rejection 'contributor component-root rule is missing'

make_probe
python3 - "$probe/dispatch/README.md" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    'Adapters do not post, approve, merge, push, or receive platform credentials.',
    'Adapters may post and push.',
))
PY
expect_rejection 'dispatch boundary is missing'

make_probe
python3 - "$probe/eval/README.md" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
p.write_text(p.read_text().replace(
    '- `eval/audit/`, `eval/runtime/`, `eval/sockets/`, `eval/logs/`, `eval/weights/`, and `eval/private-overlay` are private operational paths. Public fixture directories such as `eval/fixtures/runtime/`, `eval/fixtures/logs/`, and `eval/fixtures/weights/` remain reviewable when they contain neutral, synthetic files.\n',
    '- Evaluation runtime paths are private.\n',
))
PY
expect_rejection 'evaluation runtime boundary is missing'

make_probe
printf '#!/usr/bin/env python3\n' > "$probe/scripts/unindexed.py"
expect_rejection 'scripts index is missing entry: unindexed.py'

make_probe
ln -s /bin/echo "$probe/scripts/unindexed.sh"
expect_rejection 'scripts index is missing entry: unindexed.sh'

make_probe
printf '\n- `removed.sh`: stale index entry.\n' >> "$probe/scripts/README.md"
expect_rejection 'scripts index lists missing file: removed.sh'

make_probe
python3 - "$probe/scripts/README.md" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
old = '- `validate-skeleton.sh`: verifies the required skeleton files, boundary markers, required ignore rules, workflow safety invariants, and script indexes. It exits zero only when the checked layout and policy markers are present. It is not a content, secret, or history scanner.\n'
new = 'The validator is `validate-skeleton.sh`, but it is not indexed.\n'
if old not in text:
    raise SystemExit('script index fixture did not match')
p.write_text(text.replace(old, new, 1))
PY
expect_rejection 'scripts index is missing entry: validate-skeleton.sh'

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
