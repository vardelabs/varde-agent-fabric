#!/usr/bin/env bash
# A real OpenCode review against a provider that never stops calling tools must
# still end, at the review step limit, with a text answer. Skipped without the
# opencode CLI. Runs offline: the provider is a local fixture.
set -euo pipefail

command -v opencode >/dev/null 2>&1 || { printf 'skip: opencode CLI not found\n'; exit 0; }
[[ "$(opencode --version 2>&1)" == *v2.* ]] || { printf 'skip: the step limit is checked on OpenCode v2\n'; exit 0; }

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/opencode-step-limit.XXXXXX")"
provider_pid=''
cleanup() { [[ -z "$provider_pid" ]] || kill "$provider_pid" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT
mkdir -p "$TMP/caller-home" "$TMP/opencode-home/.config/opencode" "$TMP/work"
printf 'one\n' > "$TMP/work/a.txt"
printf 'diff body\n' > "$TMP/change.diff"

python3 "$ROOT/tests/dispatch_cli/fixtures/looping_provider.py" "$TMP/turns" > "$TMP/port" &
provider_pid=$!
for _ in $(seq 50); do [[ -s "$TMP/port" ]] && break; sleep 0.1; done
port="$(cat "$TMP/port")"
cat > "$TMP/opencode-home/.config/opencode/opencode.json" <<JSON
{"enabled_providers":["fake"],
 "provider":{"fake":{"npm":"@ai-sdk/openai-compatible","options":{"baseURL":"http://127.0.0.1:$port/v1","apiKey":"unused"},"models":{"loop":{}}}},
 "permission":{"*":"deny","read":"allow","glob":"allow","grep":"allow"}}
JSON

status=0
HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" OPENCODE_DISABLE_MODELS_FETCH=1 \
  DISPATCH_KIND=review DISPATCH_TASK=review DISPATCH_MODEL=fake/loop DISPATCH_DIFF="$TMP/change.diff" \
  DISPATCH_WORKDIR="$TMP/work" DISPATCH_OUTPUT="$TMP/out" \
  timeout 60 /bin/bash "$ROOT/dispatch/cli/backends/opencode.sh" 2>"$TMP/stderr" || status=$?
[[ "$status" -eq 0 ]] || { printf 'review failed with status %d\n' "$status" >&2; cat "$TMP/stderr" >&2; exit 1; }

. "$ROOT/dispatch/cli/backends/lib/review-steps.sh"
tool_turns="$(grep -cx tools "$TMP/turns")"
# The last turn has tools taken away, so the model must answer in text.
[[ "$(tail -n 1 "$TMP/turns")" == text ]] || { printf 'the last turn still offered tools\n' >&2; exit 1; }
(( tool_turns == review_step_limit - 1 )) || {
  printf 'expected %d tool turns before the forced answer, saw %d\n' "$((review_step_limit - 1))" "$tool_turns" >&2
  exit 1
}
grep -qF '"type":"text"' "$TMP/out" || { printf 'the review wrote no text event\n' >&2; exit 1; }
printf 'opencode step limit: ok (%d tool turns, then a text answer)\n' "$tool_turns"
