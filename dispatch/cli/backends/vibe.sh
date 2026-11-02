#!/usr/bin/env bash
# Public Mistral Vibe adapter. It runs the Vibe CLI in headless mode under an
# API key kept in a dedicated home, with a per-run config the adapter writes.
set -euo pipefail

: "${DISPATCH_TASK:?DISPATCH_TASK is required}"
: "${DISPATCH_WORKDIR:?DISPATCH_WORKDIR is required}"
: "${DISPATCH_OUTPUT:?DISPATCH_OUTPUT is required}"
: "${DISPATCH_MODEL:?DISPATCH_MODEL is required for the Vibe adapter}"
: "${DISPATCH_VIBE_HOME:?DISPATCH_VIBE_HOME is required for isolated Vibe auth}"

backends="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
# shellcheck source=lib/review-isolation.sh
. "$backends/lib/review-isolation.sh"
# shellcheck source=lib/review-steps.sh
. "$backends/lib/review-steps.sh"

require_dedicated_home Vibe DISPATCH_VIBE_HOME
keep_path_outside_workdir
command -v vibe >/dev/null 2>&1 || fail 'vibe CLI not found on PATH' 127

model="$DISPATCH_MODEL"
[[ "$model" =~ ^mistral/([A-Za-z0-9][A-Za-z0-9._-]{0,63})$ ]] || fail 'Vibe needs a mistral/<model> identifier'
provider_model="${BASH_REMATCH[1]}"
# Approval policy is all that stops an edit, so implement waits for an OS sandbox.
[[ "${DISPATCH_KIND:-}" == review ]] || fail 'Vibe supports only --kind review'
[[ ! -d "$DISPATCH_OUTPUT" ]] || fail 'DISPATCH_OUTPUT must not be a directory'

key_file="$dedicated_home_real/.env"
[[ -f "$key_file" && ! -L "$key_file" ]] || fail 'Vibe has no API key file (.env) in DISPATCH_VIBE_HOME; write the key first'

prompt=$'Task for this run:\n\n'"$DISPATCH_TASK"
if [[ -n "${DISPATCH_DIFF:-}" ]]; then
  [[ -f "$DISPATCH_DIFF" && ! -L "$DISPATCH_DIFF" ]] || fail 'Vibe diff attachment requires a regular file'
  prompt+=$'\n\n--- BEGIN DIFF ---\n'
  prompt+="$(cat -- "$DISPATCH_DIFF")"
  prompt+=$'\n--- END DIFF ---'
fi
unset DISPATCH_TASK DISPATCH_DIFF

make_scratch Vibe DISPATCH_VIBE_HOME vibe-dispatch
# The prompt goes in a file, never in process arguments that other users can read.
printf '%s' "$prompt" > "$scratch/prompt"
run_home="$scratch/vibe-home"
mkdir -p "$scratch/home" "$scratch/tmp" "$scratch/export" "$run_home"
ln -s -- "$key_file" "$run_home/.env"

# The per-run VIBE_HOME holds only this config and the key link, so no agent,
# skill, MCP server, connector, or plugin of the operator's loads. The workdir is
# never trusted, so its .vibe/ config is ignored; a trusted one could change the
# default agent. In headless mode the ask agent denies every tool whose
# permission is ask, which covers bash, edit, write_file, and the web tools,
# also when called from run_typescript. Each is set to never as well, so a later
# release that prompts differently still cannot run one. read_file stays, and
# outside_workdir = deny refuses any path, a link included, that resolves outside
# the workdir.
cat > "$run_home/config.toml" <<EOF
active_model = "review"
default_agent = "ask"
strict_config = true
outside_workdir = "deny"
enable_auto_approve = false
bypass_tool_permissions = false
enable_mcp = false
enable_connectors = false
enable_subagents = false
enable_background_processes = false
include_project_context = false
enable_telemetry = false
enable_update_checks = false
enable_auto_update = false

[session_logging]
enabled = false

[[providers]]
name = "mistral"
api_base = "https://api.mistral.ai/v1"
api_key_env_var = "MISTRAL_API_KEY"
backend = "mistral"

[models.review]
name = "$provider_model"
provider = "mistral"
alias = "review"

[tools.bash]
permission = "never"
[tools.git_bash]
permission = "never"
[tools.powershell]
permission = "never"
[tools.edit]
permission = "never"
[tools.write_file]
permission = "never"
[tools.web_fetch]
permission = "never"
[tools.web_search]
permission = "never"
[tools.ask_user_question]
permission = "never"
EOF

# The CLI gets only this environment, so a proxy, an endpoint override, or a key
# in the caller's environment never reaches it, even outside the dispatcher.
cli_env=(PATH="$PATH" HOME="$scratch/home" TMPDIR="$scratch/tmp" VIBE_HOME="$run_home")

read_review_deadline
cli_time_limit=""
time_limit_args=()
if seconds_left="$(review_seconds_left)" && [[ -n "$seconds_left" ]]; then
  ((seconds_left > review_stop_grace_s)) || review_out_of_time_exit
  # Vibe stops itself at --time-limit and still writes its export; the watchdog
  # is the backstop for a CLI that does not.
  time_limit_args=(--time-limit "$((seconds_left - review_stop_grace_s))")
  cli_time_limit="$seconds_left"
fi
run_cli /dev/null "$scratch/stdout" "$scratch/stderr" vibe --prompt-file "$scratch/prompt" \
  --output json --agent ask --output-dir "$scratch/export" "${time_limit_args[@]}"
! stopped_by_watchdog || review_out_of_time_exit
result_status=0
python3 "$backends/vibe_result.py" "$scratch/stdout" "$scratch/export/export.json" "$status" "$DISPATCH_OUTPUT" \
  || result_status=$?
((result_status != 124)) || review_out_of_time_exit
exit "$result_status"
