#!/usr/bin/env bash
# The grok backend against a fake `opencode` CLI: no account, network, or key.
set -euo pipefail
unset DISPATCH_MODEL DISPATCH_ROUTES_FILE DISPATCH_GROK_HOME XAI_API_KEY

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
CLI="$ROOT/dispatch/cli/varde-agent-dispatch"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/grok-backend-test.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
# The backend refuses a workdir below a .git or .hg, so the test tree needs a
# temporary directory with none above it.
ancestor="$TMP"
while [[ "$ancestor" != / ]]; do
  ancestor="$(dirname "$ancestor")"
  [[ ! -e "$ancestor/.git" && ! -e "$ancestor/.hg" ]] \
    || { printf 'grok backend tests need a TMPDIR with no .git or .hg above it: %s\n' "$ancestor" >&2; exit 1; }
done
mkdir -p "$TMP/work" "$TMP/caller-home" "$TMP/bin" "$TMP/runs" "$TMP/state"
printf 'diff body sentinel\n' > "$TMP/change.diff"
MODEL=xai/grok-example
state="$TMP/state"
signin_db="$TMP/grok-home/.local/share/opencode/opencode.db"
mkdir -p "$(dirname "$signin_db")"
# The sign-in database holds an xAI credential and another provider's; a run
# must keep a refreshed xAI token and nothing else.
python3 - "$signin_db" <<'EOF'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("CREATE TABLE credential (id TEXT PRIMARY KEY, integration_id TEXT, label TEXT, value TEXT)")
db.execute("CREATE TABLE session (id TEXT PRIMARY KEY, body TEXT)")
db.execute("INSERT INTO credential VALUES ('xai-1', 'xai', 'SuperGrok', '{\"refresh\":\"stored\"}')")
db.execute("INSERT INTO credential VALUES ('other-1', 'other', 'Other', '{\"key\":\"stored\"}')")
db.commit()
EOF

printf '%s' "$signin_db" > "$state/signin-path"
pass=0
check() { "$@"; pass=$((pass + 1)); }
check_not() { if "$@"; then printf 'unexpected match: %s\n' "$*" >&2; exit 1; fi; pass=$((pass + 1)); }
mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
alive() { kill -0 "$1" 2>/dev/null; }

