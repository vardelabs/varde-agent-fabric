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
  tests/README.md \
  tests/test-skeleton.sh; do
  [[ -f "$path" && -s "$path" ]] || {
    printf 'missing or empty required skeleton file: %s\n' "$path" >&2
    exit 1
  }
done

if [[ -e .github/README.md || -L .github/README.md ]]; then
  printf 'GitHub homepage override is forbidden: keep the homepage in README.md, not .github/README.md\n' >&2
  exit 1
fi

# CI runs as the cluster Skeleton validation job, defined outside this
# repository. A workflow here would be a second gate nothing validates.
if [[ -e .github/workflows || -L .github/workflows ]]; then
  printf 'GitHub Actions workflows are forbidden: CI runs as the cluster Skeleton validation job\n' >&2
  exit 1
fi

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

if [[ -n "$(find scripts -mindepth 1 -type d -print -quit)" ]]; then
  printf 'scripts directory must remain flat\n' >&2
  exit 1
fi

printf 'skeleton layout and documented boundaries: ok\n'
