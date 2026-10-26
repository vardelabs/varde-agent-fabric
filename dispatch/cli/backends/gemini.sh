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

# A per-run home carries the adapter's own user settings. The CLI loads a system
# settings file only when root owns it and every parent, which a worker cannot
# arrange, but it always loads user settings, and an untrusted or empty folder
# adds no workspace settings above them. The sign-in files are linked from the
# dedicated home, so the stored login is used and a token refresh reaches it.
# ignoreLocalEnv stops the CLI reading a .env in or above the folder for its auth
# variables, which it otherwise does even when the folder is untrusted. The web
# tools are excluded because the read-only policy still allows them, and text in
# a reviewed diff could steer workdir content into a search or a fetch.
run_home="$scratch/home"
mkdir -p "$run_home/.gemini" "$scratch/folder"
if [[ -d "$DISPATCH_GEMINI_HOME/.gemini" ]]; then
  for entry in "$DISPATCH_GEMINI_HOME/.gemini"/* "$DISPATCH_GEMINI_HOME/.gemini"/.[!.]*; do
    [[ -e "$entry" && "$(basename -- "$entry")" != settings.json ]] || continue
    ln -s -- "$entry" "$run_home/.gemini/"
  done
fi
cat > "$run_home/.gemini/settings.json" <<'EOF'
{
  "security": {
    "auth": {"selectedType": "oauth-personal", "enforcedType": "oauth-personal"},
    "folderTrust": {"enabled": false}
  },
  "general": {"enableAutoUpdate": false, "enableAutoUpdateNotification": false},
  "advanced": {"ignoreLocalEnv": true},
  "tools": {"exclude": ["google_web_search", "web_fetch"]}
}
EOF

export HOME="$run_home" GEMINI_CLI_HOME="$run_home"
# With folder trust off, an untrusted headless run starts instead of refusing.
export GEMINI_CLI_TRUST_WORKSPACE="$trust_folder"

if [[ "$trust_folder" == true ]]; then
  folder="$scratch/folder"
  include=(--include-directories "$DISPATCH_WORKDIR")
else
  folder="$DISPATCH_WORKDIR"
  include=()
fi

status=0
(cd -- "$folder" && exec gemini \
  --prompt 'Follow the instructions above.' --output-format json \
  --model "$model" --approval-mode "$approval_mode" "${include[@]}") \
  <"$scratch/prompt" >"$scratch/stdout" 2>"$scratch/stderr" || status=$?
python3 "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/gemini_result.py" \
  "$scratch/stdout" "$scratch/stderr" "$model" "$status" "$DISPATCH_OUTPUT"
