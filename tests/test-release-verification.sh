#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="${FABRIC_TEST_TMPDIR:-$ROOT/tests/.fabric-test}"
mkdir -p "$TMP_ROOT"
TMP="$(mktemp -d "$TMP_ROOT/release-verification.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

cd "$ROOT"
git_cmd=(env GIT_CONFIG_COUNT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git)
"${git_cmd[@]}" init -q "$TMP/repo"
"${git_cmd[@]}" -C "$TMP/repo" config user.email test@example.invalid
"${git_cmd[@]}" -C "$TMP/repo" config user.name synthetic
printf 'release\n' > "$TMP/repo/artifact.bin"
"${git_cmd[@]}" -C "$TMP/repo" add artifact.bin
"${git_cmd[@]}" -C "$TMP/repo" commit -q -m initial
rollback=$("${git_cmd[@]}" -C "$TMP/repo" rev-parse HEAD)
printf 'release-v2\n' > "$TMP/repo/artifact.bin"
"${git_cmd[@]}" -C "$TMP/repo" commit -q -am update
release=$("${git_cmd[@]}" -C "$TMP/repo" rev-parse HEAD)
checksum=$(sha256sum "$TMP/repo/artifact.bin" | cut -d' ' -f1)

output=$(env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.excludesFile GIT_CONFIG_VALUE_0=/dev/null \
  ./scripts/verify-release.sh \
  --repo "$TMP/repo" \
  --commit "$release" \
  --artifact "$TMP/repo/artifact.bin" \
  --sha256 "$checksum" \
  --rollback-commit "$rollback")
python3 - "$output" "$release" "$rollback" "$checksum" <<'PY'
import json
import sys
value = json.loads(sys.argv[1])
assert value == {
    "artifact_sha256": sys.argv[4],
    "rollback_commit": sys.argv[3],
    "tested_commit": sys.argv[2],
}
PY

if ./scripts/verify-release.sh --repo "$TMP/repo" --commit "$release" \
  --artifact "$TMP/repo/artifact.bin" --sha256 "0$(printf '%0.s0' {1..63})" \
  --rollback-commit "$rollback"; then
  printf 'expected checksum mismatch to fail\n' >&2
  exit 1
fi

if ./scripts/verify-release.sh --repo "$TMP/repo" --commit "$rollback" \
  --artifact "$TMP/repo/artifact.bin" --sha256 "$checksum" \
  --rollback-commit "$release"; then
  printf 'expected forward rollback commit to fail\n' >&2
  exit 1
fi

upper_release=$(printf '%s' "$release" | tr '[:lower:]' '[:upper:]')
if ./scripts/verify-release.sh --repo "$TMP/repo" --commit "$upper_release" \
  --artifact "$TMP/repo/artifact.bin" --sha256 "$checksum" \
  --rollback-commit "$upper_release"; then
  printf 'expected case-insensitive self-rollback to fail\n' >&2
  exit 1
fi

printf 'release verification tests: passed\n'
