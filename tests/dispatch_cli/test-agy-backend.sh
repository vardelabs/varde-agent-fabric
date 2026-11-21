#!/usr/bin/env bash
# The agy write backend against a fake `agy` CLI: no account, network, or sign-in.
set -euo pipefail
unset DISPATCH_MODEL DISPATCH_ROUTES_FILE DISPATCH_AGY_HOME DISPATCH_AGY_TIMEOUT_SECONDS DISPATCH_WRITE_PATHS GEMINI_API_KEY

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
CLI="$ROOT/dispatch/cli/varde-agent-dispatch"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/agy-backend-test.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/caller-home" "$TMP/agy-home" "$TMP/bin" "$TMP/state"
state="$TMP/state"
MODEL=example-model-high

pass=0
check() { "$@"; pass=$((pass + 1)); }
check_not() { if "$@"; then printf 'unexpected match: %s\n' "$*" >&2; exit 1; fi; pass=$((pass + 1)); }

# The fake records what it was given, edits its job folder the way the scenario
# names, and prints the JSON report the real CLI prints in print mode.
cat > "$TMP/bin/agy" <<EOF
#!/usr/bin/env bash
set -euo pipefail
state="$state"
EOF
cat >> "$TMP/bin/agy" <<'EOF'
settings=""
args=("$@")
while (($#)); do case "$1" in --settings) settings="$2"; shift 2 ;; *) shift ;; esac; done
{
  printf 'argv=%s\n' "${args[*]}"
  printf 'cwd=%s\n' "$PWD"
  printf 'home=%s\n' "$HOME"
  printf 'env=%s\n' "$(env | cut -d= -f1 | sort | tr '\n' ' ')"
  printf 'prompt=%s\n' "$(tr '\n' ' ')"
  printf 'settings=%s\n' "$(tr -d ' \n' < "$settings")"
  printf 'staged=%s\n' "$(find . -type f | sort | tr '\n' ' ')"
} > "$state/record"
# Like the real CLI, every run leaves its conversation in the home, named by its id.
conv="$(cat "$state/conv")"
data="$HOME/.gemini/antigravity-cli"
mkdir -p "$data/brain/$conv/.system_generated/logs" "$data/conversations" "$data/presence"
printf 'log\n' > "$data/brain/$conv/.system_generated/logs/transcript"
: > "$data/conversations/$conv.db"; : > "$data/conversations/$conv.db-wal"; : > "$data/presence/$conv.lock"
report() { printf '{"conversation_id":"%s","status":"%s","response":"%s","error":%s,"num_turns":1,"finished_reason":"%s"%s}\n' "$conv" "$@"; }
done_ok() { report SUCCESS 'wrote the copy' null stop ''; }
case "$(cat "$state/scenario")" in
  edit) printf 'new intro\n' > docs/intro.md; mkdir -p docs/new; printf 'fresh\n' > docs/new/page.md; done_ok ;;
  unchanged) done_ok ;;
  delete) rm docs/intro.md; done_ok ;;
  stray) printf 'x\n' > docs/intro.md; printf 'x\n' > other.md; done_ok ;;
  stray-dir) mkdir .scratch; done_ok ;;
  dir-link) mkdir ../elsewhere; mv docs/intro.md ../elsewhere/; rmdir docs; ln -s ../elsewhere docs; done_ok ;;
  link) rm docs/intro.md; ln -s /etc/hostname docs/intro.md; done_ok ;;
  binary) printf '\377\376\n' > docs/intro.md; done_ok ;;
  denied) report SUCCESS 'done' null stop ',"denied_actions":["command(ls)"]' ;;
  quota) report ERROR '' '"model is out of quota until tomorrow"' error ''; exit 1 ;;
  failed) report ERROR '' '"Provider said secret-sentinel-3c1e"' error ''; exit 1 ;;
  not-json) printf 'Error: something broke\n' ;;
  hang) sleep 30 ;;
  deep) printf 'new intro\n' > docs/intro.md; (cd "$data/brain/$conv" && mkdir -p "$(printf 'd/%.0s' {1..1100})"); done_ok ;;
esac
EOF
chmod +x "$TMP/bin/agy"

