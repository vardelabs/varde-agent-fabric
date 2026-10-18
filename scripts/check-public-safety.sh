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
  '/data/[A-Za-z0-9_-]+(/|$)'
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
# Deployment names stay out of a public tree, so they come from a list the
# deployment keeps outside it.
denylist="${PUBLIC_SAFETY_DENYLIST:-}"
if [ -n "$denylist" ]; then
  if [ ! -r "$denylist" ]; then
    printf 'public safety scan could not read the denylist\n' >&2
    exit 2
  fi
  # grep -F -f reads a comment line as one more name to match.
  set +e
  names="$(grep -v -E '^[[:space:]]*(#|$)' "$denylist")"
  status=$?
  if [ "$status" -le 1 ] && [ -n "$names" ]; then
    git -C "$root" grep --text -n -i -w -F -f <(printf '%s\n' "$names") -- . >/dev/null
    status=$?
  elif [ "$status" -le 1 ]; then
    status=1
  fi
  set -e
  case "$status" in
    0)
      printf 'public safety scan failed: a denylisted name is tracked (name withheld)\n' >&2
      exit 1
      ;;
    1) ;;
    *)
      printf 'public safety scan could not inspect repository (status %s)\n' "$status" >&2
      exit "$status"
      ;;
  esac
fi
printf 'public safety scan: ok\n'
