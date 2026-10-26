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
mkdir -p "$TMP/work" "$TMP/caller-home" "$TMP/gemini-home/.gemini" "$TMP/bin"
printf 'diff body\n' > "$TMP/change.diff"
# The dedicated home holds the sign-in and the operator's own settings; a run
# must use the sign-in and never these settings, which pick an API key.
printf '{"refresh_token":"example"}' > "$TMP/gemini-home/.gemini/oauth_creds.json"
mkdir -p "$TMP/gemini-home/.gemini/tmp"
printf '{"security":{"auth":{"selectedType":"gemini-api-key"}}}' > "$TMP/gemini-home/.gemini/settings.json"
state="$TMP/gemini-home/.gemini"
MODEL=example-gemini-model

pass=0
check() { "$@"; pass=$((pass + 1)); }
check_not() { if "$@"; then printf 'unexpected match: %s\n' "$*" >&2; exit 1; fi; pass=$((pass + 1)); }

# The fake records what it was given, then answers as the scenario file says.
cat > "$TMP/bin/gemini" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# The scenario file sits beside the sign-in, found through the sign-in's link.
state="$(dirname -- "$(readlink -f -- "$HOME/.gemini/oauth_creds.json")")"
{
  printf 'argv=%s\n' "$*"
  printf 'cwd=%s\n' "$PWD"
  printf 'home=%s cli_home=%s\n' "$HOME" "${GEMINI_CLI_HOME:-}"
  printf 'trust=%s\n' "${GEMINI_CLI_TRUST_WORKSPACE:-}"
  printf 'keys=%s\n' "${GEMINI_API_KEY:-}${GOOGLE_API_KEY:-}${DISPATCH_UNSAFE_SENTINEL:-}"
  printf 'system_settings=%s\n' "${GEMINI_CLI_SYSTEM_SETTINGS_PATH:-}"
  printf 'settings=%s\n' "$(tr -d ' \n' < "$HOME/.gemini/settings.json")"
  printf 'creds=%s\n' "$(readlink -f -- "$HOME/.gemini/oauth_creds.json")"
  [[ ! -e "$HOME/.gemini/tmp" ]] || touch "$HOME/.gemini/tmp/leaked"
  printf 'scratch_mode=%s\n' "$(stat -c '%a' "$(dirname -- "$HOME")" 2>/dev/null || stat -f '%Lp' "$(dirname -- "$HOME")")"
  printf 'stdin=%s\n' "$(cat)"
} > "$state/record"
model=""
while (($#)); do
  case "$1" in --model) model="$2"; shift 2 ;; *) shift ;; esac
done
# turn <model> <role> <requests> <errors>: one model's stats entry for one role.
turn() { printf '"%s":{"api":{"totalRequests":%s,"totalErrors":%s},"roles":{"%s":{"totalRequests":%s,"totalErrors":%s}}}' "$1" "$3" "$4" "$2" "$3" "$4"; }
answer() { printf '{"session_id":"s","response":"%s","stats":{"models":{%s}}}\n' "$1" "$2"; }
quota_error() { printf '{"session_id":"s","error":{%s}}\n' "$1" >&2; exit 173; }
case "$(cat "$state/scenario")" in
  ok) answer "review text" "$(turn "$model" main 1 0)" ;;
  with-utility) answer "review text" "$(turn "$model" main 1 0),$(turn utility-model utility_router 2 0)" ;;
  retried-main) answer "review text" "$(turn "$model" main 3 2)" ;;
  retry-logged)
    printf 'Attempt 1 failed with status 429. Retrying... {"error":{"code":429,"message":"Rate limited."}}\n' >&2
    answer "review text" "$(turn "$model" main 2 1)" ;;
  retry-then-fail)
    printf 'Attempt 1 failed with status 429. Retrying... {"error":{"code":429,"message":"Rate limited."}}\n' >&2
    printf '[ERROR] {"session_id":"s","error":{"type":"FatalToolExecutionError","message":"No space left.","code":"NO_SPACE_LEFT"}}\n' >&2
    exit 1 ;;
  subagent-other) answer "relayed text" "$(turn "$model" main 1 0),$(turn fallback-model subagent 1 0)" ;;
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
  all-failed) answer "text" "$(turn "$model" main 2 2)" ;;
  slow) sleep 6 ;;
  text-auth)
    printf 'Warning: something unrelated\nError authenticating: FatalAuthenticationError: Manual authorization is required\n' >&2
    exit 41 ;;
  auth)
    printf '{"session_id":"s","error":{"type":"FatalAuthenticationError","message":"Please sign in.","code":41}}\n' >&2
    exit 41 ;;
esac
EOF
chmod +x "$TMP/bin/gemini"

