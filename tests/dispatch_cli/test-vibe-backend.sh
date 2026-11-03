#!/usr/bin/env bash
# The vibe backend against a fake `vibe` CLI: no account, network, or key.
set -euo pipefail
unset DISPATCH_MODEL DISPATCH_ROUTES_FILE DISPATCH_VIBE_HOME DISPATCH_DEADLINE_EPOCH MISTRAL_API_KEY VIBE_HOME

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
CLI="$ROOT/dispatch/cli/varde-agent-dispatch"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/vibe-backend-test.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/work/.vibe" "$TMP/caller-home" "$TMP/vibe-home" "$TMP/bin" "$TMP/state"
printf 'diff body\n' > "$TMP/change.diff"
printf 'MISTRAL_API_KEY=example-key\n' > "$TMP/vibe-home/.env"
# A reviewed tree may carry its own Vibe config; a run must never load it.
printf 'default_agent = "auto-approve"\n' > "$TMP/work/.vibe/config.toml"
state="$TMP/state"
MODEL=mistral/example-model

pass=0
check() { "$@"; pass=$((pass + 1)); }
check_not() { if "$@"; then printf 'unexpected match: %s\n' "$*" >&2; exit 1; fi; pass=$((pass + 1)); }

# The fake records what it was given, then writes the transcript and export the
# scenario names, the way the real CLI does on every exit.
cat > "$TMP/bin/vibe" <<EOF
#!/usr/bin/env bash
set -euo pipefail
state="$state"
EOF
cat >> "$TMP/bin/vibe" <<'EOF'
export_dir="" prompt_file=""
args=("$@")
while (($#)); do
  case "$1" in --output-dir) export_dir="$2"; shift 2 ;; --prompt-file) prompt_file="$2"; shift 2 ;; *) shift ;; esac
done
{
  printf 'argv=%s\n' "${args[*]}"
  printf 'cwd=%s\n' "$PWD"
  printf 'env=%s\n' "$(env | cut -d= -f1 | sort | tr '\n' ' ')"
  printf 'key_link=%s\n' "$(readlink -f -- "$VIBE_HOME/.env")"
  printf 'prompt=%s\n' "$(tr '\n' ' ' < "$prompt_file")"
  printf 'config=%s\n' "$(tr '\n' '|' < "$VIBE_HOME/config.toml")"
} > "$state/record"
export_record() { printf '{"outcome":"%s","error":%s,"exit_code":%s}' "$1" "$2" "$3" > "$export_dir/export.json"; }
assistant() { printf '[{"role":"user","type":"message","content":[{"type":"text","text":"task"}]},{"role":"assistant","type":"message","content":[{"type":"text","text":"%s"},{"type":"text","text":"%s"}]}]' "$1" "$2"; }
case "$(cat "$state/scenario")" in
  ok) assistant 'review ' 'text'; export_record finished null 0 ;;
  rate-limit) export_record infrastructure_failure '{"code":"rate_limit","message":"429"}' 2; exit 2 ;;
  deadline) printf '[]'; export_record deadline null 3; exit 3 ;;
  config-error)
    export_record config_error '{"code":"Provider said secret-sentinel-3c1e","message":"secret-sentinel-3c1e"}' 1; exit 1 ;;
  ends-with-user) printf '[{"role":"user","type":"message","content":[{"type":"text","text":"x"}]}]'; export_record finished null 0 ;;
  empty-text) assistant '' ' '; export_record finished null 0 ;;
  no-export) assistant 'review' 'text' ;;
  large)
    printf '[{"role":"tool","type":"message","content":[{"type":"text","text":"%s"}]},' "$(head -c 9437184 /dev/zero | tr '\0' x)"
    assistant 'review ' 'text' | cut -c2-; export_record finished null 0 ;;
  limit-status) assistant 'review' 'text'; export_record finished null 0; exit 75 ;;
esac
EOF
chmod +x "$TMP/bin/vibe"

