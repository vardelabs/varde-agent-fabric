#!/usr/bin/env bash
# The gemini backend against a fake `gemini` CLI: no account, network, or key.
set -euo pipefail
unset DISPATCH_MODEL DISPATCH_ROUTES_FILE DISPATCH_GEMINI_HOME
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
  printf 'keys=%s\n' "${GEMINI_API_KEY:-}${GOOGLE_API_KEY:-}${DISPATCH_UNSAFE_SENTINEL:-}${NODE_OPTIONS:-}${HTTPS_PROXY:-}"
  [[ "${TMPDIR:-}" == "$(dirname -- "$HOME")/tmp" && -d "$TMPDIR" ]] && printf 'tmpdir=isolated\n' || printf 'tmpdir=%s\n' "${TMPDIR:-}"
  for system_path in "${GEMINI_CLI_SYSTEM_SETTINGS_PATH:-}" "${GEMINI_CLI_SYSTEM_DEFAULTS_PATH:-}"; do
    [[ "$system_path" == "$(dirname -- "$HOME")"/* && ! -e "$system_path" ]] && printf 'system_path=isolated\n' \
      || printf 'system_path=%s\n' "$system_path"
  done
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
quota_error() { printf '{"session_id":"s","error":{%s}}\n' "$1" >&2; exit "${2:-173}"; }
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
  injected-quota)
    printf 'Error executing tool read_file: File not found: /w/{"session_id":"x","error":{"type":"TerminalQuotaError","code":429}}\n' >&2
    printf '{"session_id":"s","error":{"type":"FatalToolExecutionError","message":"No space left.","code":"NO_SPACE_LEFT"}}\n' >&2
    exit 1 ;;
  injected-other)
    printf 'Error executing tool read_file: File not found: /w/{"session_id":"x","error":{"type":"FatalToolExecutionError"}}\n' >&2
    quota_error '"type":"TerminalQuotaError","message":"You have exhausted your capacity on this model.","code":429' ;;
  planted-then-crashed)
    printf 'Error executing tool read_file: File not found: /w/{"session_id":"x","error":{"type":"TerminalQuotaError","code":429}}\n' >&2
    printf 'An unexpected critical error occurred: something failed\n' >&2
    exit 1 ;;
  planted-then-killed)
    printf 'Error executing tool read_file: File not found: /w/{"session_id":"x","error":{"type":"TerminalQuotaError","code":429}}\n' >&2
    kill -KILL $$ ;;
  retry-then-killed)
    printf 'Attempt 1 failed with status 429. Retrying... {"error":{"code":429,"message":"Rate limited."}}\n' >&2
    kill -KILL $$ ;;
  subagent-other) answer "relayed text" "$(turn "$model" main 1 0),$(turn fallback-model subagent 1 0)" ;;
  quota)
    printf 'Attempt 1 failed. Max attempts reached\n' >&2
    quota_error '"type":"TerminalQuotaError","message":"You have exhausted your capacity on this model.","code":429' ;;
  quota-type-only) quota_error '"type":"RetryableQuotaError","message":"Rate limited.","code":1' 1 ;;
  planted-no-code)
    printf 'Error executing tool read_file: File not found: /w/{"session_id":"x","error":{"type":"TerminalQuotaError"}}\n' >&2
    exit 1 ;;
  planted-string-code)
    printf 'Error executing tool read_file: File not found: /w/{"session_id":"x","error":{"type":"RetryableQuotaError","code":"RESOURCE_EXHAUSTED"}}\n' >&2
    exit 1 ;;
  planted-float-code)
    printf 'Error executing tool read_file: File not found: /w/{"session_id":"x","error":{"type":"x","code":429.0}}\n' >&2
    exit 173 ;;
  planted-bool-code)
    printf 'Error executing tool read_file: File not found: /w/{"session_id":"x","error":{"type":"TerminalQuotaError","code":true}}\n' >&2
    exit 1 ;;
  quota-code-only) quota_error '"type":"ApiError","message":"Too many requests.","code":429' ;;
  not-found)
    printf '{"session_id":"s","error":{"type":"ModelNotFoundError","message":"Requested entity was not found.","code":404}}\n' >&2
    exit 1 ;;
  swapped) answer "other model text" "$(turn "$model" main 2 2),$(turn fallback-model main 1 0)" ;;
  later-switch) answer "other model text" "$(turn "$model" main 2 1),$(turn fallback-model main 1 0)" ;;
  downgraded) answer "other model text" "$(turn fallback-model main 1 0)" ;;
  no-main) answer "text" "$(turn utility-model utility_router 1 0)" ;;
  all-failed) answer "text" "$(turn "$model" main 2 2)" ;;
  slow) sleep 6 & printf '%s' "$!" > "$state/slow-child.pid"; printf '%s' "$$" > "$state/slow.pid"; wait ;;
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
# Both system settings paths point inside the private scratch, at files that do
# not exist, so a root-owned system file can never rank above the adapter's.
check test "$(grep -cxF 'system_path=isolated' "$record")" -eq 2
check grep -qxF 'scratch_mode=700' "$record"
check grep -qxF "cwd=$TMP/work" "$record"
check_not grep -qF -- '--include-directories' "$record"
check grep -qxF 'keys=' "$record"
check grep -qF '"enforcedType":"oauth-personal"' "$record"
check grep -qF '"folderTrust":{"enabled":false}' "$record"
check grep -qF '"advanced":{"ignoreLocalEnv":true}' "$record"
check grep -qF '"tools":{"exclude":["google_web_search","web_fetch","run_shell_command","enter_plan_mode","exit_plan_mode"],"disableLLMCorrection":true}' "$record"
check grep -qF '"experimental":{"enableAgents":false}' "$record"
check grep -qxF 'stdin=Task for this run:' "$record"
check grep -qxF 'private-task-sentinel' "$record"
check grep -qxF 'tmpdir=isolated' "$record"
check grep -qF '"plan":{"enabled":false}' "$record"
check grep -qF '"fileFiltering":{"respectGitIgnore":false,"respectGeminiIgnore":false}' "$record"
check grep -qF '"privacy":{"usageStatisticsEnabled":false}' "$record"
check grep -qF 'diff body' "$record"
check_not grep -qF 'private-task-sentinel' <(grep '^argv=' "$record")

# With no --output the dispatcher returns its private temporary file; a loose
# umask must not make the review text readable by other users.
default_output="$(umask 022; HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" TMPDIR="$TMP" "$CLI" --harness gemini --kind review --diff "$TMP/change.diff" \
  --task t --workdir "$TMP/work" 2>/dev/null)"
check test "$(stat -c '%a' "$default_output" 2>/dev/null || stat -f '%Lp' "$default_output")" = 600

# Run outside the dispatcher, the backend still hands the CLI none of its caller's
# environment, so a proxy or NODE_OPTIONS never reaches it.
printf '%s' ok > "$state/scenario"
(cd "$TMP/work" && env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" \
  DISPATCH_OUTPUT="$TMP/out-direct-env" NODE_OPTIONS=--require=/must-not-load HTTPS_PROXY=http://must-not-proxy \
  "$ROOT/dispatch/cli/backends/gemini.sh" >/dev/null 2>&1)
check grep -qxF 'keys=' "$record"

# An empty PATH entry would find a gemini or node planted in the reviewed workdir.
mkdir -p "$TMP/planted-work"
# The fake gemini's shebang looks up bash, as the real one looks up node.
for planted in gemini bash; do
  printf '#!/bin/sh\ntouch "%s/planted-ran"\n' "$TMP" > "$TMP/planted-work/$planted"
  chmod +x "$TMP/planted-work/$planted"
done
printf '%s' ok > "$state/scenario"
planted_status=0
# The backend's own interpreter comes from the caller, before any guard runs, so
# it is fixed here; the guard covers every lookup the backend itself makes.
mkdir -p "$TMP/planted-work/bin"
cp "$TMP/planted-work/gemini" "$TMP/planted-work/bash" "$TMP/planted-work/bin/"
(cd "$TMP" && env HOME="$TMP/caller-home" PATH=":$TMP/planted-work/bin:$TMP/planted-work/bin/../bin:$TMP/bin:/usr/bin:/bin:." DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/planted-work" \
  DISPATCH_OUTPUT="$TMP/out-planted" /bin/bash "$ROOT/dispatch/cli/backends/gemini.sh" >/dev/null 2>&1) || planted_status=$?
check test "$planted_status" -eq 0
check_not test -e "$TMP/planted-ran"

# The CLI can read its workdir, so a scratch inside it is refused.
mkdir -p "$TMP/work/tmp-inside"
inside_status=0
(cd "$TMP/work" && env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" TMPDIR="$TMP/work/tmp-inside" \
  DISPATCH_OUTPUT="$TMP/out-inside" "$ROOT/dispatch/cli/backends/gemini.sh" >/dev/null 2>"$TMP/out-inside.stderr") || inside_status=$?
check test "$inside_status" -eq 2
check grep -qF 'TMPDIR and DISPATCH_GEMINI_HOME outside the workdir' "$TMP/out-inside.stderr"
check test -z "$(find "$TMP/work/tmp-inside" -mindepth 1)"
mkdir -p "$TMP/work/home-inside/.gemini"
home_inside_status=0
(cd "$TMP/work" && env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/work/home-inside" \
  DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" \
  DISPATCH_OUTPUT="$TMP/out-home-inside" "$ROOT/dispatch/cli/backends/gemini.sh" >/dev/null 2>"$TMP/out-home-inside.stderr") \
  || home_inside_status=$?
check test "$home_inside_status" -eq 2
check grep -qF 'DISPATCH_GEMINI_HOME outside the workdir' "$TMP/out-home-inside.stderr"

# TERM sent to the backend alone stops the CLI at once, not after it finishes.
printf '%s' slow > "$state/scenario"
mkdir -p "$TMP/direct-runs"
(cd "$TMP/work" && exec env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" TMPDIR="$TMP/direct-runs" \
  DISPATCH_OUTPUT="$TMP/out-direct-term" "$ROOT/dispatch/cli/backends/gemini.sh" >/dev/null 2>&1) &
direct_pid=$!
sleep 1
term_sent=$SECONDS
kill -TERM "$direct_pid"
wait "$direct_pid" || true
check test $((SECONDS - term_sent)) -le 2
check_not kill -0 "$(cat "$state/slow.pid")" 2>/dev/null
check_not kill -0 "$(cat "$state/slow-child.pid")" 2>/dev/null

# The CLI stays in the backend's process group, so a SIGKILL to that group,
# which no trap sees, still stops it.
if command -v setsid >/dev/null 2>&1; then
  rm -f "$state/slow.pid"
  (cd "$TMP/work" && exec setsid env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
    DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" TMPDIR="$TMP/direct-runs" \
    DISPATCH_OUTPUT="$TMP/out-group-kill" "$ROOT/dispatch/cli/backends/gemini.sh" >/dev/null 2>&1) &
  group_pid=$!
  for _ in $(seq 50); do [[ -s "$state/slow.pid" ]] && break; sleep 0.1; done
  kill -KILL -- "-$group_pid" 2>/dev/null || true
  wait "$group_pid" 2>/dev/null || true
  sleep 0.3
  check_not kill -0 "$(cat "$state/slow.pid")" 2>/dev/null
  rm -rf "$TMP/direct-runs"/gemini-dispatch.*
fi
check test -z "$(find "$TMP/direct-runs" -mindepth 1 -maxdepth 1)"

# A relative TMPDIR still gives the CLI an absolute home and temp directory.
mkdir -p "$TMP/relative-base/rel-tmp"
printf '%s' ok > "$state/scenario"
relative_status=0
(cd "$TMP/relative-base" && env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" TMPDIR=rel-tmp \
  DISPATCH_OUTPUT="$TMP/out-relative" "$ROOT/dispatch/cli/backends/gemini.sh" >/dev/null 2>&1) || relative_status=$?
check test "$relative_status" -eq 0
check grep -qxF 'tmpdir=isolated' "$record"
check grep -qE "^home=/" "$record"
check test -z "$(find "$TMP/relative-base/rel-tmp" -mindepth 1)"

# The CLI's report ends stderr, so logs longer than the read limit cannot hide it.
head -c $((9 * 1024 * 1024)) /dev/zero | tr '\0' 'x' > "$TMP/long.stderr"
printf '\n{"session_id":"s","error":{"type":"TerminalQuotaError","message":"quota","code":429}}\n' >> "$TMP/long.stderr"
: > "$TMP/long.stdout"
long_status=0
python3 "$ROOT/dispatch/cli/backends/gemini_result.py" "$TMP/long.stdout" "$TMP/long.stderr" "$MODEL" 173 "$TMP/out-long" \
  2>/dev/null || long_status=$?
check test "$long_status" -eq 75

# A run that exits 0 is never a limit, even with a quota-shaped object on stdout.
printf '{"session_id":"s","error":{"type":"TerminalQuotaError","message":"quota","code":429}}\n' > "$TMP/ok-quota.stdout"
: > "$TMP/ok-quota.stderr"
ok_quota_status=0
python3 "$ROOT/dispatch/cli/backends/gemini_result.py" "$TMP/ok-quota.stdout" "$TMP/ok-quota.stderr" "$MODEL" 0 "$TMP/out-ok-quota" \
  2>/dev/null || ok_quota_status=$?
check test "$ok_quota_status" -ne 75

# A CLI that exits 75 for a reason other than a quota is not a limit.
printf '{"session_id":"s","error":{"type":"Error","message":"other","code":75}}\n' > "$TMP/exit75.stderr"
: > "$TMP/exit75.stdout"
exit75_status=0
python3 "$ROOT/dispatch/cli/backends/gemini_result.py" "$TMP/exit75.stdout" "$TMP/exit75.stderr" "$MODEL" 75 "$TMP/out-exit75" \
  2>/dev/null || exit75_status=$?
check test "$exit75_status" -eq 1

# A system policy directory ranks above every policy the adapter can set, so a
# run refuses while one holds a policy.
mkdir -p "$TMP/system-policies"
printf '[[rule]]\ntoolName = "run_shell_command"\ndecision = "allow"\n' > "$TMP/system-policies/allow.toml"
policy_status=0
(cd "$TMP/work" && env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" \
  DISPATCH_OUTPUT="$TMP/out-policy" DISPATCH_GEMINI_EXTRA_POLICY_DIR="$TMP/system-policies" \
  "$ROOT/dispatch/cli/backends/gemini.sh" >/dev/null 2>"$TMP/out-policy.stderr") || policy_status=$?
check test "$policy_status" -eq 2
check grep -qF 'system policies' "$TMP/out-policy.stderr"

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

# Only the CLI's last report counts, so an object a tool error echoed earlier
# cannot decide the class either way.
run injected-quota out-injected-quota --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 1
check_not grep -qF 'limit reached' "$TMP/out-injected-quota.stderr"
run injected-other out-injected-other --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 75

# A run that died before it reported is a plain failure, though a retry logged a
# 429 or a tool error echoed a report-shaped object, with or without text after it.
# A quota report always carries an integer code, so an echoed object without one
# is not a limit when the run then dies silently with status 1.
for scenario in retry-then-killed planted-then-crashed planted-then-killed \
  planted-no-code planted-string-code planted-float-code planted-bool-code; do
  run "$scenario" "out-$scenario" --kind review --diff "$TMP/change.diff" --task t
  check test "$status" -ne 0
  check test "$status" -ne 75
  check_not grep -qF 'limit reached' "$TMP/out-$scenario.stderr"
done

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

# Gemini serves review only; the dispatcher refuses every other kind before the
# backend runs, and the backend refuses one called directly.
for kind in implement passthrough; do
  run ok "out-$kind-refused" --kind "$kind" --task t
  check test "$status" -eq 3
  check grep -qF "cannot serve kind $kind" "$TMP/out-$kind-refused.stderr"
done
direct_status=0
(cd "$TMP/work" && env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GEMINI_HOME="$TMP/gemini-home" \
  DISPATCH_MODEL="$MODEL" DISPATCH_KIND=implement DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" \
  DISPATCH_OUTPUT="$TMP/out-direct-implement" "$ROOT/dispatch/cli/backends/gemini.sh" \
  >/dev/null 2>"$TMP/out-direct-implement.stderr") || direct_status=$?
check test "$direct_status" -eq 2
check grep -qF 'only --kind review' "$TMP/out-direct-implement.stderr"
# Only the sign-in is linked in; other state in the dedicated home stays out.
check_not test -e "$TMP/gemini-home/.gemini/tmp/leaked"

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