# The fake records what it was given, then answers as the scenario file says.
cat > "$TMP/bin/opencode" <<EOF
#!/usr/bin/env bash
set -euo pipefail
state="$state"
EOF
cat >> "$TMP/bin/opencode" <<'EOF'
{
  printf 'argv=%s\n' "$*"
  printf 'cwd=%s\n' "$PWD"
  printf 'home=%s\n' "$HOME"
  printf 'tmpdir=%s\n' "${TMPDIR:-}"
  printf 'db=%s\n' "${OPENCODE_DB:-}"
  printf 'project_config_off=%s\n' "${OPENCODE_DISABLE_PROJECT_CONFIG:-}"
  printf 'config=%s\n' "${OPENCODE_CONFIG_CONTENT:-}"
  printf 'leaked=%s\n' "${XAI_API_KEY:-}${NODE_OPTIONS:-}${HTTPS_PROXY:-}${DISPATCH_UNSAFE_SENTINEL:-}"
  printf 'stdin=%s\n' "$(cat)"
} > "$state/record"
text() { printf '{"type":"text","part":{"text":%s}}\n' "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$1")"; }
printf '%s|%s\n' "$*" "${OPENCODE_CONFIG_CONTENT:-}" >> "$state/calls"
session_start() { printf '{"type":"step_start","sessionID":"ses_test1","part":{}}\n'; }
summary='Maximum steps reached. Summary of work so far: read the files.'
case "$(cat "$state/scenario")" in
  summary)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text '[{"severity":"major","title":"Late","path":"app.py","line":"2"}]'
    else text "$summary"; fi ;;
  summary-twice) session_start; text "$summary" ;;
  partial-line-hang)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text '[{"severity":"major","title":"Whole","path":"app.py","line":"5"}]'
    else text 'Exploring.'; printf '{"type":"tool_use","part":{"tool":"re'; sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait; fi ;;
  fenced-final)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text $'```json\n[]\n```'
    else text "$summary"; fi ;;
  dropped-finding)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text '[]'
    else text '[{"severity":"major","title":"Kept","path":"app.py","line":"6"}]'; sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait; fi ;;
  final-cut-clean-exit)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then trap 'text "[]"; exit 0' TERM; sleep 60 & wait
    else sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait; fi ;;
  fenced-uncut) session_start; text $'```json\n[]\n```' ;;
  fenced-final-findings)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text $'```json\n[{"severity":"major","title":"Fenced","path":"app.py","line":"8"}]\n```'
    else text "$summary"; fi ;;
  split-snapshot-dropped|split-snapshot-kept)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then
      if [[ "$(cat "$state/scenario")" == split-snapshot-kept ]]; then text '[{"severity":"major","title":"Split","path":"app.py","line":"7"}]'
      else text '[]'; fi
    else text '[{"severity":"major","title":"Split",'; text '"path":"app.py","line":"7"}]'
      sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait; fi ;;
  prose-no-session) text "$summary" ;;
  mixed)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text '[{"severity":"major","title":"Kept","path":"app.py","line":"6"}]'
    else text '[{"severity":"major","title":"Kept","path":"app.py","line":"6"}]'; text "$summary"; fi ;;
  mixed-dropped)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text '[]'
    else text '[{"severity":"major","title":"Kept","path":"app.py","line":"6"}]'; text "$summary"; fi ;;
  early-findings-no-session)
    text '[]'; sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait ;;
  early-findings-silent-final)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then printf '{"type":"step_finish","part":{}}\n'
    else text '[]'; sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait; fi ;;
  early-findings-hang)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text '[{"severity":"major","title":"Full","path":"app.py","line":"4"}]'
    else text '[]'; sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait; fi ;;
  stubborn-hang)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text '[]'
    else trap '' TERM; sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait; fi ;;
  hang)
    session_start
    if [[ " $* " == *" --session ses_test1 "* ]]; then text '[]'
    else sleep 30 & printf '%s' "$!" > "$state/hang-child.pid"; wait; fi ;;
  ok)
    python3 - "$OPENCODE_DB" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("UPDATE credential SET value = '{\"refresh\":\"refreshed\"}' WHERE id = 'xai-1'")
db.execute("UPDATE credential SET value = '{\"key\":\"changed\"}' WHERE id = 'other-1'")
db.execute("INSERT INTO credential VALUES ('xai-2', 'xai', 'New', '{\"refresh\":\"new\"}')")
db.execute("INSERT INTO session VALUES ('s1', 'diff body sentinel')")
db.commit()
PY
    text '[{"severity":"major","title":"Auth bypass","path":"app.py","line":"3"}]' ;;
  limit) printf '{"type":"error","error":{"type":"provider.rate_limit","status":429,"message":"slow down"}}\n'; exit 1 ;;
  not-entitled) printf '{"type":"error","error":{"type":"provider.api","status":403,"message":"The caller does not have permission"}}\n'; exit 1 ;;
  stale)
    python3 - "$(cat "$state/signin-path")" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("UPDATE credential SET value = '{\"refresh\":\"other-run\"}' WHERE id = 'xai-1'")
db.commit()
PY
    text '[]' ;;
  race)
    python3 - "$(cat "$state/signin-path")" "$OPENCODE_DB" <<'PY'
import sqlite3, sys
for path, value in ((sys.argv[1], "other-run"), (sys.argv[2], "this-run")):
    db = sqlite3.connect(path)
    db.execute("UPDATE credential SET value = ? WHERE id = 'xai-1'", ('{"refresh":"%s"}' % value,))
    db.commit()
PY
    text '[]' ;;
  exit-75) printf '{"type":"error","error":{"type":"provider.api","status":500}}\n'; exit 75 ;;
  slow)
    python3 - "$OPENCODE_DB" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("UPDATE credential SET value = '{\"refresh\":\"before-term\"}' WHERE id = 'xai-1'")
db.commit()
PY
    sleep 8 & printf '%s' "$!" > "$state/slow-child.pid"; printf '%s' "$$" > "$state/slow.pid"; wait ;;
