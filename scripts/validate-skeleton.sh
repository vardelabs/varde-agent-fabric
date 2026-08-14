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

grep -Fq '## Public-ready rule' README.md || {
  printf 'README is missing the public-ready rule\n' >&2
  exit 1
}

grep -Fq 'Every reachable commit must be safe to publish.' README.md || {
  printf 'README is missing the public-ready rule\n' >&2
  exit 1
}

grep -Fq 'Do not grant dispatch adapters or inference services platform-posting, repository-write, or arbitrary shell authority.' CONTRIBUTING.md || {
  printf 'contributor boundary is missing\n' >&2
  exit 1
}

grep -Fq 'If you add a component root, document its runtime-state boundary and add matching ignore and regression coverage.' CONTRIBUTING.md || {
  printf 'contributor component-root rule is missing\n' >&2
  exit 1
}

grep -Fq 'Adapters do not post, approve, merge, push, or receive platform credentials.' dispatch/README.md || {
  printf 'dispatch boundary is missing\n' >&2
  exit 1
}

grep -Eq 'does not receive[[:space:]]+repository-write,[[:space:]]+shell,[[:space:]]+GitHub,[[:space:]]+or posting authority' \
  inference/README.md || {
  printf 'inference boundary is missing\n' >&2
  exit 1
}

grep -Fq 'Keep workflows read-only by default and pin third-party actions to reviewed commit SHAs.' .github/README.md || {
  printf 'workflow documentation is missing read-only rule\n' >&2
  exit 1
}

grep -Fq 'Do not use `pull_request_target` for untrusted pull-request code.' .github/README.md || {
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
grep -Fqx 'on:' "$workflow" || {
  printf 'workflow is missing the event block\n' >&2
  exit 1
}
if grep -Fq 'pull_request_target:' "$workflow"; then
  printf 'workflow must not use pull_request_target\n' >&2
  exit 1
fi
grep -Fqx '  pull_request:' "$workflow" || {
  printf 'workflow is missing pull_request trigger\n' >&2
  exit 1
}
grep -Fqx '  push:' "$workflow" || {
  printf 'workflow is missing push trigger\n' >&2
  exit 1
}
grep -Fqx 'permissions:' "$workflow" || {
  printf 'workflow permissions are not read-only\n' >&2
  exit 1
}
grep -Fqx '  contents: read' "$workflow" || {
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
grep -Fqx '  validate:' "$workflow" || {
  printf 'workflow validate job is missing\n' >&2
  exit 1
}

workflow_validate_has_line() {
  local expected=$1
  local message=$2
  awk -v expected="$expected" '
    $0 == "  validate:" { in_validate=1; next }
    in_validate && $0 ~ /^  [^[:space:]][^:]*:/ { in_validate=0 }
    in_validate && $0 == expected { found=1 }
    END { exit found ? 0 : 1 }
  ' "$workflow" || {
    printf '%s\n' "$message" >&2
    exit 1
  }
}

workflow_validate_has_line '    runs-on: ubuntu-latest' 'workflow validate job is missing its runner'
workflow_validate_has_line '      - run: ./scripts/validate-skeleton.sh' 'workflow does not run the skeleton validator'
workflow_validate_has_line '      - run: ./tests/test-skeleton.sh' 'workflow does not run the skeleton test suite'
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

while IFS= read -r indexed; do
  [[ -e "scripts/$indexed" || -L "scripts/$indexed" ]] || {
    printf 'scripts index lists missing file: %s\n' "$indexed" >&2
    exit 1
  }
done < <(awk -F'`' '/^- `[^`]+`:/ { print $2 }' scripts/README.md)

while IFS= read -r -d '' script; do
  basename=${script##*/}
  grep -Fq "\`$basename\`" scripts/README.md || {
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
