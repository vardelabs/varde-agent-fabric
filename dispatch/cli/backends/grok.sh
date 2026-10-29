#!/usr/bin/env bash
# Public Grok adapter. It runs OpenCode's built-in xAI provider under the
# SuperGrok subscription sign-in stored in a dedicated home, never an API key.
set -euo pipefail

: "${DISPATCH_TASK:?DISPATCH_TASK is required}"
: "${DISPATCH_WORKDIR:?DISPATCH_WORKDIR is required}"
: "${DISPATCH_OUTPUT:?DISPATCH_OUTPUT is required}"
: "${DISPATCH_MODEL:?DISPATCH_MODEL is required for the Grok adapter}"
: "${DISPATCH_GROK_HOME:?DISPATCH_GROK_HOME is required for isolated Grok auth}"

backends="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
# shellcheck source=lib/review-isolation.sh
. "$backends/lib/review-isolation.sh"
# shellcheck source=lib/review-steps.sh
. "$backends/lib/review-steps.sh"

require_dedicated_home Grok DISPATCH_GROK_HOME
keep_path_outside_workdir
command -v opencode >/dev/null 2>&1 || fail 'opencode CLI not found on PATH' 127

model="$DISPATCH_MODEL"
[[ "$model" =~ ^xai/[a-z0-9][a-z0-9.-]{0,63}$ ]] || fail 'Grok needs an xai/<model> identifier'
# Config flags cannot confine an edit, so implement waits for an OS sandbox.
[[ "${DISPATCH_KIND:-}" == review ]] || fail 'Grok supports only --kind review'
[[ ! -d "$DISPATCH_OUTPUT" ]] || fail 'DISPATCH_OUTPUT must not be a directory'

# OpenCode treats the folder of the nearest .git or .hg at or above the workdir
# as inside it, so that folder must be the workdir itself, where the checks
# below look.
marker_dir="$workdir_real"
until [[ -e "$marker_dir/.git" || -e "$marker_dir/.hg" || "$marker_dir" == / ]]; do
  marker_dir="$(dirname -- "$marker_dir")"
done
[[ "$marker_dir" == "$workdir_real" || ! ( -e "$marker_dir/.git" || -e "$marker_dir/.hg" ) ]] \
  || fail 'Grok needs the workdir to be the top level of its git or Mercurial checkout'

# OpenCode decides whether a path is inside the workdir from the path text, so a
# link in the reviewed tree would let its read tools reach a file outside it,
# such as the sign-in database at its fixed place in the dedicated home.
# Python resolves a dangling link too, which BSD realpath cannot.
escaping_link="$(find "$workdir_real" -type l -print0 | python3 -c '
import os, sys
root = sys.argv[1].rstrip("/") + "/"
for link in filter(None, sys.stdin.buffer.read().split(b"\0")):
    if not (os.path.realpath(link).decode(errors="replace") + "/").startswith(root):
        print(os.path.relpath(link, sys.argv[1].encode()).decode(errors="replace"))
        break
' "$workdir_real")"
[[ -z "$escaping_link" ]] || fail "Grok refuses a workdir with a link that leaves it: $escaping_link"

signin_db="$dedicated_home_real/.local/share/opencode/opencode.db"
[[ -f "$signin_db" && ! -L "$signin_db" ]] || fail "Grok has no sign-in database in DISPATCH_GROK_HOME; run the sign-in first"

prompt=$'Task for this run:\n\n'"$DISPATCH_TASK"
if [[ -n "${DISPATCH_DIFF:-}" ]]; then
  [[ -f "$DISPATCH_DIFF" && ! -L "$DISPATCH_DIFF" ]] || fail 'Grok diff attachment requires a regular file'
  prompt+=$'\n\n--- BEGIN DIFF ---\n'
  prompt+="$(cat -- "$DISPATCH_DIFF")"
  prompt+=$'\n--- END DIFF ---'
fi
unset DISPATCH_TASK DISPATCH_DIFF

# The scratch holds the run's copy of the sign-in database, and its session. A
# refreshed token is kept on every exit, a timeout's TERM included, once the CLI
# has stopped writing it.
keep_refreshed_signin() {
  [[ -f "$scratch/signin-values.json" ]] || return 0
  python3 "$backends/opencode_signin.py" writeback "$scratch/opencode.db" "$signin_db" \
    "$scratch/signin-values.json" xai \
    || printf 'Grok could not keep a refreshed sign-in; the next run signs in from the stored one\n' >&2
}
before_scratch_removed=keep_refreshed_signin
make_scratch Grok DISPATCH_GROK_HOME grok-dispatch
# The prompt goes on stdin, which OpenCode appends to its message, never in
# process arguments that other users can read.
printf '%s' "$prompt" > "$scratch/prompt"
mkdir -p "$scratch/home" "$scratch/tmp"
python3 "$backends/opencode_signin.py" snapshot "$signin_db" "$scratch/opencode.db" "$scratch/signin-values.json" xai

