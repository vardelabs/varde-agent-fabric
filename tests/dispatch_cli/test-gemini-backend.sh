#!/usr/bin/env bash
# The gemini backend against a fake `gemini` CLI: no account, network, or key.
set -euo pipefail
unset DISPATCH_MODEL DISPATCH_ROUTES_FILE DISPATCH_GEMINI_HOME DISPATCH_GEMINI_ALLOW_WRITE
unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_USE_VERTEXAI GOOGLE_GENAI_USE_GCA

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
CLI="$ROOT/dispatch/cli/varde-agent-dispatch"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/gemini-backend-test.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/work" "$TMP/caller-home" "$TMP/gemini-home" "$TMP/bin"
printf 'diff body\n' > "$TMP/change.diff"
MODEL=example-gemini-model

pass=0
check() { "$@"; pass=$((pass + 1)); }
check_not() { if "$@"; then printf 'unexpected match: %s\n' "$*" >&2; exit 1; fi; pass=$((pass + 1)); }

# The fake records what it was given, then answers as the scenario file says.
cat > "$TMP/bin/gemini" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
record="$HOME/record"
{
  printf 'argv=%s\n' "$*"
  printf 'cwd=%s\n' "$PWD"
  printf 'home=%s cli_home=%s\n' "$HOME" "${GEMINI_CLI_HOME:-}"
  printf 'trust=%s\n' "${GEMINI_CLI_TRUST_WORKSPACE:-}"
  printf 'keys=%s\n' "${GEMINI_API_KEY:-}${GOOGLE_API_KEY:-}${DISPATCH_UNSAFE_SENTINEL:-}"
  printf 'settings=%s\n' "$(tr -d ' \n' < "$GEMINI_CLI_SYSTEM_SETTINGS_PATH")"
  printf 'stdin=%s\n' "$(cat)"
} > "$record"
model=""
while (($#)); do
  case "$1" in --model) model="$2"; shift 2 ;; *) shift ;; esac
done
stats() { printf '{"models":{"%s":{"api":{"totalRequests":%s,"totalErrors":%s}}}}' "$1" "$2" "$3"; }
case "$(cat "$HOME/scenario")" in
  ok) printf '{"session_id":"s","response":"review text","stats":%s}\n' "$(stats "$model" 1 0)" ;;
  quota)
    printf 'Attempt 1 failed. Max attempts reached\n' >&2
    printf '{"session_id":"s","error":{"type":"TerminalQuotaError","message":"You have exhausted your capacity on this model.","code":429}}\n' >&2
    exit 173 ;;
  retryable)
    printf '{"session_id":"s","error":{"type":"RetryableQuotaError","message":"Rate limited.","code":429}}\n' >&2
    exit 173 ;;
  swapped) printf '{"session_id":"s","response":"other model text","stats":{"models":{"%s":{"api":{"totalRequests":2,"totalErrors":2}},"fallback-model":{"api":{"totalRequests":1,"totalErrors":0}}}}}\n' "$model" ;;
  downgraded) printf '{"session_id":"s","response":"other model text","stats":%s}\n' "$(stats fallback-model 1 0)" ;;
  auth)
    printf '{"session_id":"s","error":{"type":"FatalAuthenticationError","message":"Please sign in.","code":41}}\n' >&2
    exit 41 ;;
esac
EOF
chmod +x "$TMP/bin/gemini"

run() {  # run <scenario> <out-name> [extra dispatcher args...]
  printf '%s' "$1" > "$TMP/gemini-home/scenario"
  local name="$2"; shift 2
  status=0
  HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
    GEMINI_API_KEY=must-not-forward DISPATCH_UNSAFE_SENTINEL=must-not-forward DISPATCH_MODEL="$MODEL" \
    "$CLI" --harness gemini --workdir "$TMP/work" --output "$TMP/$name" "$@" \
    >"$TMP/$name.stdout" 2>"$TMP/$name.stderr" || status=$?
}

