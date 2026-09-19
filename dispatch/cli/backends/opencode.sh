#!/usr/bin/env bash
# Public OpenCode CLI adapter. Provider authentication stays in the runtime's
# native OpenCode configuration and is never forwarded by the dispatcher.
set -euo pipefail

: "${DISPATCH_TASK:?DISPATCH_TASK is required}"
: "${DISPATCH_WORKDIR:?DISPATCH_WORKDIR is required}"
: "${DISPATCH_OUTPUT:?DISPATCH_OUTPUT is required}"
: "${DISPATCH_MODEL:?DISPATCH_MODEL is required for the OpenCode adapter}"
: "${DISPATCH_OPENCODE_HOME:?DISPATCH_OPENCODE_HOME is required for isolated OpenCode auth}"

[[ "$DISPATCH_OPENCODE_HOME" = /* && -d "$DISPATCH_OPENCODE_HOME" ]] || {
  printf 'DISPATCH_OPENCODE_HOME must be an existing absolute directory\n' >&2
  exit 2
}
caller_home="${HOME:-}"
[[ -n "$caller_home" ]] || {
  printf 'OpenCode requires a non-empty caller home\n' >&2
  exit 2
}
if [[ -n "$caller_home" ]]; then
  caller_home_real="$(realpath -- "$caller_home")"
  opencode_home_real="$(realpath -- "$DISPATCH_OPENCODE_HOME")"
  case "$opencode_home_real/" in
    "$caller_home_real/"*)
      printf 'DISPATCH_OPENCODE_HOME must be outside the caller home\n' >&2
      exit 2
      ;;
  esac
fi
export HOME="$DISPATCH_OPENCODE_HOME"
unset OPENAI_API_KEY OPENCODE_API_KEY OPENCODE_GO_API_KEY ANTHROPIC_API_KEY OPENROUTER_API_KEY \
  GEMINI_API_KEY GOOGLE_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN \
  AZURE_OPENAI_API_KEY COHERE_API_KEY MISTRAL_API_KEY XAI_API_KEY

command -v opencode >/dev/null 2>&1 || {
  printf 'opencode CLI not found on PATH\n' >&2
  exit 127
}

model="$DISPATCH_MODEL"
[[ "$model" != *[[:space:]]* && "$model" != *[![:print:]]* ]] || {
  printf 'model identifier is invalid\n' >&2
  exit 2
}

case "${DISPATCH_KIND:-}" in
  review) agent='plan' ;;
  passthrough) agent='build' ;;
  *) printf 'unsupported OpenCode dispatch kind\n' >&2; exit 2 ;;
esac

prompt="$DISPATCH_TASK"
[[ "$prompt" != -* ]] || {
  printf 'OpenCode task must not begin with a dash\n' >&2
  exit 2
}
args=(opencode run "$prompt" --pure --format json --agent "$agent" --model "$model" --dir "$DISPATCH_WORKDIR")
if [[ "$DISPATCH_KIND" == review ]]; then
  [[ -n "${DISPATCH_DIFF:-}" ]] || {
    printf 'OpenCode review requires a diff file\n' >&2
    exit 2
  }
elif [[ -n "${DISPATCH_DIFF:-}" ]]; then
  printf 'OpenCode diff attachments are supported only for review\n' >&2
  exit 2
fi
if [[ "$DISPATCH_KIND" == review ]]; then
  [[ -f "$DISPATCH_DIFF" && ! -L "$DISPATCH_DIFF" ]] || {
    printf 'OpenCode diff attachment requires a regular file\n' >&2
    exit 2
  }
  args+=(--file "$DISPATCH_DIFF")
fi
[[ ! -d "$DISPATCH_OUTPUT" ]] || {
  printf 'DISPATCH_OUTPUT must not be a directory\n' >&2
  exit 2
}

# OpenCode reads provider authentication from its native per-user config. The
# dispatcher gives it only the dedicated OpenCode home and no provider secret.
temporary_output="$(mktemp "${DISPATCH_OUTPUT}.opencode.XXXXXX")"
cleanup() { rm -f -- "$temporary_output"; }
trap cleanup EXIT
"${args[@]}" >"$temporary_output"
mv -f -- "$temporary_output" "$DISPATCH_OUTPUT"
trap - EXIT