run() {  # run <scenario> <out-name> [extra dispatcher args...]
  printf '%s' "$1" > "$state/scenario"
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
record="$state/record"
check grep -qF -- "--model $MODEL" "$record"
check grep -qF -- '--approval-mode default' "$record"
check grep -qF -- '--output-format json' "$record"
check grep -qxF 'trust=false' "$record"
# A per-run home, not the dedicated one, so the operator's settings never load;
# the sign-in is the dedicated home's file, reached through a link.
run_home="$(sed -n 's/^home=\([^ ]*\) .*/\1/p' "$record")"
check test -n "$run_home"
check test "$run_home" != "$TMP/gemini-home"
check grep -qxF "home=$run_home cli_home=$run_home" "$record"
check grep -qxF "creds=$TMP/gemini-home/.gemini/oauth_creds.json" "$record"
check_not grep -qF 'gemini-api-key' "$record"
check grep -qxF 'system_settings=' "$record"
check grep -qxF 'scratch_mode=700' "$record"
check grep -qxF "cwd=$TMP/work" "$record"
check_not grep -qF -- '--include-directories' "$record"
check grep -qxF 'keys=' "$record"
check grep -qF '"enforcedType":"oauth-personal"' "$record"
check grep -qF '"folderTrust":{"enabled":false}' "$record"
check grep -qF '"advanced":{"ignoreLocalEnv":true}' "$record"
check grep -qF '"tools":{"exclude":["google_web_search","web_fetch"],"disableLLMCorrection":true}' "$record"
check grep -qF '"experimental":{"enableAgents":false}' "$record"
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

# A run that fails for another reason is not a limit, though a retry logged a 429.
run retry-then-fail out-retry-then-fail --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 1
check grep -qF 'FatalToolExecutionError' "$TMP/out-retry-then-fail.stderr"
check_not grep -qF 'limit reached' "$TMP/out-retry-then-fail.stderr"

# A non-quota error fails loudly with the CLI's status, never as a limit.
run not-found out-not-found --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 1
check grep -qF 'ModelNotFoundError' "$TMP/out-not-found.stderr"
check_not grep -qF 'limit reached' "$TMP/out-not-found.stderr"

# Utility calls on other models, and a main turn retried to success, are a normal
# run, even when the retry logged the 429 it cleared.
for scenario in with-utility retried-main retry-logged; do
  run "$scenario" "out-$scenario" --kind review --diff "$TMP/change.diff" --task t
  check test "$status" -eq 0
  check grep -qxF 'review text' "$TMP/out-$scenario"
done

# Any main turn answered by another model is refused loudly and is never a limit,
# whether the requested model failed every turn or only a later one.
# A subagent on another model counts too, since the main turn may only relay it.
for scenario in swapped later-switch subagent-other; do
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

# No main turn answered, whether nothing ran on the main role or every main
# request of the requested model failed.
for scenario in no-main all-failed; do
  run "$scenario" "out-$scenario" --kind review --diff "$TMP/change.diff" --task t
  check test "$status" -eq 1
  check grep -qF 'answered no main turn' "$TMP/out-$scenario.stderr"
  check_not grep -qF 'limit reached' "$TMP/out-$scenario.stderr"
done

# The per-run home holds links to the sign-in: a finished run and a run ended by
# TERM both remove it.
mkdir -p "$TMP/runs"
run ok out-cleanup --kind review --diff "$TMP/change.diff" --task t
printf '%s' slow > "$state/scenario"
TMPDIR="$TMP/runs" HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" "$CLI" --harness gemini --kind review --diff "$TMP/change.diff" --task t \
  --workdir "$TMP/work" --output "$TMP/out-slow" >/dev/null 2>&1 &
slow_pid=$!
sleep 1
check test -n "$(find "$TMP/runs" -maxdepth 1 -name 'gemini-dispatch.*')"
kill -TERM "$slow_pid"
slow_status=0
wait "$slow_pid" || slow_status=$?
check test "$slow_status" -eq 143
for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -z "$(find "$TMP/runs" -maxdepth 1 -name 'gemini-dispatch.*')" ]] && break; sleep 0.5; done
check test -z "$(find "$TMP/runs" -maxdepth 1 -name 'gemini-dispatch.*')"

# A fatal error printed as text, as a missing sign-in is, is still named.
run text-auth out-text-auth --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 41
check grep -qF 'FatalAuthenticationError: Manual authorization is required' "$TMP/out-text-auth.stderr"

# The classifier itself creates the output at 0600; the dispatcher's own
# pre-created file must not be what makes the mode right.
printf '{"session_id":"s","response":"direct","stats":{"models":{"%s":{"roles":{"main":{"totalRequests":1,"totalErrors":0}}}}}}' "$MODEL" > "$TMP/direct.stdout"
: > "$TMP/direct.stderr"
(umask 022; python3 "$ROOT/dispatch/cli/backends/gemini_result.py" "$TMP/direct.stdout" "$TMP/direct.stderr" "$MODEL" 0 "$TMP/direct-out")
check grep -qxF 'direct' "$TMP/direct-out"
check test "$(stat -c '%a' "$TMP/direct-out" 2>/dev/null || stat -f '%Lp' "$TMP/direct-out")" = 600

