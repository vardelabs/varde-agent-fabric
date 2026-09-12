#!/usr/bin/env bash
# Reject high-signal private material in the tracked public tree.
set -euo pipefail

root="${PUBLIC_SAFETY_ROOT:-.}"
begin_marker="$(printf '%s' '-----BEGIN ')"
key_marker="$(printf '%s' 'PRIVATE KEY-----')"
private_key_pattern="${begin_marker}.*${key_marker}"
patterns=(
  "$private_key_pattern"
  'AKIA[0-9A-Z]{16}'
  '/data/(corvus|north|vega)(/|$)'
  '/run/user/[0-9]+/'
  'discord\.com/api/webhooks/[A-Za-z0-9_-]+'
)
for pattern in "${patterns[@]}"; do
  set +e
  git -C "$root" grep --text -n -E -- "$pattern" -- .
  status=$?
  set -e
  case "$status" in
    0)
      printf 'public safety scan failed for pattern: %s\n' "$pattern" >&2
      exit 1
      ;;
    1) ;;
    *)
      printf 'public safety scan could not inspect repository (status %s)\n' "$status" >&2
      exit "$status"
      ;;
  esac
done
printf 'public safety scan: ok\n'