run ok out-review --kind review --diff "$TMP/change.diff" --task private-task-sentinel
check test "$status" -eq 0
check grep -qxF 'review text' "$TMP/out-review"
check test "$(stat -c '%a' "$TMP/out-review" 2>/dev/null || stat -f '%Lp' "$TMP/out-review")" = 600
record="$TMP/gemini-home/record"
check grep -qF -- "--model $MODEL" "$record"
check grep -qF -- '--approval-mode default' "$record"
check grep -qF -- '--output-format json' "$record"
check grep -qxF 'trust=false' "$record"
check grep -qxF "home=$TMP/gemini-home cli_home=$TMP/gemini-home" "$record"
check grep -qxF "cwd=$TMP/work" "$record"
check grep -qxF 'keys=' "$record"
check grep -qF '"enforcedType":"oauth-personal"' "$record"
check grep -qF '"folderTrust":{"enabled":false}' "$record"
check grep -qF 'stdin=private-task-sentinel' "$record"
check grep -qF 'diff body' "$record"
check_not grep -qF 'private-task-sentinel' <(grep '^argv=' "$record")

# With no --output the dispatcher returns its private temporary file; a loose
# umask must not make the review text readable by other users.
default_output="$(umask 022; HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" TMPDIR="$TMP" "$CLI" --harness gemini --kind review --diff "$TMP/change.diff" \
  --task t --workdir "$TMP/work" 2>/dev/null)"
check test "$(stat -c '%a' "$default_output" 2>/dev/null || stat -f '%Lp' "$default_output")" = 600

run quota out-quota --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 75
check grep -qF 'limit reached' "$TMP/out-quota.stderr"
check grep -qF 'TerminalQuotaError' "$TMP/out-quota.stderr"
check test ! -e "$TMP/out-quota"

run retryable out-retryable --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 75

run swapped out-swapped --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 75
check grep -qF 'switched to another model' "$TMP/out-swapped.stderr"
check test ! -e "$TMP/out-swapped"

run downgraded out-downgraded --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 1
check grep -qF 'did not use the requested model' "$TMP/out-downgraded.stderr"
check_not grep -qF 'limit reached' "$TMP/out-downgraded.stderr"

run auth out-auth --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 41
check grep -qF 'FatalAuthenticationError' "$TMP/out-auth.stderr"
check_not grep -qF 'limit reached' "$TMP/out-auth.stderr"

run ok out-implement-denied --kind implement --task t
check test "$status" -eq 2
check grep -qF 'DISPATCH_GEMINI_ALLOW_WRITE=1' "$TMP/out-implement-denied.stderr"

printf '%s' ok > "$TMP/gemini-home/scenario"
status=0
HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_GEMINI_ALLOW_WRITE=1 DISPATCH_MODEL="$MODEL" \
  "$CLI" --harness gemini --kind implement --task t --workdir "$TMP/work" --output "$TMP/out-implement" \
  >/dev/null 2>"$TMP/out-implement.stderr" || status=$?
check test "$status" -eq 0
check grep -qF -- '--approval-mode auto_edit' "$record"
check grep -qxF 'trust=true' "$record"

mkdir -p "$TMP/caller-home/nested-gemini-home"
for bad_home in "$TMP/caller-home/nested-gemini-home" "$TMP" relative-home; do
  status=0
  HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$bad_home" DISPATCH_MODEL="$MODEL" \
    "$CLI" --harness gemini --kind review --diff "$TMP/change.diff" --task t --workdir "$TMP/work" \
    --output "$TMP/out-bad-home" >/dev/null 2>"$TMP/out-bad-home.stderr" || status=$?
  check test "$status" -eq 2
  check grep -qF 'DISPATCH_GEMINI_HOME must' "$TMP/out-bad-home.stderr"
done

printf 'gemini backend tests: %s passed\n' "$pass"
