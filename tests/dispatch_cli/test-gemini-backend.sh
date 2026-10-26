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
# turn <model> <role> <requests> <errors>: one model's stats entry for one role.
turn() { printf '"%s":{"api":{"totalRequests":%s,"totalErrors":%s},"roles":{"%s":{"totalRequests":%s,"totalErrors":%s}}}' "$1" "$3" "$4" "$2" "$3" "$4"; }
answer() { printf '{"session_id":"s","response":"%s","stats":{"models":{%s}}}\n' "$1" "$2"; }
quota_error() { printf '{"session_id":"s","error":{%s}}\n' "$1" >&2; exit 173; }
case "$(cat "$HOME/scenario")" in
  ok) answer "review text" "$(turn "$model" main 1 0)" ;;
  with-utility) answer "review text" "$(turn "$model" main 1 0),$(turn utility-model utility_router 2 0)" ;;
  retried-main) answer "review text" "$(turn "$model" main 3 2)" ;;
  quota)
    printf 'Attempt 1 failed. Max attempts reached\n' >&2
    quota_error '"type":"TerminalQuotaError","message":"You have exhausted your capacity on this model.","code":429' ;;
  quota-type-only) quota_error '"type":"RetryableQuotaError","message":"Rate limited."' ;;
  quota-code-only) quota_error '"type":"ApiError","message":"Too many requests.","code":429' ;;
  not-found)
    printf '{"session_id":"s","error":{"type":"ModelNotFoundError","message":"Requested entity was not found.","code":404}}\n' >&2
    exit 1 ;;
  swapped) answer "other model text" "$(turn "$model" main 2 2),$(turn fallback-model main 1 0)" ;;
  later-switch) answer "other model text" "$(turn "$model" main 2 1),$(turn fallback-model main 1 0)" ;;
  downgraded) answer "other model text" "$(turn fallback-model main 1 0)" ;;
  no-main) answer "text" "$(turn utility-model utility_router 1 0)" ;;
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
check grep -qF '"advanced":{"ignoreLocalEnv":true}' "$record"
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

# A quota is recognised by its error type alone or by its 429 code alone.
for scenario in quota-type-only quota-code-only; do
  run "$scenario" "out-$scenario" --kind review --diff "$TMP/change.diff" --task t
  check test "$status" -eq 75
  check grep -qF 'limit reached' "$TMP/out-$scenario.stderr"
done

# A non-quota error fails loudly with the CLI's status, never as a limit.
run not-found out-not-found --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 1
check grep -qF 'ModelNotFoundError' "$TMP/out-not-found.stderr"
check_not grep -qF 'limit reached' "$TMP/out-not-found.stderr"

# Utility calls on other models, and a main turn retried to success, are a normal run.
for scenario in with-utility retried-main; do
  run "$scenario" "out-$scenario" --kind review --diff "$TMP/change.diff" --task t
  check test "$status" -eq 0
  check grep -qxF 'review text' "$TMP/out-$scenario"
done

# Any main turn answered by another model is refused loudly and is never a limit,
# whether the requested model failed every turn or only a later one.
for scenario in swapped later-switch; do
  run "$scenario" "out-$scenario" --kind review --diff "$TMP/change.diff" --task t
  check test "$status" -eq 1
  check grep -qF 'switched main turns' "$TMP/out-$scenario.stderr"
  check_not grep -qF 'limit reached' "$TMP/out-$scenario.stderr"
  check test ! -e "$TMP/out-$scenario"
done

# Another model answering from the first turn is a switch too.
run downgraded out-downgraded --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 1
check grep -qF 'switched main turns' "$TMP/out-downgraded.stderr"
check_not grep -qF 'limit reached' "$TMP/out-downgraded.stderr"

run no-main out-no-main --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 1
check grep -qF 'answered no main turn' "$TMP/out-no-main.stderr"

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