esac
EOF
chmod +x "$TMP/bin/opencode"

run() {  # run <scenario> <out-name> [extra dispatcher args...]
  printf '%s' "$1" > "$state/scenario"
  local name="$2"; shift 2
  status=0
  HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GROK_HOME="$TMP/grok-home" TMPDIR="$TMP/runs" \
    XAI_API_KEY=must-not-forward DISPATCH_UNSAFE_SENTINEL=must-not-forward DISPATCH_MODEL="$MODEL" \
    "$CLI" --harness grok --workdir "$TMP/work" --output "$TMP/$name" "$@" \
    >"$TMP/$name.stdout" 2>"$TMP/$name.stderr" || status=$?
}
direct() {  # direct <out-name> [env assignments...]: the backend outside the dispatcher
  local name="$1"; shift
  status=0
  (cd "$TMP" && env HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GROK_HOME="$TMP/grok-home" \
    TMPDIR="$TMP/runs" DISPATCH_MODEL="$MODEL" DISPATCH_KIND=review DISPATCH_TASK=t DISPATCH_WORKDIR="$TMP/work" \
    DISPATCH_OUTPUT="$TMP/$name" "$@" /bin/bash "$ROOT/dispatch/cli/backends/grok.sh" \
    >/dev/null 2>"$TMP/$name.stderr") || status=$?
}
set_credential() { python3 -c 'import sqlite3,sys;d=sqlite3.connect(sys.argv[1]);d.execute("UPDATE credential SET value=? WHERE id=?",(sys.argv[3],sys.argv[2]));d.commit()' "$signin_db" "$1" "$2"; }
credential() { python3 -c 'import sqlite3,sys;print((sqlite3.connect(sys.argv[1]).execute("SELECT value FROM credential WHERE id=?",(sys.argv[2],)).fetchone() or ["none"])[0])' "$signin_db" "$1"; }

run ok out-review --kind review --diff "$TMP/change.diff" --task private-task-sentinel
check test "$status" -eq 0
check test "$(mode "$TMP/out-review")" = 600
record="$state/record"
check grep -qF -- "--standalone --format json --agent plan --model $MODEL" "$record"
check_not grep -qF 'private-task-sentinel' <(grep '^argv=' "$record")
check grep -qxF 'stdin=Task for this run:' "$record"
check grep -qxF 'private-task-sentinel' "$record"
check grep -qF 'diff body sentinel' "$record"
check grep -qxF "cwd=$TMP/work" "$record"
check grep -qE "^home=$TMP/runs/grok-dispatch\.[A-Za-z0-9]+/home$" "$record"
check grep -qE "^tmpdir=$TMP/runs/grok-dispatch\.[A-Za-z0-9]+/tmp$" "$record"
check grep -qE "^db=$TMP/runs/grok-dispatch\.[A-Za-z0-9]+/opencode\.db$" "$record"
check grep -qxF 'project_config_off=1' "$record"
check grep -qF '"enabled_providers":["xai"]' "$record"
# Every tool is denied but the three that read the workdir, so code mode's
# execute, and any tool a later release adds, is never offered.
check grep -qF '"permission":{"*":"deny","read":"allow","glob":"allow","grep":"allow","external_directory":"deny"}' "$record"
check test "$(grep '^config=' "$record" | grep -o '"allow"' | wc -l)" -eq 3
# A review that never stops exploring is cut to a text answer at the step limit.
check grep -qF '"agent":{"plan":{"steps":30}}' "$record"
check grep -qxF 'leaked=' "$record"
# The review is parsed by OpenCode's rules and normalised to the contract.
check test "$(PYTHONPATH="$ROOT" python3 -c 'import sys;from dispatch import adapters;print(adapters.resolve("review","grok").parse_review(open(sys.argv[1],"rb").read()))' "$TMP/out-review")" \
  = "({'severity': 'red', 'message': 'Auth bypass', 'file': 'app.py', 'line': 3},)"