run() {  # run <scenario> <out-name> [extra dispatcher args...]
  printf '%s' "$1" > "$state/scenario"
  local name="$2"; shift 2
  rm -f "$state/record"
  status=0
  HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" DISPATCH_VIBE_HOME="$TMP/vibe-home" \
    MISTRAL_API_KEY=must-not-forward DISPATCH_UNSAFE_SENTINEL=must-not-forward DISPATCH_MODEL="${RUN_MODEL:-$MODEL}" \
    "$CLI" --harness vibe --workdir "$TMP/work" --output "$TMP/$name" "$@" \
    >"$TMP/$name.stdout" 2>"$TMP/$name.stderr" || status=$?
}
review() { run "$1" "$2" --kind review --diff "$TMP/change.diff" --task private-task-sentinel; }

review ok out-review
check test "$status" -eq 0
check grep -qxF 'review text' "$TMP/out-review"
check test "$(stat -c '%a' "$TMP/out-review" 2>/dev/null || stat -f '%Lp' "$TMP/out-review")" = 600
record="$state/record"
check grep -qF -- '--output json --agent ask' "$record"
check_not grep -qF -- '--trust' "$record"
check_not grep -qF -- 'private-task-sentinel' <(grep '^argv=' "$record")
check grep -qxF "cwd=$TMP/work" "$record"
check grep -qxF 'env=HOME PATH PWD SHLVL TMPDIR VIBE_HOME _ ' "$record"
check grep -qxF "key_link=$TMP/vibe-home/.env" "$record"
check grep -qF 'prompt=Task for this run:  private-task-sentinel  --- BEGIN DIFF --- diff body --- END DIFF ---' "$record"
check grep -qF 'name = "example-model"|' "$record"
check grep -qF 'default_agent = "ask"|' "$record"
check grep -qF 'outside_workdir = "deny"|' "$record"
check grep -qF 'strict_config = true|' "$record"
check grep -qF 'enable_auto_approve = false|' "$record"
check grep -qF '[tools.bash]|permission = "never"|' "$record"
check_not grep -qF 'must-not-forward' "$record"
check_not compgen -G "${TMPDIR:-/tmp}/vibe-dispatch.*" >/dev/null

review rate-limit out-limit
check test "$status" -eq 75
check grep -qF 'limit reached' "$TMP/out-limit.stderr"

review deadline out-deadline
check test "$status" -eq 124
check grep -qF 'review ran out of time before its findings turn' "$TMP/out-deadline.stderr"

review config-error out-config
check test "$status" -eq 1
check grep -qF 'outcome=config_error error=unlisted' "$TMP/out-config.stderr"
check_not grep -qF 'secret-sentinel-3c1e' "$TMP/out-config.stderr"

review ends-with-user out-user
check test "$status" -eq 1
check grep -qF 'does not end with an assistant message' "$TMP/out-user.stderr"

review empty-text out-empty
check test "$status" -eq 1
check grep -qF 'no response text' "$TMP/out-empty.stderr"

review no-export out-noexport
check test "$status" -eq 1
check grep -qF 'no readable run export' "$TMP/out-noexport.stderr"

review large out-large
check test "$status" -eq 0
check grep -qxF 'review text' "$TMP/out-large"

review limit-status out-75
check test "$status" -eq 1

RUN_MODEL=example-model review ok out-bad-model
check test "$status" -ne 0
check grep -qF 'Vibe needs a mistral/<model> identifier' "$TMP/out-bad-model.stderr"
check test ! -e "$state/record"

run ok out-implement --kind implement --task task
check test "$status" -ne 0

mv "$TMP/vibe-home/.env" "$TMP/key.bak"
review ok out-nokey
check test "$status" -ne 0
check grep -qF 'Vibe has no API key file (.env) in DISPATCH_VIBE_HOME' "$TMP/out-nokey.stderr"
ln -s "$TMP/key.bak" "$TMP/vibe-home/.env"
review ok out-linkkey
check test "$status" -ne 0
check grep -qF 'Vibe has no API key file (.env) in DISPATCH_VIBE_HOME' "$TMP/out-linkkey.stderr"

printf 'vibe backend: %d checks passed\n' "$pass"
