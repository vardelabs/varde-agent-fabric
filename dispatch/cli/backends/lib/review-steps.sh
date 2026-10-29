# shellcheck shell=bash
# How an OpenCode review ends with findings inside its time budget.
#
# A step count cannot bound wall time: on one large tree a step took 16 to 50 s
# depending on the model. At its step limit OpenCode forces a turn that writes a
# work summary, not findings. So the backend gets findings in its own final
# turn: it resumes the run's session with every tool denied and asks for the
# findings alone. The step limit is only a backstop.
review_step_limit=30
# With a deadline (DISPATCH_DEADLINE_EPOCH, unix seconds), the exploring run is
# stopped this many seconds before it, leaving the final turn its time.
review_final_reserve_s=90
# Below this many seconds left there is no final turn; the review fails as out
# of time rather than being killed partway through one.
review_final_min_s=20
review_out_of_time='review ran out of time before its findings turn'
review_final_prompt='Tools are no longer available. Write your review findings now, as the JSON array the task asked for, and nothing else. Write [] if you found nothing.'

# Sets review_deadline to the validated deadline, or empty when none was given.
read_review_deadline() {
  review_deadline="${DISPATCH_DEADLINE_EPOCH:-}"
  [[ -z "$review_deadline" || "$review_deadline" =~ ^[0-9]{1,12}$ ]] || {
    printf 'DISPATCH_DEADLINE_EPOCH must be unix seconds\n' >&2
    exit 2
  }
}

# Prints the seconds left before the deadline, or nothing without one.
review_seconds_left() {
  [[ -n "$review_deadline" ]] || return 0
  printf '%d\n' $((review_deadline - $(date +%s)))
}

# Prints the seconds the exploring run may take, or nothing without a deadline.
review_explore_limit() {
  [[ -n "$review_deadline" ]] || return 0
  printf '%d\n' $(($(review_seconds_left) - review_final_reserve_s))
}

# watch_pid <seconds> <pid>: stops the process tree of pid after seconds, and
# sets watchdog_pid. An empty limit starts no watchdog.
watch_pid() {
  watchdog_pid=""
  [[ -n "$1" ]] || return 0
  local limit="$1" target="$2"
  ( sleep "$limit"
    tree() { local child; for child in $(pgrep -P "$1" 2>/dev/null); do tree "$child"; done; printf '%s\n' "$1"; }
    # shellcheck disable=SC2046
    kill -TERM $(tree "$target") 2>/dev/null ) &
  watchdog_pid=$!
}

stop_watchdog() {
  [[ -n "${watchdog_pid:-}" ]] || return 0
  pkill -P "$watchdog_pid" 2>/dev/null || true
  kill "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  watchdog_pid=""
}