# Only the refreshed xAI token flows back: no session, no new credential, and no
# other provider's credential.
check test "$(credential xai-1)" = '{"refresh":"refreshed"}'
check test "$(credential other-1)" = '{"key":"stored"}'
check test "$(credential xai-2)" = none
check test "$(python3 -c 'import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute("SELECT count(*) FROM session").fetchone()[0])' "$signin_db")" = 0
check test -z "$(find "$TMP/runs" -mindepth 1 -maxdepth 1)"

# A token another run refreshed since this run started is never overwritten,
# whether this run left its copy alone or refreshed it too.
for scenario in stale race; do
  set_credential xai-1 '{"refresh":"stored"}'
  run "$scenario" "out-$scenario" --kind review --diff "$TMP/change.diff" --task t
  check test "$status" -eq 0
  check test "$(credential xai-1)" = '{"refresh":"other-run"}'
done

# The read tools count the whole enclosing git checkout as inside the workdir, so
# the workdir must be its top level.
if command -v git >/dev/null 2>&1; then
  mkdir -p "$TMP/repo/sub"
  git -C "$TMP/repo" init -q
  printf '%s' ok > "$state/scenario"
  direct out-repo-sub DISPATCH_WORKDIR="$TMP/repo/sub"
  check test "$status" -eq 2
  check grep -qF 'top level of its git or Mercurial checkout' "$TMP/out-repo-sub.stderr"
  direct out-repo-top DISPATCH_WORKDIR="$TMP/repo"
  check test "$status" -eq 0
fi
# The same holds for a Mercurial marker, which needs no hg binary, and for a
# workdir inside a .git directory, whose nearest marker is the checkout's.
mkdir -p "$TMP/hgtree/.hg" "$TMP/hgtree/sub" "$TMP/gitdir-repo/.git/inner"
printf '%s' ok > "$state/scenario"
for nested in "$TMP/hgtree/sub" "$TMP/gitdir-repo/.git/inner"; do
  direct out-nested DISPATCH_WORKDIR="$nested"
  check test "$status" -eq 2
  check grep -qF 'top level of its git or Mercurial checkout' "$TMP/out-nested.stderr"
done
direct out-hg-top DISPATCH_WORKDIR="$TMP/hgtree"
check test "$status" -eq 0

# Only the provider's own 429 event is a limit; a 403 says the plan is not
# entitled; a CLI exit of 75 for another reason is not a limit.
run limit out-limit --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 75
check grep -qF 'limit reached' "$TMP/out-limit.stderr"
run not-entitled out-403 --kind review --diff "$TMP/change.diff" --task t
check test "$status" -ne 0
check test "$status" -ne 75
check grep -qF 'not entitled to this model (HTTP 403)' "$TMP/out-403.stderr"
check_not grep -qF 'limit reached' "$TMP/out-403.stderr"
run exit-75 out-exit-75 --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 1
check_not grep -qF 'limit reached' "$TMP/out-exit-75.stderr"

# Grok serves review only; the dispatcher refuses every other kind, and the
# backend refuses one called directly.
for kind in implement passthrough; do
  run ok "out-$kind" --kind "$kind" --task t
  check test "$status" -eq 3
done
direct out-direct-implement DISPATCH_KIND=implement
check test "$status" -eq 2
check grep -qF 'only --kind review' "$TMP/out-direct-implement.stderr"

for bad_model in grok-example openai/gpt-example 'xai/../x' 'xai/-x'; do
  direct out-bad-model DISPATCH_MODEL="$bad_model"
  check test "$status" -eq 2
  check grep -qF 'xai/<model>' "$TMP/out-bad-model.stderr"
done

mkdir -p "$TMP/empty-home"
direct out-no-signin DISPATCH_GROK_HOME="$TMP/empty-home"
check test "$status" -eq 2
check grep -qF 'no sign-in database' "$TMP/out-no-signin.stderr"

# A ? or # in the sign-in or scratch path cannot cut the database path short.
odd_home="$TMP/odd?home#x"
mkdir -p "$odd_home/.local/share/opencode" "$TMP/odd?runs#x"
cp "$signin_db" "$odd_home/.local/share/opencode/opencode.db"
printf '%s' ok > "$state/scenario"
direct out-odd-path DISPATCH_GROK_HOME="$odd_home" TMPDIR="$TMP/odd?runs#x"
check test "$status" -eq 0
check test -z "$(find "$TMP" -maxdepth 1 -name 'odd' -o -maxdepth 1 -name 'odd?runs')"
check test -z "$(find "$TMP/odd?runs#x" -mindepth 1 -maxdepth 1)"