reset_work() {
  rm -rf "$TMP/work"
  mkdir -p "$TMP/work/docs"
  printf 'old intro\n' > "$TMP/work/docs/intro.md"
  printf 'unnamed\n' > "$TMP/work/secret.md"
}
CONV=0b5e7c1a-2f4d-4e6a-9c3b-8d1f2a3b4c5d
OTHER_CONV=7e2d9f10-1a2b-4c3d-8e4f-5a6b7c8d9e0f
agy_data="$TMP/agy-home/.gemini/antigravity-cli"
run_entries() {  # run_entries <conversation-id>: what one run leaves in the home
  printf '%s\n' "$agy_data/brain/$1" "$agy_data/conversations/$1.db" "$agy_data/conversations/$1.db-wal" \
    "$agy_data/presence/$1.lock"
}
none_exist() { local entry; while IFS= read -r entry; do [[ ! -e "$entry" && ! -L "$entry" ]] || return 1; done; }
all_exist() { local entry; while IFS= read -r entry; do [[ -e "$entry" ]] || return 1; done; }
run() {  # run <scenario> <out-name> [extra dispatcher args...]
  printf '%s' "$1" > "$state/scenario"
  printf '%s' "${RUN_CONV:-$CONV}" > "$state/conv"
  local name="$2"; shift 2
  reset_work
  rm -f "$state/record"
  status=0
  HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_AGY_HOME="$TMP/agy-home" \
    GEMINI_API_KEY=must-not-forward DISPATCH_UNSAFE_SENTINEL=must-not-forward DISPATCH_MODEL="${RUN_MODEL:-$MODEL}" \
    DISPATCH_AGY_TIMEOUT_SECONDS="${RUN_TIMEOUT:-60}" \
    "$CLI" --harness agy --workdir "$TMP/work" --output "$TMP/$name" "$@" \
    >"$TMP/$name.stdout" 2>"$TMP/$name.stderr" || status=$?
}
write() { run "$1" "$2" --kind write --task private-task-sentinel --path docs/intro.md --path docs/new/page.md; }
field() { python3 -c 'import json,sys; print(json.dumps(eval(sys.argv[2], {}, {"p": json.load(open(sys.argv[1]))})))' "$@"; }

write edit out-edit
check test "$status" -eq 0
check test "$(field "$TMP/out-edit" 'p["schema"]')" = '"write-proposal.v1"'
check test "$(field "$TMP/out-edit" '[c["path"] for c in p["changes"]]')" = '["docs/intro.md", "docs/new/page.md"]'
check test "$(field "$TMP/out-edit" '[c["after_text"] for c in p["changes"]]')" = '["new intro\n", "fresh\n"]'
old_sha="$(sha256sum "$TMP/work/docs/intro.md" | cut -d' ' -f1)"
check test "$(field "$TMP/out-edit" '[c["before_sha256"] for c in p["changes"]]')" = "[\"$old_sha\", null]"
check test "$(field "$TMP/out-edit" 'p["response"]')" = '"wrote the copy"'
check grep -qxF 'old intro' "$TMP/work/docs/intro.md"
check test ! -e "$TMP/work/docs/new"
record="$state/record"
check_not grep -qF -- 'private-task-sentinel' <(grep '^argv=' "$record")
check grep -qF 'private-task-sentinel' <(grep '^prompt=' "$record")
check grep -qF -- '- docs/intro.md - docs/new/page.md' "$record"
check grep -qF -- "--model $MODEL --mode accept-edits --disable-slash-commands" "$record"
check_not grep -qF -- 'dangerously-skip-permissions' "$record"
check grep -qF -- '--print-timeout 60s' "$record"
check grep -qxF "home=$TMP/agy-home" "$record"
check grep -qE "^cwd=${TMPDIR:-/tmp}/agy-dispatch\.[^/]+/job$" "$record"
check grep -qxF 'staged=./docs/intro.md ' "$record"
check grep -qxF 'env=AGY_CLI_DISABLE_AUTO_UPDATE HOME PATH PWD SHLVL TMPDIR _ ' "$record"
check grep -qF '"deny":["command(*)","mcp(*)","read_url(*)","execute_url(*)"]' "$record"
check grep -qF '"agentMode":"accept-edits","toolPermission":"request-review","allowNonWorkspaceAccess":false' "$record"
check_not grep -qF 'must-not-forward' "$record"
check_not compgen -G "${TMPDIR:-/tmp}/agy-dispatch.*" >/dev/null
check none_exist < <(run_entries "$CONV")
check test -z "$(find "$agy_data" -mindepth 2)"

# A run deletes only its own conversation: another one in the home stays.
mkdir -p "$agy_data/brain/$OTHER_CONV"
run_entries "$OTHER_CONV" | tail -n +2 | xargs touch
printf 'keep\n' > "$agy_data/brain/$OTHER_CONV/notes"
write edit out-neighbor
check test "$status" -eq 0
check none_exist < <(run_entries "$CONV")
check all_exist < <(run_entries "$OTHER_CONV")
check grep -qxF keep "$agy_data/brain/$OTHER_CONV/notes"

write unchanged out-unchanged
check test "$status" -eq 0
check test "$(field "$TMP/out-unchanged" 'p["changes"]')" = '[]'

write delete out-delete
check test "$status" -eq 0
check test "$(field "$TMP/out-delete" 'p["changes"]')" = "[{\"path\": \"docs/intro.md\", \"before_sha256\": \"$old_sha\", \"after_text\": null}]"

for scenario in stray stray-dir dir-link; do
  write "$scenario" "out-$scenario"
  check test "$status" -eq 1
  check grep -qF 'outside the named paths; the proposal is discarded' "$TMP/out-$scenario.stderr"
  check test ! -e "$TMP/out-$scenario"
done

write link out-link
check test "$status" -eq 1
check grep -qF 'not a bounded UTF-8 regular file' "$TMP/out-link.stderr"

write binary out-binary
check test "$status" -eq 1
check grep -qF 'not a bounded UTF-8 regular file' "$TMP/out-binary.stderr"

