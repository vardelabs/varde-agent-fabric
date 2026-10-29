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
caller_home_real="$(realpath -- "$caller_home")"
opencode_home_real="$(realpath -- "$DISPATCH_OPENCODE_HOME")"
if [[ "$caller_home_real" == / || "$opencode_home_real" == / ]]; then
  printf 'OpenCode homes must not be the filesystem root\n' >&2
  exit 2
fi
caller_home_real="${caller_home_real%/}"
opencode_home_real="${opencode_home_real%/}"
case "$opencode_home_real/" in
  "$caller_home_real/"*)
    printf 'DISPATCH_OPENCODE_HOME must be outside the caller home\n' >&2
    exit 2
    ;;
esac
case "$caller_home_real/" in
  "$opencode_home_real/"*)
    printf 'DISPATCH_OPENCODE_HOME must not contain the caller home\n' >&2
    exit 2
    ;;
esac
export HOME="$DISPATCH_OPENCODE_HOME"
# A self-update would download a new binary into the writable home, bypassing
# the pinned install the deployment chose.
export OPENCODE_DISABLE_AUTOUPDATE=1
if [[ -n "${DISPATCH_OPENCODE_MODELS_PATH:-}" ]]; then
  catalog="$DISPATCH_OPENCODE_MODELS_PATH"
  [[ "$catalog" = /* && -f "$catalog" && ! -L "$catalog" ]] || {
    printf 'DISPATCH_OPENCODE_MODELS_PATH must be an absolute regular file\n' >&2
    exit 2
  }
  (( (8#$(stat -c '%a' -- "$catalog") & 8#022) == 0 )) || {
    printf 'DISPATCH_OPENCODE_MODELS_PATH must not be group or world writable\n' >&2
    exit 2
  }
  # A pinned catalog serves models the public list lacks; a fetch would replace it.
  export OPENCODE_MODELS_PATH="$catalog" OPENCODE_DISABLE_MODELS_FETCH=1
fi

command -v opencode >/dev/null 2>&1 || {
  printf 'opencode CLI not found on PATH\n' >&2
  exit 127
}

model="$DISPATCH_MODEL"
[[ "$model" != *[[:space:]]* && "$model" != *[![:print:]]* ]] || {
  printf 'model identifier is invalid\n' >&2
  exit 2
}

backends="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
# shellcheck source=lib/review-steps.sh
. "$backends/lib/review-steps.sh"
final_turn=""
review_deadline=""

case "${DISPATCH_KIND:-}" in
  review) agent='plan' ;;
  implement|passthrough)
    [[ "${DISPATCH_OPENCODE_ALLOW_WRITE:-}" == 1 ]] || {
      printf 'OpenCode implementation requires DISPATCH_OPENCODE_ALLOW_WRITE=1 (kind: %s)\n' "$DISPATCH_KIND" >&2
      exit 2
    }
    agent='build'
    ;;
  *) printf 'unsupported OpenCode dispatch kind\n' >&2; exit 2 ;;
esac

prompt="$DISPATCH_TASK"
[[ "$prompt" != -* ]] || {
  printf 'OpenCode task must not begin with a dash\n' >&2
  exit 2
}
opencode_version="$(opencode --version 2>/dev/null)" || opencode_version=''
[[ "$opencode_version" =~ ([0-9]+)\.[0-9]+ ]] || {
  printf 'could not determine the opencode CLI version\n' >&2
  exit 2
}
if (( BASH_REMATCH[1] >= 2 )); then
  # v2 has no --pure: disabling project config is what stops a plugin shipped
  # in the workdir from loading. Without --standalone the run spawns a shared
  # background service that outlives this dispatch.
  export OPENCODE_DISABLE_PROJECT_CONFIG=1
  if [[ "$agent" == plan ]]; then
    # Inline config merges over the native config, so only the step limit
    # changes. 1.x has no step limit and would pass an unknown agent key on to
    # the provider as a model option, so it gets no final turn either.
    export OPENCODE_CONFIG_CONTENT='{"agent":{"plan":{"steps":'"$review_step_limit"'}}}'
    final_turn=1
    read_review_deadline
  fi
  args=(opencode run "$prompt" --standalone --format json --agent "$agent" --model "$model")
else
  args=(opencode run "$prompt" --pure --format json --agent "$agent" --model "$model" --dir "$DISPATCH_WORKDIR")
fi
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
final_output=""
cleanup() { stop_watchdog; rm -f -- "$temporary_output" ${final_output:+"$final_output"}; }
trap cleanup EXIT
# run_opencode <time limit> <output> <command...>: sets status. v2 has no --dir
# and resolves the project from the working directory. Both CLIs append a
# non-terminal stdin to the prompt and block until it closes.
run_opencode() {
  local limit="$1" output="$2" pid
  shift 2
  status=0
  (cd -- "$DISPATCH_WORKDIR" && exec "$@") </dev/null >"$output" &
  pid=$!
  watch_pid "$limit" "$pid"
  wait "$pid" || status=$?
  stop_watchdog
}
report_failed_run() {
  # `--format json` reports failures as error events on stdout, so stderr alone
  # is empty. Only top-level error events are copied: other events, including
  # tool results with a nested error type, can quote files the agent read.
  printf 'opencode run failed with exit status %d\n' "$status" >&2
  grep -E '^\{"type":"error"' -- "$1" | tail -c 4096 >&2 \
    || printf 'opencode reported no error event\n' >&2
  exit "$status"
}
out_of_time() { printf '%s\n' "$review_out_of_time" >&2; exit 124; }

explore_limit="$(review_explore_limit)"
[[ -z "$explore_limit" ]] || ((explore_limit > 0)) || out_of_time
run_opencode "$explore_limit" "$temporary_output" "${args[@]}"
((status == 0)) || stopped_by_watchdog || report_failed_run "$temporary_output"

# A review stopped at its time cut or its step limit has written no findings,
# so its session is resumed once, with every tool denied, to write them. A
# stream that names no session goes to the caller's parser as it is.
if [[ -n "$final_turn" ]] && ! python3 "$backends/review_final.py" has-findings "$temporary_output" \
  && session="$(python3 "$backends/review_final.py" session "$temporary_output")"; then
  seconds_left="$(review_seconds_left)"
  [[ -z "$seconds_left" ]] || ((seconds_left >= review_final_min_s)) || out_of_time
  final_output="$(mktemp "${DISPATCH_OUTPUT}.opencode-final.XXXXXX")"
  export OPENCODE_CONFIG_CONTENT='{"agent":{"plan":{"steps":'"$review_step_limit"'}},"permission":{"*":"deny"}}'
  run_opencode "${seconds_left:+$((seconds_left - 5))}" "$final_output" opencode run "$review_final_prompt" \
    --session "$session" --standalone --format json --agent plan --model "$model"
  cat -- "$final_output" >> "$temporary_output"
  ((status == 0)) || { stopped_by_watchdog && out_of_time; } || report_failed_run "$final_output"
  python3 "$backends/review_final.py" has-findings "$temporary_output" || {
    printf 'OpenCode review final turn wrote no findings\n' >&2
    exit 1
  }
fi
mv -f -- "$temporary_output" "$DISPATCH_OUTPUT"
rm -f -- ${final_output:+"$final_output"}
trap - EXIT