# A link that leaves the workdir could carry the read tools to the sign-in
# database; a link that stays inside is fine.
ln -s "$signin_db" "$TMP/work/leak"
printf '%s' ok > "$state/scenario"
direct out-link-out
check test "$status" -eq 2
check grep -qF 'link that leaves it: leak' "$TMP/out-link-out.stderr"
rm "$TMP/work/leak"
printf 'x\n' > "$TMP/work/inside.txt"
ln -s inside.txt "$TMP/work/inside-link"
direct out-link-in
check test "$status" -eq 0

# Run outside the dispatcher, the CLI still gets none of its caller's environment,
# and no PATH entry inside the workdir can supply a planted opencode.
mkdir -p "$TMP/work/bin"
printf '#!/bin/sh\ntouch "%s/planted-ran"\n' "$TMP" > "$TMP/work/bin/opencode"
chmod +x "$TMP/work/bin/opencode"
direct out-env NODE_OPTIONS=--require=/must-not-load HTTPS_PROXY=http://must-not-proxy \
  PATH="$TMP/work/bin:$TMP/bin:/usr/bin:/bin"
check test "$status" -eq 0
check grep -qxF 'leaked=' "$record"
check_not test -e "$TMP/planted-ran"
rm -rf "$TMP/work/bin"

# The sign-in may not be readable from the workdir, nor the scratch.
mkdir -p "$TMP/work/tmp-inside"
direct out-tmp-inside TMPDIR="$TMP/work/tmp-inside"
check test "$status" -eq 2
check grep -qF 'TMPDIR and DISPATCH_GROK_HOME outside the workdir' "$TMP/out-tmp-inside.stderr"
rm -rf "$TMP/work/tmp-inside"

# TERM through the dispatcher stops the CLI's whole tree and removes the scratch,
# with the run's copy of the sign-in in it, after keeping a token it refreshed.
set_credential xai-1 '{"refresh":"stored"}'
printf '%s' slow > "$state/scenario"
rm -f "$state/slow.pid"
HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_GROK_HOME="$TMP/grok-home" TMPDIR="$TMP/runs" \
  DISPATCH_MODEL="$MODEL" "$CLI" --harness grok --kind review --diff "$TMP/change.diff" --task t \
  --workdir "$TMP/work" --output "$TMP/out-slow" >/dev/null 2>&1 &
slow_pid=$!
for _ in $(seq 50); do [[ -s "$state/slow.pid" && -s "$state/slow-child.pid" ]] && break; sleep 0.1; done
check test -s "$state/slow.pid"
check test -s "$state/slow-child.pid"
check test -n "$(find "$TMP/runs" -mindepth 1 -maxdepth 1 -name 'grok-dispatch.*')"
kill -TERM "$slow_pid"
slow_status=0
wait "$slow_pid" || slow_status=$?
check test "$slow_status" -eq 143
for _ in $(seq 30); do [[ -z "$(find "$TMP/runs" -mindepth 1 -maxdepth 1)" ]] && break; sleep 0.2; done
check test -z "$(find "$TMP/runs" -mindepth 1 -maxdepth 1)"
check_not alive "$(cat "$state/slow.pid")"
check_not alive "$(cat "$state/slow-child.pid")"
check test "$(credential xai-1)" = '{"refresh":"before-term"}'

