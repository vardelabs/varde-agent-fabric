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

require_dedicated_home Grok DISPATCH_GROK_HOME
keep_path_outside_workdir
command -v opencode >/dev/null 2>&1 || fail 'opencode CLI not found on PATH' 127

model="$DISPATCH_MODEL"
[[ "$model" =~ ^xai/[a-z0-9][a-z0-9.-]{0,63}$ ]] || fail 'Grok needs an xai/<model> identifier'
# Config flags cannot confine an edit, so implement waits for an OS sandbox.
[[ "${DISPATCH_KIND:-}" == review ]] || fail 'Grok supports only --kind review'
[[ ! -d "$DISPATCH_OUTPUT" ]] || fail 'DISPATCH_OUTPUT must not be a directory'

# OpenCode counts the whole enclosing git checkout as inside the workdir, so the
# workdir must be that checkout's top level, where the checks below look.
if command -v git >/dev/null 2>&1; then
  toplevel="$(env -i PATH="$PATH" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c core.fsmonitor= -C "$workdir_real" rev-parse --show-toplevel 2>/dev/null)" || toplevel=""
  [[ -z "$toplevel" || "$(realpath -- "$toplevel")" == "$workdir_real" ]] \
    || fail 'Grok needs the workdir to be the top level of its git checkout'
fi

# OpenCode decides whether a path is inside the workdir from the path text, so a
# link in the reviewed tree would let its read tools reach a file outside it,
# such as the sign-in database at its fixed place in the dedicated home.
while IFS= read -r -d '' link; do
  case "$(realpath -m -- "$link")/" in
    "${workdir_real%/}/"*) ;;
    *) fail "Grok refuses a workdir with a link that leaves it: ${link#"$workdir_real"/}" ;;
  esac
done < <(find "$workdir_real" -type l -print0)

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

# The scratch holds the run's copy of the sign-in database, and its session.
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
# release adds.
config='{"enabled_providers":["xai"],"permission":{"*":"deny","read":"allow","glob":"allow","grep":"allow",'
config+='"external_directory":"deny"}}'
# The CLI gets only this environment, so a proxy, NODE_OPTIONS, or an endpoint
# override never reaches it, even when this backend runs outside the dispatcher.
cli_env=(PATH="$PATH" HOME="$scratch/home" TMPDIR="$scratch/tmp" OPENCODE_DB="$scratch/opencode.db"
  OPENCODE_CONFIG_CONTENT="$config" OPENCODE_DISABLE_PROJECT_CONFIG=1 OPENCODE_DISABLE_CHANNEL_DB=1
  OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_FILEWATCHER=1)

# Without --standalone the run attaches to a shared background service, whose
# configuration was fixed when it started.
run_cli "$scratch/prompt" "$scratch/stdout" "$scratch/stderr" opencode run \
  'Follow the instructions in the input below.' --standalone --format json --agent plan --model "$model"
# A refreshed token is kept even when the review failed.
python3 "$backends/opencode_signin.py" writeback "$scratch/opencode.db" "$signin_db" "$scratch/signin-values.json" xai \
  || printf 'Grok could not keep a refreshed sign-in; the next run signs in from the stored one\n' >&2

if ((status != 0)); then
  python3 "$backends/opencode_errors.py" "$scratch/stdout" >&2
  # 75 means limit reached to the dispatcher, which reads that from the events.
  exit $((status == 75 ? 1 : status))
fi
output_dir="$(dirname -- "$DISPATCH_OUTPUT")"
staged="$(mktemp "$output_dir/.grok-output.XXXXXX")"
# mktemp made it 0600; the output keeps that mode.
cat -- "$scratch/stdout" > "$staged"
mv -f -- "$staged" "$DISPATCH_OUTPUT"
