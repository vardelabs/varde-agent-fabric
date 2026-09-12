#!/usr/bin/env bash
# Reject high-signal private material in the tracked public tree.
set -euo pipefail

patterns=(
  '-----BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY-----'
  'AKIA[0-9A-Z]{16}'
  '/data/(corvus|north|vega)(/|$)'
  '/run/user/[0-9]+/'
  'discord\.com/api/webhooks/[A-Za-z0-9_-]+'
)
for pattern in "${patterns[@]}"; do
  if git grep -n -I -E -- "$pattern" -- .; then
    printf 'public safety scan failed for pattern: %s\n' "$pattern" >&2
    exit 1
  fi
done
printf 'public safety scan: ok\n'