write denied out-denied
check test "$status" -eq 1
check grep -qF 'tried 1 action(s) its policy denies' "$TMP/out-denied.stderr"

write quota out-quota
check test "$status" -eq 75
check grep -qF 'limit reached' "$TMP/out-quota.stderr"

write failed out-failed
check test "$status" -eq 1
check grep -qF 'status=error finished_reason=error' "$TMP/out-failed.stderr"
check_not grep -qF 'secret-sentinel-3c1e' "$TMP/out-failed.stderr"
check none_exist < <(run_entries "$CONV")

write not-json out-notjson
check test "$status" -eq 1
check grep -qF 'agy wrote no JSON report' "$TMP/out-notjson.stderr"
# With no report there is no id to go by, so nothing is deleted rather than guessed at.
check all_exist < <(run_entries "$CONV")
rm -rf "$agy_data/brain/$CONV"; run_entries "$CONV" | tail -n +2 | xargs rm -f

# An id that is not a conversation id is never used as a path.
RUN_CONV=../../../escape write edit out-bad-id
check test "$status" -eq 0
check test -e "$TMP/agy-home/escape/.system_generated/logs/transcript"
check test -e "$TMP/agy-home/escape.db"
rm -rf "$TMP/agy-home/escape" "$TMP/agy-home/escape".*

# A conversation folder that is a link loses only the link, never what it points at.
mkdir -p "$TMP/outside"
ln -s "$TMP/outside" "$agy_data/brain/$CONV"
write edit out-brain-link
check test "$status" -eq 0
check none_exist < <(run_entries "$CONV")
check test -e "$TMP/outside/.system_generated/logs/transcript"

# A conversation that cannot be deleted leaves a warning, and the proposal still stands.
mkdir -p "$agy_data/brain/$CONV"
chmod 0500 "$agy_data/brain"
write edit out-prune-fails
chmod 0700 "$agy_data/brain"
check test "$status" -eq 0
check test "$(field "$TMP/out-prune-fails" 'p["schema"]')" = '"write-proposal.v1"'
check grep -qF "could not delete this run's conversation" "$TMP/out-prune-fails.stderr"
rm -rf "$agy_data/brain/$CONV"

# A cleanup that crashes outright still leaves the run its proposal.
write deep out-prune-crash
check test "$status" -eq 0
check test "$(field "$TMP/out-prune-crash" '[c["path"] for c in p["changes"]]')" = '["docs/intro.md"]'
check grep -qF 'the conversation cleanup failed; the run continues' "$TMP/out-prune-crash.stderr"
rm -rf "$agy_data/brain/$CONV"

RUN_TIMEOUT=1 write hang out-hang
check test "$status" -eq 124
check grep -qF 'agy ran out of time' "$TMP/out-hang.stderr"

ln -sf /etc/hostname "$TMP/link-target"
printf 'edit' > "$state/scenario"
reset_work
ln -s "$TMP/link-target" "$TMP/work/docs/linked.md"
status=0
HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_AGY_HOME="$TMP/agy-home" DISPATCH_MODEL="$MODEL" \
  "$CLI" --harness agy --workdir "$TMP/work" --kind write --task t --path docs/linked.md \
  >/dev/null 2>"$TMP/linked.stderr" || status=$?
check test "$status" -eq 1
check grep -qF 'a named path is a link' "$TMP/linked.stderr"

run edit out-dotdot --kind write --task t --path ../secret.md
check test "$status" -eq 1
check grep -qF 'unsafe relative path' "$TMP/out-dotdot.stderr"

run edit out-nopath --kind write --task t
check test "$status" -eq 2
check grep -qF 'write requires at least one --path' "$TMP/out-nopath.stderr"

run edit out-absolute --kind write --task t --path /etc/hostname
check test "$status" -eq 2

run edit out-review --kind review --task t --diff "$TMP/work/secret.md"
check test "$status" -eq 3
check grep -qF 'harness agy cannot serve kind review' "$TMP/out-review.stderr"

run edit out-implement --kind implement --task t
check test "$status" -eq 3

RUN_MODEL='-bad model' write edit out-bad-model
check test "$status" -ne 0
check grep -qF 'model identifier is invalid' "$TMP/out-bad-model.stderr"
check test ! -e "$state/record"

# A direct backend call under a loose umask still writes an owner-only proposal.
direct="$TMP/direct"
mkdir -p "$direct/job/docs"
printf 'new\n' > "$direct/job/docs/intro.md"
printf '{"docs/intro.md": null}' > "$direct/manifest.json"
printf '{"status": "SUCCESS", "response": "ok"}' > "$direct/report.json"
(umask 022 && DISPATCH_WRITE_PATHS=docs/intro.md python3 "$ROOT/dispatch/cli/backends/agy_write.py" result \
  "$direct/report.json" 0 0 "$direct/manifest.json" "$direct/job" "$MODEL" "$direct/out")
check test "$(stat -c %a "$direct/out")" = 600

printf 'agy backend: %d checks passed\n' "$pass"
