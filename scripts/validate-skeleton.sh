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
  tests/README.md \
  tests/test-skeleton.sh; do
  [[ -f "$path" && -s "$path" ]] || {
    printf 'missing or empty required skeleton file: %s\n' "$path" >&2
    exit 1
  }
done

grep -Fqx -- '## Public-ready rule' README.md || {
  printf 'README is missing the public-ready rule\n' >&2
  exit 1
}

grep -Fqx -- 'Every reachable commit must be safe to publish.' README.md || {
  printf 'README is missing the public-ready rule\n' >&2
  exit 1
}

grep -Fqx -- 'Every reachable commit must be safe to publish.' CONTRIBUTING.md || {
  printf 'contributor public-ready rule is missing\n' >&2
  exit 1
}

grep -Fqx -- 'Do not grant dispatch adapters or inference services platform-posting, repository-write, or arbitrary shell authority.' CONTRIBUTING.md || {
  printf 'contributor boundary is missing\n' >&2
  exit 1
}

grep -Fqx -- '5. If you add a component root, document its runtime-state boundary and add matching ignore and regression coverage.' CONTRIBUTING.md || {
  printf 'contributor component-root rule is missing\n' >&2
  exit 1
}

grep -Fqx -- '- Adapters do not post, approve, merge, push, or receive platform credentials.' dispatch/README.md || {
  printf 'dispatch boundary is missing\n' >&2
  exit 1
}

grep -Fqx -- 'A broker does not receive repository-write, shell, GitHub, or posting authority.' inference/README.md || {
  printf 'inference boundary is missing\n' >&2
  exit 1
}

grep -Fqx -- '- `eval/audit/`, `eval/runtime/`, `eval/sockets/`, `eval/logs/`, `eval/weights/`, and `eval/private-overlay` are private operational paths. Public fixture directories such as `eval/fixtures/runtime/`, `eval/fixtures/logs/`, and `eval/fixtures/weights/` remain reviewable when they contain neutral, synthetic files.' eval/README.md || {
  printf 'evaluation runtime boundary is missing\n' >&2
  exit 1
}

grep -Fqx -- '- Keep workflows read-only by default and pin third-party actions to reviewed commit SHAs.' .github/README.md || {
  printf 'workflow documentation is missing read-only rule\n' >&2
  exit 1
}

grep -Fqx -- '- Do not use `pull_request_target` for untrusted pull-request code.' .github/README.md || {
  printf 'workflow documentation is missing pull_request_target rule\n' >&2
  exit 1
}

for rule in \
  '.env' '.env.*' '!.env.example' '.envrc' '.secrets' \
  '*.gguf' '*.safetensors' '*.onnx' '*.pt' '*.ckpt' '*.bin' \
  '*.pem' '*.key' '*.crt' '*.p12' '*.pfx' '*.p8' '*.jks' \
  '*.pth' '*.h5' '*.npz' '*.tflite' \
  'id_rsa' 'id_dsa' 'id_ecdsa' 'id_ed25519' \
  '/eval/audit/' '/eval/runtime/' '/eval/sockets/' '/eval/logs/' '/eval/weights/'; do
  grep -Fqx -- "$rule" .gitignore || {
    printf 'gitignore is missing required rule: %s\n' "$rule" >&2
    exit 1
  }
done

env_pattern_line=$(grep -n -F -x '.env.*' .gitignore | cut -d: -f1)
env_example_line=$(grep -n -F -x '!.env.example' .gitignore | cut -d: -f1)
if (( env_example_line <= env_pattern_line )); then
  printf 'gitignore .env.example exception must follow .env.*\n' >&2
  exit 1
fi

workflow=.github/workflows/skeleton.yml
grep -Fqx -- 'on:' "$workflow" || {
  printf 'workflow is missing the event block\n' >&2
  exit 1
}
if grep -Fq 'pull_request_target:' "$workflow"; then
  printf 'workflow must not use pull_request_target\n' >&2
  exit 1
fi
grep -Fqx -- 'permissions:' "$workflow" || {
  printf 'workflow permissions are not read-only\n' >&2
  exit 1
}
if grep -Eq '^[[:space:]]+if:' "$workflow"; then
  printf 'workflow gate contains a conditional\n' >&2
  exit 1
fi
if grep -Eq '^[[:space:]]+continue-on-error:' "$workflow"; then
  printf 'workflow gate allows continue-on-error\n' >&2
  exit 1
fi

grep -Fqx -- 'jobs:' "$workflow" || {
  printf 'workflow jobs block is missing\n' >&2
  exit 1
}