# The per-run home is empty, so no global config, agent, skill, plugin, or MCP
# server loads, and project config is off, so none loads from the workdir. Only
# the xAI provider is enabled. Every tool is denied but the three that read the
# workdir: a deny list would miss a tool such as code mode's execute, which can
# fetch from the network and move the session's directory, or one a later
# release adds. The plan agent's step limit is a backstop for a review that
# never stops exploring; the final turn below writes the findings.
config='{"enabled_providers":["xai"],"permission":{"*":"deny","read":"allow","glob":"allow","grep":"allow",'
config+='"external_directory":"deny"},"agent":{"plan":{"steps":'"$review_step_limit"'}}}'
# The CLI gets only this environment, so a proxy, NODE_OPTIONS, or an endpoint
# override never reaches it, even when this backend runs outside the dispatcher.
cli_env=(PATH="$PATH" HOME="$scratch/home" TMPDIR="$scratch/tmp" OPENCODE_DB="$scratch/opencode.db"
  OPENCODE_CONFIG_CONTENT="$config" OPENCODE_DISABLE_PROJECT_CONFIG=1 OPENCODE_DISABLE_CHANNEL_DB=1
  OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_FILEWATCHER=1)

report_failed_run() {
  python3 "$backends/opencode_errors.py" "$1" >&2
  # 75 means limit reached to the dispatcher, which reads that from the events.
  exit $((status == 75 ? 1 : status))
}

read_review_deadline
cli_time_limit="$(review_explore_limit)"
[[ -z "$cli_time_limit" ]] || ((cli_time_limit > 0)) || fail "$review_out_of_time" 124
# Without --standalone the run attaches to a shared background service, whose
# configuration was fixed when it started.
run_cli "$scratch/prompt" "$scratch/stdout" "$scratch/stderr" opencode run \
  'Follow the instructions in the input below.' --standalone --format json --agent plan --model "$model"
((status == 0)) || stopped_by_watchdog || report_failed_run "$scratch/stdout"

# A run stopped at its step limit has written no findings, and one stopped at
# its time cut may hold only an early snapshot, so its session is resumed once,
# with every tool denied, and that turn alone must write them. A cut run with no
# session to resume is out of time; an uncut stream that names none goes to the
# caller's parser as it is.
if stopped_by_watchdog || ! python3 "$backends/review_final.py" has-findings "$scratch/stdout"; then
  if ! session="$(python3 "$backends/review_final.py" session "$scratch/stdout")"; then
    ! stopped_by_watchdog || fail "$review_out_of_time" 124
  else
    seconds_left="$(review_seconds_left)"
    [[ -z "$seconds_left" ]] || ((seconds_left >= review_final_min_s)) || fail "$review_out_of_time" 124
    final_config='{"enabled_providers":["xai"],"permission":{"*":"deny"}}'
    for index in "${!cli_env[@]}"; do
      [[ "${cli_env[index]}" != OPENCODE_CONFIG_CONTENT=* ]] || cli_env[index]="OPENCODE_CONFIG_CONTENT=$final_config"
    done
    cli_time_limit="${seconds_left:+$((seconds_left - 5))}"
    run_cli /dev/null "$scratch/final-stdout" "$scratch/final-stderr" opencode run "$review_final_prompt" \
      --session "$session" --standalone --format json --agent plan --model "$model"
    ((status == 0)) || { stopped_by_watchdog && fail "$review_out_of_time" 124; } || report_failed_run "$scratch/final-stdout"
    python3 "$backends/review_final.py" has-findings "$scratch/final-stdout" \
      || fail 'Grok review final turn wrote no findings' 1
    cat -- "$scratch/final-stdout" >> "$scratch/stdout"
  fi
fi
output_dir="$(dirname -- "$DISPATCH_OUTPUT")"
staged="$(mktemp "$output_dir/.grok-output.XXXXXX")"
# mktemp made it 0600; the output keeps that mode.
cat -- "$scratch/stdout" > "$staged"
mv -f -- "$staged" "$DISPATCH_OUTPUT"
