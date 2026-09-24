#!/usr/bin/env bash
# Public Codex adapter. Credentials and provider configuration are intentionally
# not forwarded by the dispatcher. Authentication is the runtime's concern.
set -euo pipefail

: "${DISPATCH_TASK:?DISPATCH_TASK is required}"
: "${DISPATCH_WORKDIR:?DISPATCH_WORKDIR is required}"
: "${DISPATCH_OUTPUT:?DISPATCH_OUTPUT is required}"

command -v codex >/dev/null 2>&1 || {
  printf 'codex CLI not found on PATH\n' >&2
  exit 127
}

model="${DISPATCH_MODEL:-gpt-5.6-luna}"
[[ "$model" != *$'\n'* && "$model" != *$'\r'* ]] || {
  printf 'model identifier is invalid\n' >&2
  exit 2
}

sandbox_mode="${DISPATCH_CODEX_SANDBOX:-read-only}"
case "$sandbox_mode" in
  read-only|workspace-write) ;;
  *) printf 'DISPATCH_CODEX_SANDBOX is invalid\n' >&2; exit 2 ;;
esac

case "${DISPATCH_KIND:-}" in
  review|passthrough) ;;
  implement)
    [[ "$sandbox_mode" == workspace-write ]] || {
      printf 'Codex implement requires DISPATCH_CODEX_SANDBOX=workspace-write\n' >&2
      exit 2
    }
    ;;
  *) printf 'unsupported Codex dispatch kind\n' >&2; exit 2 ;;
esac

prompt="$DISPATCH_TASK"
if [[ -n "${DISPATCH_DIFF:-}" ]]; then
  diff_content="$(cat -- "$DISPATCH_DIFF")"
  prompt+=$'\n\n--- BEGIN DIFF ---\n'
  prompt+="$diff_content"
  prompt+=$'\n--- END DIFF ---'
fi

unset DISPATCH_TASK DISPATCH_DIFF
args=(codex exec --ignore-user-config --skip-git-repo-check --model "$model" --sandbox "$sandbox_mode" -C "$DISPATCH_WORKDIR" -o "$DISPATCH_OUTPUT" -- -)
printf '%s' "$prompt" | "${args[@]}" >&2
