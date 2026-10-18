#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/public-safety-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/repo"
git_cmd=(env GIT_CONFIG_COUNT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git -c init.templateDir=)
"${git_cmd[@]}" -C "$TMP/repo" init -q
"${git_cmd[@]}" -C "$TMP/repo" config user.email test@example.invalid
"${git_cmd[@]}" -C "$TMP/repo" config user.name synthetic

run_scan() { PUBLIC_SAFETY_ROOT="$TMP/repo" "$ROOT/scripts/check-public-safety.sh"; }

printf 'synthetic content\n' > "$TMP/repo/clean.txt"
"${git_cmd[@]}" -C "$TMP/repo" add clean.txt
run_scan >/dev/null

begin_marker='-----BEGIN '
private_marker='PRIVATE KEY-----'
printf '%s%s\n' "$begin_marker" "$private_marker" > "$TMP/repo/pkcs8.txt"
"${git_cmd[@]}" -C "$TMP/repo" add pkcs8.txt
if run_scan >/dev/null 2>&1; then exit 1; fi
"${git_cmd[@]}" -C "$TMP/repo" reset -q pkcs8.txt
rm "$TMP/repo/pkcs8.txt"

encrypted_marker='ENCRYPTED PRIVATE KEY-----'
printf '%s%s\n' "$begin_marker" "$encrypted_marker" > "$TMP/repo/encrypted.txt"
"${git_cmd[@]}" -C "$TMP/repo" add encrypted.txt
if run_scan >/dev/null 2>&1; then exit 1; fi
"${git_cmd[@]}" -C "$TMP/repo" reset -q encrypted.txt
rm "$TMP/repo/encrypted.txt"

binary_marker='X25519 PRIVATE KEY-----'
printf 'prefix\0%s%s\0suffix' "$begin_marker" "$binary_marker" > "$TMP/repo/binary.dat"
"${git_cmd[@]}" -C "$TMP/repo" add binary.dat
if run_scan >/dev/null 2>&1; then exit 1; fi
"${git_cmd[@]}" -C "$TMP/repo" reset -q binary.dat
rm "$TMP/repo/binary.dat"

expect_rejected() {
  file="$1"
  content="$2"
  printf '%s' "$content" > "$TMP/repo/$file"
  "${git_cmd[@]}" -C "$TMP/repo" add "$file"
  if run_scan >/dev/null 2>&1; then exit 1; fi
  "${git_cmd[@]}" -C "$TMP/repo" reset -q "$file"
  rm "$TMP/repo/$file"
}

aws_prefix='AKIA'
aws_suffix='0000000000000000'
expect_rejected aws.txt "${aws_prefix}${aws_suffix}"
private_root='/data/'
private_agent='example-agent'
expect_rejected path.txt "${private_root}${private_agent}/state"
runtime_root='/run/user/'
runtime_id='1003/'
expect_rejected runtime.txt "${runtime_root}${runtime_id}socket"
webhook_host='discord.com/api/web''hooks/'
webhook_id='1234567890'
expect_rejected webhook.txt "${webhook_host}${webhook_id}"

printf '# comment\nzyxdeployname\n' > "$TMP/denylist"
printf 'synthetic content\n' > "$TMP/repo/named.txt"
"${git_cmd[@]}" -C "$TMP/repo" add named.txt
PUBLIC_SAFETY_DENYLIST="$TMP/denylist" run_scan >/dev/null
expect_denied() {
  printf '%s\n' "$1" > "$TMP/repo/named.txt"
  "${git_cmd[@]}" -C "$TMP/repo" add named.txt
  if PUBLIC_SAFETY_DENYLIST="$TMP/denylist" run_scan >/dev/null 2>&1; then exit 1; fi
}
expect_denied 'built for ZyxDeployName here'
denied_output="$(PUBLIC_SAFETY_DENYLIST="$TMP/denylist" run_scan 2>&1)" || true
case "$denied_output" in *denylisted*) ;; *) exit 1 ;; esac
if grep -qi zyxdeployname <<<"$denied_output"; then exit 1; fi
printf '# comment here\n' > "$TMP/repo/named.txt"
"${git_cmd[@]}" -C "$TMP/repo" add named.txt
PUBLIC_SAFETY_DENYLIST="$TMP/denylist" run_scan >/dev/null
run_scan >/dev/null
if PUBLIC_SAFETY_DENYLIST="$TMP/missing" run_scan >/dev/null 2>&1; then exit 1; fi
"${git_cmd[@]}" -C "$TMP/repo" reset -q named.txt
rm "$TMP/repo/named.txt"

if PUBLIC_SAFETY_ROOT="$TMP/not-a-repository" "$ROOT/scripts/check-public-safety.sh" >/dev/null 2>&1; then
  exit 1
fi

printf 'public safety scanner tests: 15 passed\n'
