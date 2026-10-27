# Isolation shared by the review-only backends that run a third-party CLI in the
# reviewed workdir under a subscription sign-in. Sourced, never run.

fail() { printf '%s\n' "$1" >&2; exit "${2:-2}"; }

# require_dedicated_home <label> <variable>: the sign-in home must be an existing
# absolute directory, apart from the caller home in both directions. Sets
# dedicated_home_real.
require_dedicated_home() {
  local label="$1" variable="$2" home="${!2:-}" caller_home="${HOME:-}" caller_home_real
  [[ "$home" = /* && -d "$home" ]] || fail "$variable must be an existing absolute directory"
  [[ -n "$caller_home" ]] || fail "$label requires a non-empty caller home"
  caller_home_real="$(realpath -- "$caller_home")"
  dedicated_home_real="$(realpath -- "$home")"
  [[ "$caller_home_real" != / && "$dedicated_home_real" != / ]] || fail "$label homes must not be the filesystem root"
  caller_home_real="${caller_home_real%/}"
  dedicated_home_real="${dedicated_home_real%/}"
  case "$dedicated_home_real/" in
    "$caller_home_real/"*) fail "$variable must be outside the caller home" ;;
  esac
  case "$caller_home_real/" in
    "$dedicated_home_real/"*) fail "$variable must not contain the caller home" ;;
  esac
}

# The CLI runs in the reviewed workdir, so a relative PATH entry, or one that
# resolves inside the workdir, would find the CLI, or the interpreter its shebang
# names, among files under review. Each kept entry is the resolved directory,
# never the literal one, because a link in it could resolve differently from the
# workdir. Sets workdir_real and replaces PATH.
keep_path_outside_workdir() {
  local entries entry resolved kept=""
  workdir_real="$(realpath -- "$DISPATCH_WORKDIR")"
  IFS=: read -ra entries <<<"$PATH"
  for entry in "${entries[@]}"; do
    [[ "$entry" == /* ]] || continue
    resolved="$(realpath -- "$entry" 2>/dev/null)" && [[ -d "$resolved" ]] || continue
    case "${resolved%/}/" in
      "${workdir_real%/}/"*) continue ;;
    esac
    kept+="${kept:+:}$resolved"
  done
  PATH="$kept"
}

cli_pid=""
process_tree() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do process_tree "$child"; done
  printf '%s\n' "$1"
}
any_alive() {
  local pid
  for pid in "$@"; do kill -0 "$pid" 2>/dev/null && return 0; done
  return 1
}
# Bash runs a trap only after its foreground child ends, so the CLI runs in the
# background. On a signal its whole tree is stopped, and the scratch is removed
# only once the CLI can no longer write into it. It stays in this process group,
# so a SIGKILL to the group still reaches it.
stop_cli() {
  [[ -n "$cli_pid" ]] || return 0
  local tree
  tree="$(process_tree "$cli_pid")"
  # shellcheck disable=SC2086
  kill -TERM $tree 2>/dev/null || true
  # shellcheck disable=SC2086
  for _ in $(seq 50); do any_alive $tree || return 0; sleep 0.1; done
  # shellcheck disable=SC2086
  kill -KILL $tree 2>/dev/null || true
}

# make_scratch <label> <variable> <prefix>: a 0700 scratch directory, absolute
# because the CLI starts in the workdir, removed on every exit after the CLI has
# stopped. The CLI can read its workdir, so neither the scratch nor the sign-in
# may be in it. Sets scratch.
make_scratch() {
  local label="$1" variable="$2" private_path
  scratch="$(realpath -- "$(mktemp -d "${TMPDIR:-/tmp}/$3.XXXXXX")")"
  trap 'stop_cli; rm -rf -- "$scratch"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  for private_path in "$scratch" "$dedicated_home_real"; do
    case "$private_path/" in
      "${workdir_real%/}/"*) fail "$label needs its TMPDIR and $variable outside the workdir, which the CLI can read" ;;
    esac
  done
}

# run_cli <stdin> <stdout> <stderr> <command...>: runs the CLI in the workdir
# with only the environment in cli_env, and sets status.
run_cli() {
  local stdin="$1" stdout="$2" stderr="$3"
  shift 3
  status=0
  (cd -- "$DISPATCH_WORKDIR" && exec env -i "${cli_env[@]}" "$@") <"$stdin" >"$stdout" 2>"$stderr" &
  cli_pid=$!
  wait "$cli_pid" || status=$?
  cli_pid=""
}