# Every end state of a review run and its final turn, as the outcome table in
# the backend's pull request lists it: scenario | deadline | exit status | CLI
# calls | published output, where a number is the findings every OpenCode parser
# reads, a number with * is read only by the registered review parser, x is
# output the review parser refuses, and - is no output.
outcome_table='
ok|none|0|1|1
fenced-uncut|none|0|1|0*
prose-no-session|none|0|1|x
summary|none|0|2|1
mixed|none|0|2|1
mixed-dropped|none|1|2|-
summary-twice|none|1|2|-
fenced-final|none|1|2|-
fenced-final-findings|none|0|2|1
hang|36|0|2|0
early-findings-hang|36|0|2|1
partial-line-hang|36|0|2|1
split-snapshot-dropped|36|1|2|-
split-snapshot-kept|36|0|2|1
dropped-finding|36|1|2|-
early-findings-silent-final|36|1|2|-
early-findings-no-session|36|124|1|-
stubborn-hang|36|0|2|0
final-cut-clean-exit|36|124|2|-
summary|past|124|0|-
exit-75|none|1|1|-'
published_findings() {
  [[ -e "$1" ]] || { printf -- '-'; return; }
  PYTHONPATH="$ROOT" python3 -c 'import sys;from dispatch.adapters import opencode
raw = open(sys.argv[1], "rb").read()
try: count = len(opencode.parse_review(raw))
except Exception: print("x", end=""); raise SystemExit
try: opencode.opencode_findings(raw); print(count, end="")
except Exception: print(f"{count}*", end="")' "$1"
}
while IFS='|' read -r scenario deadline want_status want_calls want_published; do
  [[ -n "$scenario" ]] || continue
  : > "$state/calls"
  rm -f "$state/hang-child.pid"
  printf '%s' "$scenario" > "$state/scenario"
  started="$(date +%s)"
  case "$deadline" in
    none) direct "out-table-$scenario-$deadline" ;;
    past) direct "out-table-$scenario-$deadline" DISPATCH_DEADLINE_EPOCH="$((started - 1))" ;;
    *) direct "out-table-$scenario-$deadline" DISPATCH_DEADLINE_EPOCH="$((started + deadline))" ;;
  esac
  got="$status|$(wc -l < "$state/calls" | tr -d ' ')|$(published_findings "$TMP/out-table-$scenario-$deadline")"
  [[ "$got" == "$want_status|$want_calls|$want_published" ]] \
    || { printf 'outcome %s (deadline %s): got %s, want %s\n' "$scenario" "$deadline" "$got" "$want_status|$want_calls|$want_published" >&2; exit 1; }
  pass=$((pass + 1))
  [[ "$deadline" != 36 ]] || check test "$(($(date +%s) - started))" -lt 36
  [[ ! -s "$state/hang-child.pid" ]] || check_not alive "$(cat "$state/hang-child.pid")"
  cp "$state/calls" "$state/calls.$scenario-$deadline"
done <<<"$outcome_table"

# The final turn resumes the run's session with every tool denied.
check grep -qF -- '--session ses_test1 --standalone --format json --agent plan' <(sed -n 2p "$state/calls.summary-none")
check grep -qF '|{"enabled_providers":["xai"],"permission":{"*":"deny"}}' <(sed -n 2p "$state/calls.summary-none")
check test "$(PYTHONPATH="$ROOT" python3 -c 'import sys;from dispatch import adapters;print(adapters.resolve("review","grok").parse_review(open(sys.argv[1],"rb").read()))' "$TMP/out-table-summary-none")" \
  = "({'severity': 'red', 'message': 'Late', 'file': 'app.py', 'line': 2},)"
# A run cut mid-event keeps its step events, but not the cut line or its prose.
check grep -qF '"type":"step_start"' "$TMP/out-table-partial-line-hang-36"
check_not grep -qF 'Exploring.' "$TMP/out-table-partial-line-hang-36"
check grep -qF 'final turn wrote no valid findings' "$TMP/out-table-dropped-finding-36.stderr"
check grep -qF 'ran out of time before its findings turn' "$TMP/out-table-early-findings-no-session-36.stderr"

# The dispatcher passes the deadline on to the backend.
: > "$state/calls"
printf '%s' hang > "$state/scenario"
started="$(date +%s)"
DISPATCH_DEADLINE_EPOCH="$((started + 36))" run hang out-hang-dispatched --kind review --diff "$TMP/change.diff" --task t
check test "$status" -eq 0
check test "$(($(date +%s) - started))" -lt 36
check grep -qF -- '--session ses_test1' <(sed -n 2p "$state/calls")
direct out-bad-deadline DISPATCH_DEADLINE_EPOCH=soon
check test "$status" -eq 2

printf 'grok backend tests: %s passed\n' "$pass"
