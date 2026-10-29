#!/usr/bin/env bash
# A real OpenCode review against a provider that never stops calling tools, and
# answers OpenCode's forced step-limit turn with a work summary, must still end
# with findings, written in the backend's own final turn. Skipped without the
# opencode CLI. Runs offline: the provider is a local fixture.
set -euo pipefail

command -v opencode >/dev/null 2>&1 || { printf 'skip: opencode CLI not found\n'; exit 0; }
[[ "$(opencode --version 2>&1)" == *v2.* ]] || { printf 'skip: the final turn is checked on OpenCode v2\n'; exit 0; }

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/opencode-final-turn.XXXXXX")"
provider_pid=''
stop_provider() { [[ -z "$provider_pid" ]] || kill "$provider_pid" 2>/dev/null || true; provider_pid=''; }
trap 'stop_provider; rm -rf "$TMP"' EXIT
. "$ROOT/dispatch/cli/backends/lib/review-steps.sh"
pass=0

# review <name> <provider mode> [env assignments...]: one review through the backend; sets status.
review() {
  local name="$1" mode="$2" home="$TMP/$1/opencode-home"
  shift 2
  mkdir -p "$TMP/$name/caller-home" "$home/.config/opencode" "$TMP/$name/work"
  printf 'one\n' > "$TMP/$name/work/a.txt"
  printf 'diff body\n' > "$TMP/$name/change.diff"
  python3 "$ROOT/tests/dispatch_cli/fixtures/looping_provider.py" "$TMP/$name/turns" "$mode" > "$TMP/$name/port" &
  provider_pid=$!
  for _ in $(seq 50); do [[ -s "$TMP/$name/port" ]] && break; sleep 0.1; done
  cat > "$home/.config/opencode/opencode.json" <<JSON
{"enabled_providers":["fake"],
 "provider":{"fake":{"npm":"@ai-sdk/openai-compatible","options":{"baseURL":"http://127.0.0.1:$(cat "$TMP/$name/port")/v1","apiKey":"unused"},"models":{"loop":{}}}},
 "permission":{"*":"deny","read":"allow","glob":"allow","grep":"allow"}}
JSON
  status=0
  env HOME="$TMP/$name/caller-home" DISPATCH_OPENCODE_HOME="$home" OPENCODE_DISABLE_MODELS_FETCH=1 \
    DISPATCH_KIND=review DISPATCH_TASK=review DISPATCH_MODEL=fake/loop DISPATCH_DIFF="$TMP/$name/change.diff" \
    DISPATCH_WORKDIR="$TMP/$name/work" DISPATCH_OUTPUT="$TMP/$name/out" "$@" \
    timeout 120 /bin/bash "$ROOT/dispatch/cli/backends/opencode.sh" 2>"$TMP/$name/stderr" || status=$?
  stop_provider
}
expect() { "$@" || { printf 'failed: %s\n' "$*" >&2; exit 1; }; pass=$((pass + 1)); }
findings() { PYTHONPATH="$ROOT" python3 -c 'import sys;from dispatch.adapters import opencode;print(len(opencode.parse_review(open(sys.argv[1],"rb").read())))' "$1"; }

# The step limit forces a summary; the backend's final turn writes the findings.
review limit default
expect test "$status" -eq 0
expect test "$(grep -cx tools "$TMP/limit/turns")" -eq $((review_step_limit - 1))
expect test "$(tail -n 2 "$TMP/limit/turns" | tr '\n' ' ')" = 'summary findings '
expect test "$(findings "$TMP/limit/out")" -eq 1
expect test -z "$(find "$TMP/limit" -maxdepth 1 -name 'out.opencode*')"

# A final turn that writes a summary again fails once, with no second resume.
review stubborn stubborn
expect test "$status" -eq 1
expect grep -qF 'final turn wrote no findings' "$TMP/stubborn/stderr"
expect test "$(grep -cvx tools "$TMP/stubborn/turns")" -eq 3
expect test ! -e "$TMP/stubborn/out"

# A deadline stops a slow exploring run at its cut, before the step limit and
# its forced summary, and the final turn still writes the findings in time.
started="$(date +%s)"
review deadline slow DISPATCH_DEADLINE_EPOCH="$((started + 36))"
expect test "$status" -eq 0
expect test "$(($(date +%s) - started))" -lt 36
expect test "$(grep -cx tools "$TMP/deadline/turns")" -lt $((review_step_limit - 1))
expect test "$(tail -n 1 "$TMP/deadline/turns")" = findings
expect test "$(findings "$TMP/deadline/out")" -eq 1

printf 'opencode review final turn: %s passed\n' "$pass"
