#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="${FABRIC_TEST_TMPDIR:-$ROOT/tests/.fabric-test}"
mkdir -p "$TMP_ROOT"
TMP="$(mktemp -d "$TMP_ROOT/release-verification.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

cd "$ROOT"
git init -q "$TMP/repo"
git -C "$TMP/repo" config user.email test@example.invalid
git -C "$TMP/repo" config user.name synthetic
printf 'release\n' > "$TMP/repo/artifact.bin"
git -C "$TMP/repo" add artifact.bin
git -C "$TMP/repo" commit -q -m initial
rollback=$(git -C "$TMP/repo" rev-parse HEAD)
printf 'release-v2\n' > "$TMP/repo/artifact.bin"
git -C "$TMP/repo" commit -q -am update
release=$(git -C "$TMP/repo" rev-parse HEAD)
checksum=$(sha256sum "$TMP/repo/artifact.bin" | cut -d' ' -f1)

./scripts/verify-release.sh \
  --repo "$TMP/repo" \
  --commit "$release" \
  --artifact "$TMP/repo/artifact.bin" \
  --sha256 "$checksum" \
  --rollback-commit "$rollback"

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

printf 'release verification tests: passed\n'
