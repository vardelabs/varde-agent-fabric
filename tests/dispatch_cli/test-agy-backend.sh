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
report() { printf '{"conversation_id":"c1","status":"%s","response":"%s","error":%s,"num_turns":1,"finished_reason":"%s"%s}\n' "$@"; }
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
esac
EOF
chmod +x "$TMP/bin/agy"

reset_work() {
  rm -rf "$TMP/work"
  mkdir -p "$TMP/work/docs"
  printf 'old intro\n' > "$TMP/work/docs/intro.md"
  printf 'unnamed\n' > "$TMP/work/secret.md"
}
run() {  # run <scenario> <out-name> [extra dispatcher args...]
  printf '%s' "$1" > "$state/scenario"
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
check test -z "$(ls -A "$TMP/agy-home")"

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

write not-json out-notjson
check test "$status" -eq 1
check grep -qF 'agy wrote no JSON report' "$TMP/out-notjson.stderr"

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