workflow_section_has_line() {
  local section=$1
  local expected=$2
  local message=$3
  awk -v section="$section" -v expected="$expected" '
    $0 == section { in_section=1; next }
    in_section && $0 ~ /^[^[:space:]][^:]*:/ { in_section=0 }
    in_section && $0 == expected { found=1 }
    END { exit found ? 0 : 1 }
  ' "$workflow" || {
    printf '%s\n' "$message" >&2
    exit 1
  }
}

workflow_permissions_are_read_only() {
  awk '
    $0 == "permissions:" { in_permissions=1; next }
    in_permissions && $0 ~ /^[^[:space:]][^:]*:/ { in_permissions=0 }
    in_permissions && $0 ~ /^  [^[:space:]][^:]*:/ {
      if ($0 == "  contents: read") { found=1 } else { invalid=1 }
    }
    END { exit (found && !invalid) ? 0 : 1 }
  ' "$workflow" || {
    printf 'workflow permissions are not read-only\n' >&2
    exit 1
  }
}

workflow_steps_have_line() {
  local expected=$1
  local message=$2
  awk -v expected="$expected" '
    $0 == "  validate:" { in_validate=1; next }
    in_validate && $0 ~ /^  [^[:space:]][^:]*:/ { in_validate=0; in_steps=0 }
    in_validate && $0 == "    steps:" { in_steps=1; next }
    in_steps && $0 ~ /^    [^[:space:]][^:]*:/ { in_steps=0 }
    in_steps && $0 == expected { found=1 }
    END { exit found ? 0 : 1 }
  ' "$workflow" || {
    printf '%s\n' "$message" >&2
    exit 1
  }
}

workflow_section_has_line 'on:' '  pull_request:' 'workflow does not declare pull_request under on'
workflow_section_has_line 'on:' '  push:' 'workflow does not declare push under on'
workflow_permissions_are_read_only
workflow_section_has_line 'jobs:' '  validate:' 'workflow validate job is missing'
workflow_section_has_line '  validate:' '    runs-on: ubuntu-latest' 'workflow validate job is missing its runner'
workflow_section_has_line '  validate:' '    steps:' 'workflow validate job is missing its steps'
workflow_steps_have_line '      - run: ./scripts/validate-skeleton.sh' 'workflow does not run the skeleton validator'
workflow_steps_have_line '      - run: ./tests/test-skeleton.sh' 'workflow does not run the skeleton test suite'
grep -Fq 'git cat-file -e "$PR_BASE_SHA^{commit}"' "$workflow" || {
  printf 'workflow does not guard missing pull request bases\n' >&2
  exit 1
}
grep -Fq 'git cat-file -e "$EVENT_BEFORE^{commit}"' "$workflow" || {
  printf 'workflow does not guard missing push bases\n' >&2
  exit 1
}
if grep -Fq 'git diff --check --root HEAD' "$workflow"; then
  printf 'workflow fallback uses the ineffective --root form\n' >&2
  exit 1
fi

scripts_index_has_entry() {
  local expected=$1
  awk -F'`' -v expected="$expected" \
    '$0 ~ /^- `[^`]+`:/ && $2 == expected { found=1 } END { exit found ? 0 : 1 }' \
    scripts/README.md
}

while IFS= read -r indexed; do
  [[ -e "scripts/$indexed" || -L "scripts/$indexed" ]] || {
    printf 'scripts index lists missing file: %s\n' "$indexed" >&2
    exit 1
  }
done < <(awk -F'`' '/^- `[^`]+`:/ { print $2 }' scripts/README.md)

while IFS= read -r -d '' script; do
  basename=${script##*/}
  scripts_index_has_entry "$basename" || {
    printf 'scripts index is missing entry: %s\n' "$basename" >&2
    exit 1
  }
done < <(find scripts -maxdepth 1 \( -type f -o -type l \) ! -name README.md -print0)

while IFS= read -r -d '' workflow; do
  basename=${workflow##*/}
  grep -Fq "\`$basename\`" .github/README.md || {
    printf 'CI index is missing entry: %s\n' "$basename" >&2
    exit 1
  }
done < <(find .github/workflows -maxdepth 1 \( -type f -o -type l \) -print0)

if [[ -n "$(find scripts -mindepth 1 -type d -print -quit)" ]]; then
  printf 'scripts directory must remain flat\n' >&2
  exit 1
fi

printf 'skeleton layout and documented boundaries: ok\n'
