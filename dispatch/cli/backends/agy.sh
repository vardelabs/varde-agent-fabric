#!/usr/bin/env bash
# Public Antigravity CLI (agy) content writer. It proposes new text for the
# files the caller names and never applies it: agy edits copies in a private
# job folder, and the proposal is built from what changed there.
set -euo pipefail

: "${DISPATCH_TASK:?DISPATCH_TASK is required}"
: "${DISPATCH_WORKDIR:?DISPATCH_WORKDIR is required}"
: "${DISPATCH_OUTPUT:?DISPATCH_OUTPUT is required}"
: "${DISPATCH_MODEL:?DISPATCH_MODEL is required for the agy adapter}"
: "${DISPATCH_AGY_HOME:?DISPATCH_AGY_HOME is required for the agy sign-in}"
: "${DISPATCH_WRITE_PATHS:?DISPATCH_WRITE_PATHS is required for a write}"

backends="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
# shellcheck source=lib/review-isolation.sh
. "$backends/lib/review-isolation.sh"
# shellcheck source=lib/review-steps.sh
. "$backends/lib/review-steps.sh"

require_dedicated_home agy DISPATCH_AGY_HOME
keep_path_outside_workdir
command -v agy >/dev/null 2>&1 || fail 'agy CLI not found on PATH' 127

[[ "${DISPATCH_KIND:-}" == write ]] || fail 'agy supports only --kind write'
[[ ! -d "$DISPATCH_OUTPUT" ]] || fail 'DISPATCH_OUTPUT must not be a directory'
model="$DISPATCH_MODEL"
[[ "$model" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail 'model identifier is invalid'
timeout_s="${DISPATCH_AGY_TIMEOUT_SECONDS:-600}"
[[ "$timeout_s" =~ ^[1-9][0-9]{0,3}$ ]] && ((timeout_s <= 3600)) || fail 'DISPATCH_AGY_TIMEOUT_SECONDS must be from 1 to 3600'

make_scratch agy DISPATCH_AGY_HOME agy-dispatch
job="$scratch/job"
mkdir -m 0700 "$job" "$scratch/tmp"
python3 "$backends/agy_write.py" stage "$workdir_real" "$job" "$scratch/manifest.json"

# The prompt goes on stdin, never in process arguments that other users can read.
{
  printf 'You are a content writer. Edit only these files in the current folder; a file that is missing may be created:\n'
  while IFS= read -r path; do [[ -z "$path" ]] || printf -- '- %s\n' "$path"; done <<< "$DISPATCH_WRITE_PATHS"
  printf '\nDo not run commands, fetch URLs, or touch any other file. Your edits are returned for review and applied by someone else.\n\nTask:\n\n%s\n' "$DISPATCH_TASK"
} > "$scratch/prompt"
unset DISPATCH_TASK

# Session-only settings, never saved to the sign-in home. accept-edits lets a
# headless run edit files in its workspace, the job folder; request-review keeps
# every other tool on ask, which a headless run refuses and reports in
# denied_actions. The deny rules name commands, MCP tools and URL reads outright,
# so a broader allow in the home settings cannot reach them.
cat > "$scratch/settings.json" <<'EOF'
{
  "agentMode": "accept-edits",
  "toolPermission": "request-review",
  "allowNonWorkspaceAccess": false,
  "permissions": {"deny": ["command(*)", "mcp(*)", "read_url(*)", "execute_url(*)"]}
}
EOF

# The sign-in home is the real one, so a refreshed token saves in place. One run
# at a time holds it, so two refreshes never race.
exec {home_lock}<"$dedicated_home_real"
flock -w "$timeout_s" "$home_lock" || fail 'agy sign-in home is busy'

# The CLI gets only this environment, so a provider key, a proxy, or an endpoint
# override in the caller's environment never reaches it.
cli_env=(PATH="$PATH" HOME="$dedicated_home_real" TMPDIR="$scratch/tmp" AGY_CLI_DISABLE_AUTO_UPDATE=1)
# agy stops itself at --print-timeout; the watchdog is the backstop for a CLI that does not.
cli_time_limit="$((timeout_s + review_stop_grace_s))"
DISPATCH_WORKDIR="$job"
run_cli "$scratch/prompt" "$scratch/stdout" "$scratch/stderr" agy --output-format json --model "$model" \
  --mode accept-edits --disable-slash-commands --settings "$scratch/settings.json" \
  --print-timeout "${timeout_s}s" --log-file "$scratch/agy.log"
timed_out=0
! stopped_by_watchdog || timed_out=1
result_status=0
python3 "$backends/agy_write.py" result "$scratch/stdout" "$status" "$timed_out" "$scratch/manifest.json" \
  "$job" "$model" "$DISPATCH_OUTPUT" || result_status=$?
exit "$result_status"
