#!/usr/bin/env bash
# Public OpenCode CLI adapter. Provider authentication stays in the runtime's
# native OpenCode configuration and is never forwarded by the dispatcher.
set -euo pipefail

: "${DISPATCH_TASK:?DISPATCH_TASK is required}"
: "${DISPATCH_WORKDIR:?DISPATCH_WORKDIR is required}"
: "${DISPATCH_OUTPUT:?DISPATCH_OUTPUT is required}"
: "${DISPATCH_MODEL:?DISPATCH_MODEL is required for the OpenCode adapter}"

command -v opencode >/dev/null 2>&1 || {
  printf 'opencode CLI not found on PATH\n' >&2
  exit 127
}

model="$DISPATCH_MODEL"
[[ "$model" != *$'\n'* && "$model" != *$'\r'* ]] || {
  printf 'model identifier is invalid\n' >&2
  exit 2
}

case "${DISPATCH_KIND:-}" in
  review) agent='plan' ;;
  passthrough) agent='build' ;;
  *) printf 'unsupported OpenCode dispatch kind\n' >&2; exit 2 ;;
esac

prompt="$DISPATCH_TASK"
if [[ -n "${DISPATCH_DIFF:-}" ]]; then
  diff_content="$(cat -- "$DISPATCH_DIFF")"
  prompt+=$'\n\n--- BEGIN DIFF ---\n'
  prompt+="$diff_content"
  prompt+=$'\n--- END DIFF ---'
fi

# OpenCode reads provider authentication from its native per-user config. The
# dispatcher deliberately preserves HOME but does not pass provider secrets.
opencode run --pure --format json --agent "$agent" --model "$model" --dir "$DISPATCH_WORKDIR" -- "$prompt" \
  >"$DISPATCH_OUTPUT"
