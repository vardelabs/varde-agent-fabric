#!/usr/bin/env bash
# Public Gemini CLI adapter. It runs only under the CLI's own Google sign-in,
# stored in a dedicated home; an API key route is refused by settings.
set -euo pipefail

: "${DISPATCH_TASK:?DISPATCH_TASK is required}"
: "${DISPATCH_WORKDIR:?DISPATCH_WORKDIR is required}"
: "${DISPATCH_OUTPUT:?DISPATCH_OUTPUT is required}"
: "${DISPATCH_MODEL:?DISPATCH_MODEL is required for the Gemini adapter}"
: "${DISPATCH_GEMINI_HOME:?DISPATCH_GEMINI_HOME is required for isolated Gemini auth}"

fail() { printf '%s\n' "$1" >&2; exit "${2:-2}"; }

[[ "$DISPATCH_GEMINI_HOME" = /* && -d "$DISPATCH_GEMINI_HOME" ]] \
  || fail 'DISPATCH_GEMINI_HOME must be an existing absolute directory'
caller_home="${HOME:-}"
[[ -n "$caller_home" ]] || fail 'Gemini requires a non-empty caller home'
caller_home_real="$(realpath -- "$caller_home")"
gemini_home_real="$(realpath -- "$DISPATCH_GEMINI_HOME")"
[[ "$caller_home_real" != / && "$gemini_home_real" != / ]] || fail 'Gemini homes must not be the filesystem root'
caller_home_real="${caller_home_real%/}"
gemini_home_real="${gemini_home_real%/}"
case "$gemini_home_real/" in
  "$caller_home_real/"*) fail 'DISPATCH_GEMINI_HOME must be outside the caller home' ;;
esac
case "$caller_home_real/" in
  "$gemini_home_real/"*) fail 'DISPATCH_GEMINI_HOME must not contain the caller home' ;;
esac

command -v gemini >/dev/null 2>&1 || fail 'gemini CLI not found on PATH' 127

model="$DISPATCH_MODEL"
[[ "$model" != *[[:space:]]* && "$model" != *[![:print:]]* && "$model" != -* ]] || fail 'model identifier is invalid'

# The workdir never supplies Gemini settings, because they could pick another
# auth type. A review runs inside it untrusted, which drops its settings and
# extensions and leaves headless mode refusing every tool that needs approval.
# An edit needs a trusted folder, so implement runs in an empty trusted folder
# and reaches the worktree as an included directory.
case "${DISPATCH_KIND:-}" in
  review) approval_mode=default; trust_folder=false ;;
  implement|passthrough)
    [[ "${DISPATCH_GEMINI_ALLOW_WRITE:-}" == 1 ]] \
      || fail "Gemini implementation requires DISPATCH_GEMINI_ALLOW_WRITE=1 (kind: $DISPATCH_KIND)"
    approval_mode=auto_edit; trust_folder=true
    ;;
  *) fail 'unsupported Gemini dispatch kind' ;;
esac
[[ ! -d "$DISPATCH_OUTPUT" ]] || fail 'DISPATCH_OUTPUT must not be a directory'

# The CLI runs input that starts with / as a command, and an untrusted workdir's
# commands still load, so the input never starts with the task itself.
prompt=$'Task for this run:\n\n'"$DISPATCH_TASK"
if [[ -n "${DISPATCH_DIFF:-}" ]]; then
  [[ -f "$DISPATCH_DIFF" && ! -L "$DISPATCH_DIFF" ]] || fail 'Gemini diff attachment requires a regular file'
  prompt+=$'\n\n--- BEGIN DIFF ---\n'
  prompt+="$(cat -- "$DISPATCH_DIFF")"
  prompt+=$'\n--- END DIFF ---'
fi
unset DISPATCH_TASK DISPATCH_DIFF

# mktemp -d makes the scratch directory 0700. It holds links to the sign-in, so
# a signal exits through the EXIT trap too and it never outlives the run.
scratch="$(mktemp -d "${TMPDIR:-/tmp}/gemini-dispatch.XXXXXX")"
implement_folder=""
trap 'rm -rf -- "$scratch" ${implement_folder:+"$implement_folder"}' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# The prompt goes on stdin, never in process arguments that other users can read.
printf '%s' "$prompt" > "$scratch/prompt"
# The CLI dumps the whole chat to its temp directory on an API error and never
# removes it, so its temp directory is inside the scratch.
mkdir "$scratch/tmp"

# A root-owned system policy directory ranks above every policy the adapter can
# set, and the CLI reads it from a fixed path, so the run refuses while one holds
# a policy.
for policy_dir in /etc/gemini-cli/policies "/Library/Application Support/GeminiCli/policies" \
  ${DISPATCH_GEMINI_EXTRA_POLICY_DIR:+"$DISPATCH_GEMINI_EXTRA_POLICY_DIR"}; do
  ! compgen -G "$policy_dir/*.toml" >/dev/null || fail "Gemini refuses to run under the system policies in $policy_dir"
done

# A per-run home carries the adapter's own user settings. A root-owned system
# settings file would rank above them, and system defaults could add MCP servers
# they never name, so both system paths point at files in the private scratch
# that never exist. An untrusted or empty folder adds no workspace settings
# above the user settings, which are then final. Only the sign-in files are linked in
# from the dedicated home; the CLI's session state stays in the per-run home and
# goes with it. A refreshed token is not written back: the CLI replaces the link
# with a new file, so each run refreshes from the stored sign-in.
# ignoreLocalEnv stops the CLI reading a .env in or above the folder for its auth
# variables, which it otherwise does even when the folder is untrusted. The web
# tools are excluded because the read-only policy still allows them, and text in
# a reviewed diff could steer workdir content into a search or a fetch. Plan mode
# is off and its tools excluded, because leaving it headless in a trusted folder
# switches the run to yolo, which allows the shell; the shell is excluded too.
run_home="$scratch/home"
mkdir -p "$run_home/.gemini"
for entry in "$DISPATCH_GEMINI_HOME/.gemini"/*; do
  case "$(basename -- "$entry")" in
    oauth_creds.json|google_accounts.json|installation_id|*credential*) ln -s -- "$entry" "$run_home/.gemini/" ;;
  esac
done
cat > "$run_home/.gemini/settings.json" <<'EOF'
{
  "security": {
    "auth": {"selectedType": "oauth-personal", "enforcedType": "oauth-personal"},
    "folderTrust": {"enabled": false}
  },
  "general": {"enableAutoUpdate": false, "enableAutoUpdateNotification": false, "plan": {"enabled": false}},
  "advanced": {"ignoreLocalEnv": true},
  "tools": {
    "exclude": ["google_web_search", "web_fetch", "run_shell_command", "enter_plan_mode", "exit_plan_mode"],
    "disableLLMCorrection": true
  },
  "experimental": {"enableAgents": false}
}
EOF

# The CLI gets only this environment, so a proxy, NODE_OPTIONS, or an endpoint
# override never reaches it, even when this backend runs outside the dispatcher.
# With folder trust off, an untrusted headless run starts instead of refusing.
cli_env=(PATH="$PATH" HOME="$run_home" GEMINI_CLI_HOME="$run_home" TMPDIR="$scratch/tmp"
  GEMINI_CLI_SYSTEM_SETTINGS_PATH="$scratch/no-system-settings.json"
  GEMINI_CLI_SYSTEM_DEFAULTS_PATH="$scratch/no-system-defaults.json"
  GEMINI_CLI_TRUST_WORKSPACE="$trust_folder")

# In a trusted folder the CLI loads .gemini/.env from the folder and every parent,
# unfiltered, before ignoreLocalEnv applies. So the implement folder lives in the
# dedicated home, and the run refuses when any parent could let another user plant
# that file or already holds one: a parent owned by another user, or one that
# group or others can write.
require_safe_parents() {
  local dir="$1" mode owner uid
  uid="$(id -u)"
  while :; do
    [[ ! -e "$dir/.gemini/.env" ]] || fail "Gemini implementation refuses $dir/.gemini/.env above its folder"
    owner="$(stat -c '%u' -- "$dir" 2>/dev/null || stat -f '%u' -- "$dir")"
    [[ "$owner" == "$uid" || "$owner" == 0 ]] \
      || fail "Gemini implementation needs every parent of its folder owned by this user or root: $dir"
    mode="$(stat -c '%a' -- "$dir" 2>/dev/null || stat -f '%Lp' -- "$dir")"
    (( (8#$mode & 8#022) == 0 )) || fail "Gemini implementation needs every parent of its folder closed to group and others: $dir"
    [[ "$dir" != / ]] || break
    dir="$(dirname -- "$dir")"
  done
}

includable_path() {
  local LC_ALL=C
  [[ "$1" != *[![:print:]]* && "$1" != *,* && "$1" != *' ' && "$1" != *' /'* ]]
}

if [[ "$trust_folder" == true ]]; then
  # The CLI splits --include-directories on commas, trims each part of Unicode
  # whitespace, and resolves .. without following links. So it gets the resolved
  # workdir, and only a printable ASCII path it would not split or trim. The x
  # keeps a trailing newline that command substitution would otherwise drop.
  workdir_real="$(realpath -- "$DISPATCH_WORKDIR" && printf x)"
  workdir_real="${workdir_real%$'\n'x}"
  includable_path "$workdir_real" \
    || fail 'Gemini implementation needs a printable ASCII workdir path with no comma and no name ending in a space'
  implement_folder="$(mktemp -d "$gemini_home_real/.dispatch-folder.XXXXXX")"
  require_safe_parents "$implement_folder"
  folder="$implement_folder"
  include=(--include-directories "$workdir_real")
else
  folder="$DISPATCH_WORKDIR"
  include=()
fi

status=0
(cd -- "$folder" && exec env -i "${cli_env[@]}" gemini \
  --prompt 'Follow the instructions above.' --output-format json \
  --model "$model" --approval-mode "$approval_mode" "${include[@]}") \
  <"$scratch/prompt" >"$scratch/stdout" 2>"$scratch/stderr" || status=$?
python3 "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/gemini_result.py" \
  "$scratch/stdout" "$scratch/stderr" "$model" "$status" "$DISPATCH_OUTPUT"
