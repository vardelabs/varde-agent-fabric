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
# stopped this many seconds before it, or a third of the time left if that is
# less, leaving the final turn its time.
review_final_reserve_s=90
# Below this many seconds left there is no final turn; the review fails as out
# of time rather than being killed partway through one.
review_final_min_s=10
review_stop_grace_s=5
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
  local left reserve
  left="$(review_seconds_left)"
  reserve=$((left / 3 < review_final_reserve_s ? left / 3 : review_final_reserve_s))
  # The watchdog may take review_stop_grace_s more to stop a run that ignores TERM.
  printf '%d\n' $((left - reserve - review_stop_grace_s))
}

# watch_pid <seconds> <pid>: after seconds, stops the process tree of pid with
# TERM, then KILL for anything still alive review_stop_grace_s later. Sets
# watchdog_pid. An empty limit starts no watchdog.
watch_pid() {
  watchdog_pid=""
  watchdog_fired=""
  [[ -n "$1" ]] || return 0
  local limit="$1" target="$2"
  watchdog_fired="$(mktemp "${TMPDIR:-/tmp}/review-watchdog.XXXXXX")"
  rm -f -- "$watchdog_fired"
  ( sleep "$limit"
    : > "$watchdog_fired"
    tree() { local child; for child in $(pgrep -P "$1" 2>/dev/null); do tree "$child"; done; printf '%s\n' "$1"; }
    # The tree is named once: a child whose parent exits is no longer found under it.
    pids="$(tree "$target")"
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null
    for _ in $(seq $((review_stop_grace_s * 10))); do
      alive=""
      for pid in $pids; do kill -0 "$pid" 2>/dev/null && alive=1; done
      [[ -n "$alive" ]] || exit 0
      sleep 0.1
    done
    # shellcheck disable=SC2086
    kill -KILL $pids 2>/dev/null ) &
  watchdog_pid=$!
}

# stop_watchdog: a watchdog that has fired is let finish, so nothing it stops
# outlives the run; one still waiting is ended. Sets review_cut when it fired.
stop_watchdog() {
  review_cut=""
  [[ -n "${watchdog_pid:-}" ]] || return 0
  if [[ -e "$watchdog_fired" ]]; then
    review_cut=1
  else
    # The watchdog goes first: its sleep ending would let it fire.
    local sleeper
    sleeper="$(pgrep -P "$watchdog_pid" 2>/dev/null || true)"
    kill "$watchdog_pid" 2>/dev/null || true
    # shellcheck disable=SC2086
    [[ -z "$sleeper" ]] || kill $sleeper 2>/dev/null || true
  fi
  wait "$watchdog_pid" 2>/dev/null || true
  rm -f -- "$watchdog_fired"
  watchdog_pid=""
}

# Whether the last run ended because its watchdog stopped it.
stopped_by_watchdog() { [[ -n "${review_cut:-}" ]]; }
