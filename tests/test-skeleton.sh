#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
"$root/scripts/validate-skeleton.sh"

probe=$(mktemp -d)
trap 'rm -rf "$probe"' EXIT
cp -a "$root/." "$probe/"

# The skeleton validator verifies the repository layout. Publication review is a
# separate human security gate, because a heuristic scanner cannot prove every
# reachable Git object is free of secrets or internal material.
rm "$probe/dispatch/README.md"
if output=$(cd "$probe" && ./scripts/validate-skeleton.sh 2>&1); then
  printf 'validator accepted a malformed skeleton\n' >&2
  exit 1
fi
case "$output" in
  *'missing or empty required skeleton file: dispatch/README.md'*) ;;
  *) printf 'validator rejected probe for the wrong reason\n' >&2; exit 1 ;;
esac

printf 'skeleton layout rejection: ok\n'
