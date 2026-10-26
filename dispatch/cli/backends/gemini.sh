#!/usr/bin/env bash
# Public Gemini CLI adapter. It runs only under the CLI's own Google sign-in,
# stored in a dedicated home; an API key route is refused by settings.
set -euo pipefail

: "${DISPATCH_TASK:?DISPATCH_TASK is required}"
: "${DISPATCH_WORKDIR:?DISPATCH_WORKDIR is required}"
: "${DISPATCH_OUTPUT:?DISPATCH_OUTPUT is required}"
: "${DISPATCH_MODEL:?DISPATCH_MODEL is required for the Gemini adapter}"
: "${DISPATCH_GEMINI_HOME:?DISPATCH_GEMINI_HOME is required for isolated Gemini auth}"

LIMIT_REACHED_STATUS=75

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

# A review runs with the workdir untrusted: its settings, env file, and
# extensions stay unloaded, and headless mode refuses every tool that needs
# approval, so the run is read-only. Edits need a trusted workdir.
case "${DISPATCH_KIND:-}" in
  review) approval_mode=default; trust_workdir=false ;;
  implement|passthrough)
    [[ "${DISPATCH_GEMINI_ALLOW_WRITE:-}" == 1 ]] \
      || fail "Gemini implementation requires DISPATCH_GEMINI_ALLOW_WRITE=1 (kind: $DISPATCH_KIND)"
    approval_mode=auto_edit; trust_workdir=true
    ;;
  *) fail 'unsupported Gemini dispatch kind' ;;
esac
[[ ! -d "$DISPATCH_OUTPUT" ]] || fail 'DISPATCH_OUTPUT must not be a directory'

prompt="$DISPATCH_TASK"
if [[ -n "${DISPATCH_DIFF:-}" ]]; then
  [[ -f "$DISPATCH_DIFF" && ! -L "$DISPATCH_DIFF" ]] || fail 'Gemini diff attachment requires a regular file'
  prompt+=$'\n\n--- BEGIN DIFF ---\n'
  prompt+="$(cat -- "$DISPATCH_DIFF")"
  prompt+=$'\n--- END DIFF ---'
fi
unset DISPATCH_TASK DISPATCH_DIFF

scratch="$(mktemp -d "${TMPDIR:-/tmp}/gemini-dispatch.XXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT
# The prompt goes on stdin, never in process arguments that other users can read.
printf '%s' "$prompt" > "$scratch/prompt"
# System settings take precedence over the user and workspace settings, so a
# workdir under review cannot select another auth type or re-enable updates.
# ignoreLocalEnv keeps the CLI from loading any .env file in or above the workdir,
# which it would otherwise read for its auth variables even when untrusted; only
# the dedicated home's own .env remains.
cat > "$scratch/system-settings.json" <<'EOF'
{
  "security": {
    "auth": {"selectedType": "oauth-personal", "enforcedType": "oauth-personal"},
    "folderTrust": {"enabled": false}
  },
  "general": {"enableAutoUpdate": false, "enableAutoUpdateNotification": false},
  "advanced": {"ignoreLocalEnv": true}
}
EOF

export HOME="$DISPATCH_GEMINI_HOME" GEMINI_CLI_HOME="$DISPATCH_GEMINI_HOME"
export GEMINI_CLI_SYSTEM_SETTINGS_PATH="$scratch/system-settings.json"
export GEMINI_CLI_SYSTEM_DEFAULTS_PATH="$scratch/system-defaults.json"
# With folder trust off, an untrusted headless run starts instead of refusing.
export GEMINI_CLI_TRUST_WORKSPACE="$trust_workdir"

status=0
(cd -- "$DISPATCH_WORKDIR" && exec gemini \
  --prompt 'Follow the instructions above.' --output-format json \
  --model "$model" --approval-mode "$approval_mode") \
  <"$scratch/prompt" >"$scratch/stdout" 2>"$scratch/stderr" || status=$?
python3 "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/gemini_result.py" \
  "$scratch/stdout" "$scratch/stderr" "$model" "$status" "$DISPATCH_OUTPUT"