run auth out-auth --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 41
check grep -qF 'FatalAuthenticationError' "$TMP/out-auth.stderr"
check_not grep -qF 'limit reached' "$TMP/out-auth.stderr"

run ok out-implement-denied --kind implement --task t
check test "$status" -eq 2
check grep -qF 'DISPATCH_GEMINI_ALLOW_WRITE=1' "$TMP/out-implement-denied.stderr"

printf '%s' ok > "$state/scenario"
status=0
HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_GEMINI_ALLOW_WRITE=1 DISPATCH_MODEL="$MODEL" \
  "$CLI" --harness gemini --kind implement --task t --workdir "$TMP/work" --output "$TMP/out-implement" \
  >/dev/null 2>"$TMP/out-implement.stderr" || status=$?
check test "$status" -eq 0
check grep -qF -- '--approval-mode auto_edit' "$record"
check grep -qxF 'trust=true' "$record"
# Implement trusts only an empty folder of its own and reaches the worktree as an
# included directory, so the worktree's own Gemini settings and env never load.
check_not grep -qxF "cwd=$TMP/work" "$record"
check grep -qE "^cwd=$TMP/gemini-home/\.dispatch-folder\.[A-Za-z0-9]+$" "$record"
check grep -qF -- "--include-directories $TMP/work" "$record"
check test -z "$(find "$TMP/gemini-home" -maxdepth 1 -name '.dispatch-folder.*')"
# Only the sign-in is linked in; other state in the dedicated home stays out.
check_not test -e "$TMP/gemini-home/.gemini/tmp/leaked"

# A trusted folder loads .gemini/.env from every parent, so implement refuses
# when a parent holds one or could let another user plant one.
implement_refused() {  # implement_refused <name> <message> [workdir] [path]
  status=0
  HOME="$TMP/caller-home" PATH="${4:-$TMP/bin:/usr/bin:/bin}" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
    DISPATCH_GEMINI_ALLOW_WRITE=1 DISPATCH_MODEL="$MODEL" "$CLI" --harness gemini --kind implement --task t \
    --workdir "${3:-$TMP/work}" --output "$TMP/out-$1" >/dev/null 2>"$TMP/out-$1.stderr" || status=$?
  check test "$status" -eq 2
  check grep -qF "$2" "$TMP/out-$1.stderr"
}
printf 'CODE_ASSIST_ENDPOINT=https://example.invalid\n' > "$TMP/gemini-home/.gemini/.env"
implement_refused planted-env '.gemini/.env above its folder'
rm -f "$TMP/gemini-home/.gemini/.env"
chmod g+w "$TMP/gemini-home"
implement_refused open-parent 'closed to group and others'
chmod g-w "$TMP/gemini-home"
# A parent owned by another user is refused even when its mode is closed. The
# fake id makes this user someone other than the owner of every parent.
mkdir -p "$TMP/other-user-bin"
printf '#!/bin/sh\necho 65533\n' > "$TMP/other-user-bin/id"
chmod +x "$TMP/other-user-bin/id"
implement_refused other-owner 'owned by this user or root' "$TMP/work" "$TMP/other-user-bin:$TMP/bin:/usr/bin:/bin"
# The CLI splits an included directory on commas and trims it, so such a
# workdir is refused.
mkdir -p "$TMP/work,extra" "$TMP/work-space " "$TMP/work-space /inner"
implement_refused comma-workdir 'no comma' "$TMP/work,extra"
implement_refused space-workdir 'no trailing space' "$TMP/work-space "
implement_refused space-parent 'no trailing space' "$TMP/work-space /inner"
# The CLI resolves .. without following links, so it gets the resolved workdir.
mkdir -p "$TMP/work/inner"
ln -s "$TMP/work/inner" "$TMP/inner-link"
printf '%s' ok > "$state/scenario"
status=0
HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_GEMINI_ALLOW_WRITE=1 DISPATCH_MODEL="$MODEL" "$CLI" --harness gemini --kind implement --task t \
  --workdir "$TMP/inner-link/.." --output "$TMP/out-dotdot" >/dev/null 2>&1 || status=$?
check test "$status" -eq 0
check grep -qE -- "--include-directories $TMP/work( |$)" "$record"
check test -z "$(find "$TMP/gemini-home" -maxdepth 1 -name '.dispatch-folder.*')"
check grep -qF '"enforcedType":"oauth-personal"' "$record"

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
