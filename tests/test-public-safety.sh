#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
TMP="$(mktemp -d /data/vega/.tmp/public-safety-test.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/repo"
git -C "$TMP/repo" init -q
git -C "$TMP/repo" config user.email test@example.invalid
git -C "$TMP/repo" config user.name synthetic

run_scan() { PUBLIC_SAFETY_ROOT="$TMP/repo" "$ROOT/scripts/check-public-safety.sh"; }

printf 'synthetic content\n' > "$TMP/repo/clean.txt"
git -C "$TMP/repo" add clean.txt
run_scan >/dev/null

printf '%s\n' '-----BEGIN PRIVATE KEY-----' > "$TMP/repo/pkcs8.txt"
git -C "$TMP/repo" add pkcs8.txt
if run_scan >/dev/null 2>&1; then exit 1; fi
git -C "$TMP/repo" reset -q pkcs8.txt
rm "$TMP/repo/pkcs8.txt"

printf '%s\n' '-----BEGIN ENCRYPTED PRIVATE KEY-----' > "$TMP/repo/encrypted.txt"
git -C "$TMP/repo" add encrypted.txt
if run_scan >/dev/null 2>&1; then exit 1; fi
git -C "$TMP/repo" reset -q encrypted.txt
rm "$TMP/repo/encrypted.txt"

printf 'prefix\0-----BEGIN X25519 PRIVATE KEY-----\0suffix' > "$TMP/repo/binary.dat"
git -C "$TMP/repo" add binary.dat
if run_scan >/dev/null 2>&1; then exit 1; fi

printf 'public safety scanner tests: 4 passed\n'
