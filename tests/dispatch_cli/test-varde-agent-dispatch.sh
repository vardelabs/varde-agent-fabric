#!/usr/bin/env bash
set -euo pipefail
unset DISPATCH_MODEL DISPATCH_ROUTES_FILE DISPATCH_CODEX_SANDBOX DISPATCH_MAX_TASK_BYTES DISPATCH_OPENCODE_HOME
unset OPENAI_API_KEY OPENCODE_API_KEY OPENCODE_GO_API_KEY ANTHROPIC_API_KEY OPENROUTER_API_KEY
unset GEMINI_API_KEY GOOGLE_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
unset AZURE_OPENAI_API_KEY COHERE_API_KEY MISTRAL_API_KEY XAI_API_KEY
REAL_OPENCODE="$(command -v opencode || true)"

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
CLI="$ROOT/dispatch/cli/varde-agent-dispatch"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/dispatch-test.XXXXXX")"
# The CLI prints readlink -f canonical paths; macOS resolves /var and /tmp to /private.
TMP="$(cd "$TMP" && pwd -P)"
trap '[[ -z "${SERVER_PID:-}" ]] || kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$TMP"' EXIT

mkdir -p "$TMP/work" "$TMP/caller-home" "$TMP/opencode-home"
printf 'diff body\n' > "$TMP/change.diff"
cat > "$TMP/routes.json" <<'EOF'
{
  "schema": "dispatch-routes.v1",
  "routes": {
    "test/model": {
      "default_harness": "codex",
      "harnesses": ["codex", "opencode"],
      "target": "local",
      "kinds": ["review", "implement", "passthrough"],
      "fallback": {"policy": "none"}
    },
    "caller/model": {
      "default_harness": "codex",
      "harnesses": ["codex"],
      "target": "local",
      "kinds": ["passthrough"],
      "fallback": {"policy": "none"}
    },
    "fallback/model": {
      "default_harness": "codex",
      "harnesses": ["codex"],
      "target": "remote",
      "kinds": ["review"],
      "fallback": {"policy": "remote-unavailable", "harness": "codex", "target": "local"}
    },
    "opencode-go/qwen3.8-flash": {
      "default_harness": "opencode",
      "harnesses": ["opencode"],
      "target": "remote",
      "kinds": ["review"],
      "fallback": {"policy": "remote-unavailable", "harness": "opencode", "target": "local"}
    }
  }
}
EOF

cat > "$TMP/remote-routes.json" <<'EOF'
{
  "schema": "dispatch-routes.v1",
  "routes": {
    "remote/model": {
      "default_harness": "codex",
      "harnesses": ["codex"],
      "target": "remote",
      "kinds": ["implement"],
      "fallback": {"policy": "none"}
    }
  }
}
EOF

pass=0
check() { "$@"; pass=$((pass + 1)); }

check bash -n "$CLI"
check bash -n "$ROOT/dispatch/cli/backends/codex.sh"
check test -x "$CLI"
check test -x "$ROOT/dispatch/cli/backends/codex.sh"
help_text="$($CLI --help 2>&1 || true)"
check grep -qF 'review or implement' <<<"$help_text"
check grep -qF 'use a Git worktree for isolated review' <<<"$help_text"

if "$CLI" --harness missing --kind passthrough --task x --workdir "$TMP/work" 2>"$TMP/err"; then
  printf 'unsupported harness unexpectedly succeeded\n' >&2; exit 1
fi
check grep -q 'unsupported harness' "$TMP/err"

if "$CLI" --harness codex --kind review --task x --workdir "$TMP/work" 2>"$TMP/err"; then
  printf 'review without diff unexpectedly succeeded\n' >&2; exit 1
fi
check grep -q -- '--diff' "$TMP/err"

oversized_implement_task="$(python3 -c 'print("x" * 16385, end="")')"
if DISPATCH_MAX_TASK_BYTES=120000 DISPATCH_TIMEOUT_SECONDS=300 \
  DISPATCH_GATEWAY_URL=https://127.0.0.1:1 DISPATCH_GATEWAY_TOKEN=not-used \
  "$CLI" --model remote/model --routes "$TMP/remote-routes.json" --kind implement \
  --task "$oversized_implement_task" --workdir "$TMP/work" --repository org/repo \
  --correlation-id cli-size-test --path source.py --output "$TMP/out-oversized-implement" \
  >"$TMP/stdout-oversized-implement" 2>"$TMP/err-oversized-implement"; then
  printf 'oversized remote implement task unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'implement task exceeds 16384 bytes' "$TMP/err-oversized-implement"

if DISPATCH_TIMEOUT_SECONDS=901 DISPATCH_GATEWAY_URL=https://127.0.0.1:1 DISPATCH_GATEWAY_TOKEN=not-used \
  "$CLI" --model remote/model --routes "$TMP/remote-routes.json" --kind implement \
  --task valid --workdir "$TMP/work" --repository org/repo \
  --correlation-id cli-timeout-test --path source.py --output "$TMP/out-timeout-range" \
  >"$TMP/stdout-timeout-range" 2>"$TMP/err-timeout-range"; then
  printf 'remote implement timeout above 900 unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'DISPATCH_TIMEOUT_SECONDS must be from 1 to 900 for remote routes' "$TMP/err-timeout-range"

mkdir -p "$TMP/bin"
cat > "$TMP/bin/codex" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
output=''
prompt=''
working_dir=''
config=''
for arg in "$@"; do
  [[ "$arg" != *'diff body'* && "$arg" != *'private-task-sentinel'* ]] || exit 94
done
while (($#)); do
  if [[ "$1" == '-o' ]]; then output="$2"; shift 2
  elif [[ "$1" == '-C' ]]; then working_dir="$2"; shift 2
  elif [[ "$1" == '-c' ]]; then config+="$2;"; shift 2
  elif [[ "$1" == '--' ]]; then
    shift
    [[ $# -eq 1 && "$1" == '-' ]] || exit 92
    prompt="$(cat)"
    break
  else shift
  fi
done
[[ "$(printenv)" != *'diff body'* && "$(printenv)" != *'private-task-sentinel'* ]] || exit 93
[[ -z "${DISPATCH_UNSAFE_SENTINEL:-}${OPENAI_API_KEY:-}" ]] || exit 90
printf 'model=%s\nsandbox=%s\ntarget=%s\noverridden=%s\nfallback=%s\nfallback_harness=%s\nfallback_target=%s\ncwd=%s\nconfig=%s\n' "${DISPATCH_MODEL:-}" "${DISPATCH_CODEX_SANDBOX:-}" "${DISPATCH_TARGET:-}" "${DISPATCH_HARNESS_OVERRIDDEN:-}" "${DISPATCH_FALLBACK_POLICY:-}" "${DISPATCH_FALLBACK_HARNESS:-}" "${DISPATCH_FALLBACK_TARGET:-}" "$working_dir" "$config" > "$output"
printf '%s\n' "$prompt" >> "$output"
EOF
chmod +x "$TMP/bin/codex"

if ! DISPATCH_CODEX_SANDBOX=workspace-write DISPATCH_UNSAFE_SENTINEL=must-not-forward OPENAI_API_KEY=must-not-forward \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --kind review \
  --diff "$TMP/change.diff" --model test/model --routes "$TMP/routes.json" --task private-task-sentinel --workdir "$TMP/work" \
  --output "$TMP/out" >"$TMP/stdout" 2>"$TMP/err"; then
  printf 'fake codex dispatch unexpectedly failed\n' >&2
  cat "$TMP/err" >&2
  exit 1
fi
check test -s "$TMP/out"
check grep -qF "$TMP/out" "$TMP/stdout"
check grep -qF 'model=test/model' "$TMP/out"
check grep -qF 'sandbox=workspace-write' "$TMP/out"
check grep -qF 'target=local' "$TMP/out"
check grep -qF "cwd=$TMP/work" "$TMP/out"
check grep -qF 'overridden=false' "$TMP/out"
check grep -qF 'fallback=none' "$TMP/out"
check grep -qF 'BEGIN DIFF' "$TMP/out"
check grep -qF 'diff body' "$TMP/out"
check grep -qF 'private-task-sentinel' "$TMP/out"
check grep -qxF 'config=model_reasoning_effort=high;' "$TMP/out"

if PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex --kind implement \
  --model test/model --routes "$TMP/routes.json" --task codex-implement --workdir "$TMP/work" \
  --output "$TMP/out-codex-implement-denied" >"$TMP/stdout-codex-implement-denied" 2>"$TMP/err-codex-implement-denied"; then
  printf 'fake codex implement without write sandbox unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'Codex implement requires DISPATCH_CODEX_SANDBOX=workspace-write' "$TMP/err-codex-implement-denied"

if ! DISPATCH_CODEX_SANDBOX=workspace-write PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex --kind implement \
  --model test/model --routes "$TMP/routes.json" --task codex-implement --workdir "$TMP/work" \
  --output "$TMP/out-codex-implement" >"$TMP/stdout-codex-implement" 2>"$TMP/err-codex-implement"; then
  printf 'fake codex implement dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-codex-implement" >&2
  exit 1
fi
check grep -qF 'sandbox=workspace-write' "$TMP/out-codex-implement"
check grep -qxF 'config=' "$TMP/out-codex-implement"

cat > "$TMP/bin/opencode" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
version="$(cat "$(dirname "$0")/opencode-version")"
if [[ "${1:-}" == --version ]]; then printf '%s\n' "$version"; exit 0; fi
[[ "${1:-}" == run ]] || exit 91
[[ "${OPENCODE_DISABLE_AUTOUPDATE:-}" == 1 ]] || exit 88
shift
prompt="${1:-}"
shift
if [[ "$version" == *v2.* ]]; then
  [[ "${1:-}" == --standalone ]] || exit 92
  [[ "${OPENCODE_DISABLE_PROJECT_CONFIG:-}" == 1 ]] || exit 89
else
  [[ "${1:-}" == --pure ]] || exit 92
  [[ -z "${OPENCODE_DISABLE_PROJECT_CONFIG:-}" ]] || exit 89
fi
shift
[[ "${1:-}" == --format && "${2:-}" == json ]] || exit 93
shift 2
[[ "${1:-}" == --agent && "${2:-}" == plan ]] || [[ "${1:-}" == --agent && "${2:-}" == build ]] || exit 94
agent="$2"
shift 2
[[ "${1:-}" == --model ]] || exit 95
model="$2"
shift 2
workdir="$PWD"
if [[ "$version" != *v2.* ]]; then
  [[ "${1:-}" == --dir ]] || exit 96
  workdir="$2"
  shift 2
fi
diff=''
if [[ "${1:-}" == --file ]]; then diff="$2"; shift 2; fi
[[ $# -eq 0 ]] || exit 97
[[ -z "${DISPATCH_UNSAFE_SENTINEL:-}${OPENAI_API_KEY:-}${OPENCODE_GO_API_KEY:-}" ]] || exit 90
[[ -n "$diff" && -f "$diff" ]] || [[ -z "$diff" ]] || exit 98
stdin_text=''
[[ -t 0 ]] || stdin_text="$(cat)"
fail_mode="$(cat "$(dirname "$0")/opencode-fail" 2>/dev/null || true)"
if [[ "$fail_mode" == event ]]; then
  printf '%s\n' '{"type":"step_start"}' '{"type":"tool_use","part":{"state":{"type":"error","output":"quoted-file-text"}}}' '{"type":"error","error":{"name":"UnknownError","data":{"message":"Model not found: test/model"}}}'
  exit 3
elif [[ "$fail_mode" == plain ]]; then
  printf 'plain failure line\n'
  exit 4
fi
if [[ "$model" == "opencode-go/qwen3.8-flash" ]]; then
  printf '%s\n' '{"type":"text","part":{"text":"[]"}}' '{"type":"step_finish","reason":"stop"}'
  exit 0
fi
printf '{"type":"text","version":"%s","agent":"%s","model":"%s","workdir":"%s","cwd":"%s","kind":"%s","diff":"%s","home":"%s","models":"%s|%s","stdin":"%s","text":"%s"}\n' "$version" "$agent" "$model" "$workdir" "$PWD" "${DISPATCH_KIND:-}" "$diff" "${HOME:-}" "${OPENCODE_MODELS_PATH:-}" "${OPENCODE_DISABLE_MODELS_FETCH:-}" "$stdin_text" "$prompt"
printf '{"type":"step_finish","reason":"stop"}\n'
EOF
chmod +x "$TMP/bin/opencode"
printf '1.18.32\n' > "$TMP/bin/opencode-version"

if [[ -n "$REAL_OPENCODE" ]]; then
  real_help="$($REAL_OPENCODE run --help 2>&1)"
  real_flags=(--pure --format --agent --model --dir --file)
  [[ "$($REAL_OPENCODE --version 2>&1)" != *v2.* ]] || real_flags=(--standalone --format --agent --model --file)
  for flag in "${real_flags[@]}"; do
    check grep -q -- "$flag" <<<"$real_help"
  done
fi

if ! HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" DISPATCH_UNSAFE_SENTINEL=must-not-forward OPENAI_API_KEY=must-not-forward OPENCODE_GO_API_KEY=must-not-forward \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness opencode --kind review \
  --diff "$TMP/change.diff" --model test/model --routes "$TMP/routes.json" --task opencode-review \
  --workdir "$TMP/work" --output "$TMP/out-opencode-review" >"$TMP/stdout-opencode-review" 2>"$TMP/err-opencode-review"; then
  printf 'fake opencode review dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-opencode-review" >&2
  exit 1
fi
check test -s "$TMP/out-opencode-review"
check grep -qF '"agent":"plan"' "$TMP/out-opencode-review"
check grep -qF '"model":"test/model"' "$TMP/out-opencode-review"
check grep -qF '"kind":"review"' "$TMP/out-opencode-review"
check grep -qF '"diff":"'"$TMP/change.diff"'"' "$TMP/out-opencode-review"
check grep -qF '"home":"'"$TMP/opencode-home"'"' "$TMP/out-opencode-review"
check grep -qF '"type":"step_finish"' "$TMP/out-opencode-review"
check grep -qF '"workdir":"'"$TMP/work"'"' "$TMP/out-opencode-review"

for fail_mode in event plain; do
  printf '%s\n' "$fail_mode" > "$TMP/bin/opencode-fail"
  fail_status=0
  HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" PATH="$TMP/bin:/usr/bin:/bin" \
    "$CLI" --harness opencode --kind review --diff "$TMP/change.diff" --model test/model --routes "$TMP/routes.json" \
    --task x --workdir "$TMP/work" --output "$TMP/out-opencode-fail-$fail_mode" >"$TMP/stdout-opencode-fail-$fail_mode" 2>"$TMP/err-opencode-fail-$fail_mode" \
    || fail_status=$?
  printf '%s\n' "$fail_status" > "$TMP/status-opencode-fail-$fail_mode"
  check test ! -e "$TMP/out-opencode-fail-$fail_mode"
done
rm -f "$TMP/bin/opencode-fail"
check test "$(cat "$TMP/status-opencode-fail-event")" -eq 3
check grep -qF 'opencode run failed with exit status 3' "$TMP/err-opencode-fail-event"
check grep -qF 'Model not found: test/model' "$TMP/err-opencode-fail-event"
check test "$(grep -c 'step_start' "$TMP/err-opencode-fail-event")" -eq 0
check test "$(grep -c 'quoted-file-text' "$TMP/err-opencode-fail-event")" -eq 0
check test "$(cat "$TMP/status-opencode-fail-plain")" -eq 4
check grep -qF 'opencode reported no error event' "$TMP/err-opencode-fail-plain"
check test "$(grep -c 'plain failure line' "$TMP/err-opencode-fail-plain")" -eq 0

printf 'opencode v2.0.18\n' > "$TMP/bin/opencode-version"
if ! printf 'caller-stdin' | HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" DISPATCH_UNSAFE_SENTINEL=must-not-forward OPENAI_API_KEY=must-not-forward \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness opencode --kind review \
  --diff "$TMP/change.diff" --model test/model --routes "$TMP/routes.json" --task opencode-v2-review \
  --workdir "$TMP/work" --output "$TMP/out-opencode-v2-review" >"$TMP/stdout-opencode-v2-review" 2>"$TMP/err-opencode-v2-review"; then
  printf 'fake opencode v2 review dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-opencode-v2-review" >&2
  exit 1
fi
check grep -qF '"agent":"plan"' "$TMP/out-opencode-v2-review"
check grep -qF '"cwd":"'"$TMP/work"'"' "$TMP/out-opencode-v2-review"
check grep -qF '"diff":"'"$TMP/change.diff"'"' "$TMP/out-opencode-v2-review"
check grep -qF '"home":"'"$TMP/opencode-home"'"' "$TMP/out-opencode-v2-review"
check grep -qF '"stdin":""' "$TMP/out-opencode-v2-review"
check grep -qF '"models":"|"' "$TMP/out-opencode-v2-review"

printf '{}\n' > "$TMP/models.json"
chmod 644 "$TMP/models.json"
if ! HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" DISPATCH_OPENCODE_MODELS_PATH="$TMP/models.json" \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness opencode --kind review --diff "$TMP/change.diff" --model test/model \
  --routes "$TMP/routes.json" --task pinned-catalog --workdir "$TMP/work" --output "$TMP/out-opencode-catalog" \
  >"$TMP/stdout-opencode-catalog" 2>"$TMP/err-opencode-catalog"; then
  printf 'opencode review with a pinned catalog unexpectedly failed\n' >&2
  cat "$TMP/err-opencode-catalog" >&2
  exit 1
fi
check grep -qF '"models":"'"$TMP/models.json"'|1"' "$TMP/out-opencode-catalog"
cp "$TMP/models.json" "$TMP/models-group.json"
chmod 620 "$TMP/models-group.json"
chmod 666 "$TMP/models.json"
for catalog in "$TMP/models-group.json" "$TMP/models.json" relative.json "$TMP/missing.json"; do
  if HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" DISPATCH_OPENCODE_MODELS_PATH="$catalog" \
    PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness opencode --kind review --diff "$TMP/change.diff" --model test/model \
    --routes "$TMP/routes.json" --task x --workdir "$TMP/work" --output "$TMP/out-opencode-bad-catalog" \
    >/dev/null 2>"$TMP/err-opencode-bad-catalog"; then
    printf 'opencode accepted an unsafe catalog: %s\n' "$catalog" >&2; exit 1
  fi
  check grep -qF 'DISPATCH_OPENCODE_MODELS_PATH must' "$TMP/err-opencode-bad-catalog"
done

if ! HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" DISPATCH_OPENCODE_ALLOW_WRITE=1 \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness opencode --kind implement \
  --model test/model --routes "$TMP/routes.json" --task opencode-v2-implement --workdir "$TMP/work" \
  --output "$TMP/out-opencode-v2-implement" >"$TMP/stdout-opencode-v2-implement" 2>"$TMP/err-opencode-v2-implement"; then
  printf 'fake opencode v2 implement dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-opencode-v2-implement" >&2
  exit 1
fi
check grep -qF '"agent":"build"' "$TMP/out-opencode-v2-implement"
check grep -qF '"cwd":"'"$TMP/work"'"' "$TMP/out-opencode-v2-implement"

printf 'unknown\n' > "$TMP/bin/opencode-version"
if HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" PATH="$TMP/bin:/usr/bin:/bin" \
  "$CLI" --harness opencode --kind review --diff "$TMP/change.diff" --model test/model --routes "$TMP/routes.json" \
  --task x --workdir "$TMP/work" --output "$TMP/out-opencode-bad-version" >"$TMP/stdout-opencode-bad-version" 2>"$TMP/err-opencode-bad-version"; then
  printf 'opencode with an unparseable version unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'could not determine the opencode CLI version' "$TMP/err-opencode-bad-version"
printf '1.18.32\n' > "$TMP/bin/opencode-version"

if ! HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" DISPATCH_OPENCODE_ALLOW_WRITE=1 DISPATCH_UNSAFE_SENTINEL=must-not-forward OPENCODE_GO_API_KEY=must-not-forward \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness opencode --kind passthrough \
  --model test/model --routes "$TMP/routes.json" --task opencode-build --workdir "$TMP/work" \
  --output "$TMP/out-opencode-build" >"$TMP/stdout-opencode-build" 2>"$TMP/err-opencode-build"; then
  printf 'fake opencode passthrough dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-opencode-build" >&2
  exit 1
fi
check grep -qF '"agent":"build"' "$TMP/out-opencode-build"
check grep -qF '"kind":"passthrough"' "$TMP/out-opencode-build"

if ! HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" DISPATCH_OPENCODE_ALLOW_WRITE=1 DISPATCH_UNSAFE_SENTINEL=must-not-forward OPENCODE_GO_API_KEY=must-not-forward \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness opencode --kind implement \
  --model test/model --routes "$TMP/routes.json" --task opencode-implement --workdir "$TMP/work" \
  --output "$TMP/out-opencode-implement" >"$TMP/stdout-opencode-implement" 2>"$TMP/err-opencode-implement"; then
  printf 'fake opencode implement dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-opencode-implement" >&2
  exit 1
fi
check grep -qF '"agent":"build"' "$TMP/out-opencode-implement"
check grep -qF '"kind":"implement"' "$TMP/out-opencode-implement"

if HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" PATH="$TMP/bin:/usr/bin:/bin" \
  "$CLI" --harness opencode --kind implement --model test/model --routes "$TMP/routes.json" --task x \
  --workdir "$TMP/work" --output "$TMP/out-opencode-no-write" >"$TMP/stdout-opencode-no-write" 2>"$TMP/err-opencode-no-write"; then
  printf 'opencode implement without write opt-in unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'DISPATCH_OPENCODE_ALLOW_WRITE=1' "$TMP/err-opencode-no-write"

if HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" PATH="$TMP/bin:/usr/bin:/bin" \
  "$CLI" --harness opencode --kind passthrough --model test/model --routes "$TMP/routes.json" --task x \
  --workdir "$TMP/work" --output "$TMP/out-opencode-passthrough-no-write" >"$TMP/stdout-opencode-passthrough-no-write" 2>"$TMP/err-opencode-passthrough-no-write"; then
  printf 'opencode passthrough without write opt-in unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'OpenCode implementation requires DISPATCH_OPENCODE_ALLOW_WRITE=1 (kind: passthrough)' "$TMP/err-opencode-passthrough-no-write"

if HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP" DISPATCH_OPENCODE_ALLOW_WRITE=1 PATH="$TMP/bin:/usr/bin:/bin" \
  "$CLI" --harness opencode --kind passthrough --model test/model --routes "$TMP/routes.json" --task x \
  --workdir "$TMP/work" --output "$TMP/out-opencode-ancestor" >"$TMP/stdout-opencode-ancestor" 2>"$TMP/err-opencode-ancestor"; then
  printf 'ancestor opencode home unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'must not contain the caller home' "$TMP/err-opencode-ancestor"

if HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME=/ DISPATCH_OPENCODE_ALLOW_WRITE=1 PATH="$TMP/bin:/usr/bin:/bin" \
  "$CLI" --harness opencode --kind passthrough --model test/model --routes "$TMP/routes.json" --task x \
  --workdir "$TMP/work" --output "$TMP/out-opencode-root" >"$TMP/stdout-opencode-root" 2>"$TMP/err-opencode-root"; then
  printf 'root opencode home unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'filesystem root' "$TMP/err-opencode-root"

if HOME="$TMP/caller-home" PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness opencode --kind passthrough \
  --model test/model --routes "$TMP/routes.json" --task x --workdir "$TMP/work" \
  --output "$TMP/out-opencode-no-home" >"$TMP/stdout-opencode-no-home" 2>"$TMP/err-opencode-no-home"; then
  printf 'opencode without dedicated home unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'DISPATCH_OPENCODE_HOME' "$TMP/err-opencode-no-home"

mkdir -p "$TMP/caller-home/nested-opencode-home"
if HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/caller-home/nested-opencode-home" PATH="$TMP/bin:/usr/bin:/bin" \
  "$CLI" --harness opencode --kind passthrough --model test/model --routes "$TMP/routes.json" --task x \
  --workdir "$TMP/work" --output "$TMP/out-opencode-nested" >"$TMP/stdout-opencode-nested" 2>"$TMP/err-opencode-nested"; then
  printf 'nested opencode home unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'outside the caller home' "$TMP/err-opencode-nested"

if HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" PATH="$TMP/bin:/usr/bin:/bin" \
  "$CLI" --harness opencode --kind review --model test/model --routes "$TMP/routes.json" --task x \
  --workdir "$TMP/work" --output "$TMP/out-opencode-no-diff" >"$TMP/stdout-opencode-no-diff" 2>"$TMP/err-opencode-no-diff"; then
  printf 'opencode review without diff unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF -- '--diff' "$TMP/err-opencode-no-diff"

cp "$TMP/routes.json" "$TMP/invalid-routes.json"
python3 - "$TMP/invalid-routes.json" <<'PY'
import json
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text())
data["routes"]["fallback/model"]["target"] = "local"
path.write_text(json.dumps(data))
PY

if PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model fallback/model --routes "$TMP/invalid-routes.json" \
  --kind passthrough --task x --workdir "$TMP/work" --output "$TMP/out-fallback" \
  >"$TMP/stdout-fallback" 2>"$TMP/err-fallback"; then
  printf 'local remote-unavailable route unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'remote primary route' "$TMP/err-fallback"
check test ! -e "$TMP/out-fallback"
check test ! -s "$TMP/stdout-fallback"

if PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model test/model --kind passthrough \
  --task x --workdir "$TMP/work" --output "$TMP/out-no-routes" \
  >"$TMP/stdout-no-routes" 2>"$TMP/err-no-routes"; then
  printf 'model dispatch without routes unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'routes must name a route configuration file' "$TMP/err-no-routes"

if PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex --routes "$TMP/routes.json" --kind passthrough \
  --task x --workdir "$TMP/work" --output "$TMP/out-routes-without-model" \
  >"$TMP/stdout-routes-without-model" 2>"$TMP/err-routes-without-model"; then
  printf 'routes without model unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF -- '--routes requires --model' "$TMP/err-routes-without-model"

if ! DISPATCH_MODEL=legacy/model DISPATCH_ROUTES_FILE= PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex \
  --kind passthrough --task x --workdir "$TMP/work" --output "$TMP/out-legacy-env" \
  >"$TMP/stdout-legacy-env" 2>"$TMP/err-legacy-env"; then
  printf 'legacy model environment dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-legacy-env" >&2
  exit 1
fi
check grep -qF 'model=legacy/model' "$TMP/out-legacy-env"
check grep -qF 'fallback=' "$TMP/out-legacy-env"

if PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness missing --kind passthrough \
  --model test/model --routes "$TMP/routes.json" --task x --workdir "$TMP/work" \
  --output "$TMP/out-incompatible" >"$TMP/stdout-incompatible" 2>"$TMP/err-incompatible"; then
  printf 'incompatible harness unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'not approved for model' "$TMP/err-incompatible"

if ! DISPATCH_MODEL=caller/model DISPATCH_ROUTES_FILE="$TMP/routes.json" PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex \
  --kind passthrough --task x --workdir "$TMP/work" --output "$TMP/out-env" \
  >"$TMP/stdout-env" 2>"$TMP/err-env"; then
  printf 'caller model dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-env" >&2
  exit 1
fi
check grep -qF 'model=caller/model' "$TMP/out-env"

if DISPATCH_MAX_TASK_BYTES=10 PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex \
  --kind review --diff "$TMP/change.diff" --task x --workdir "$TMP/work" \
  --output "$TMP/out-large" >"$TMP/stdout-large" 2>"$TMP/err-large"; then
  printf 'oversized diff unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'exceed DISPATCH_MAX_TASK_BYTES' "$TMP/err-large"

if DISPATCH_CODEX_SANDBOX=danger-full-access PATH="$TMP/bin:/usr/bin:/bin" "$CLI" \
  --harness codex --kind passthrough --task x --workdir "$TMP/work" \
  --output "$TMP/out-danger" >"$TMP/stdout-danger" 2>"$TMP/err-danger"; then
  printf 'danger-full-access unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'DISPATCH_CODEX_SANDBOX is invalid' "$TMP/err-danger"

printf 'original\n' > "$TMP/safe-target"
ln -s "$TMP/safe-target" "$TMP/out-link"
if PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex --kind passthrough \
  --task x --workdir "$TMP/work" --output "$TMP/out-link" >"$TMP/stdout-link" 2>"$TMP/err-link"; then
  printf 'symlink output unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'Too many levels of symbolic links' "$TMP/err-link"
check grep -qF 'original' "$TMP/safe-target"
shopt -s nullglob
leftovers=("$TMP"/.agent-dispatch-backend.*)
shopt -u nullglob
check test "${#leftovers[@]}" -eq 0

if ! PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex --kind passthrough \
  --task x --workdir /tmp --output "$TMP/out-root" >"$TMP/stdout-root" 2>"$TMP/err-root"; then
  printf 'single-component workdir unexpectedly failed\n' >&2
  cat "$TMP/err-root" >&2
  exit 1
fi

# Exercise the real CLI remote branch against a localhost HTTPS gateway.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
  >/dev/null 2>&1
cat > "$TMP/https-gateway.py" <<'PY'
import hashlib, http.server, json, ssl, sys
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        assert self.headers["Authorization"] == "Bearer cli-test-token"
        if request["worker_kind"] == "opencode" and self.path == "/v1/reviews":
            assert request["model"] == "opencode-go/qwen3.8-flash"
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if request["worker_kind"] == "opencode":
            assert self.path == "/v1/implementations"
            assert request["model"] in ("opencode-go/kimi-k2.7-code", "opencode-go/kimi-k3")
            assert request["schema"] == "implement-request.v2"
        else:
            assert request["worker_kind"] == "codex"
        if self.path == "/v1/implementations":
            if request["correlation_id"] == "cli-implement-boundary":
                assert len(request["task"].encode("utf-8")) == 16384
                assert request["timeout_seconds"] == 900
            files = sorted(request["files"], key=lambda item: item["path"])
            digest = hashlib.sha256(json.dumps(files, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            changes = [] if request["task"] == "no-op" else [{
                "path": files[0]["path"], "before_sha256": hashlib.sha256(files[0]["content"].encode()).hexdigest(),
                "after_text": "after"}]
            provider = request["model"]
            finish = "completed"
            if request["correlation_id"] == "cli-mismatch":
                provider = "opencode-go/kimi-k2.7-code" if request["model"] == "opencode-go/kimi-k3" else "opencode-go/kimi-k3"
            if request["correlation_id"] == "cli-incomplete":
                finish = "incomplete"
            body = json.dumps({"schema":"implement-artifact.v2", "repository":request["repository"],
                "worker_kind":request["worker_kind"], "correlation_id":request["correlation_id"],
                "snapshot_sha256":digest, "provider":provider, "model":request["model"],
                "execution_location":"cluster",
                "changes":changes, "model_output":"done", "finish_reason":finish,
                "fallback":None, "latency_ms":1}).encode()
        else:
            assert self.path == "/v1/reviews"
            assert request["schema"] == "review-request.v1"
            assert request["repository"] == "example-org/example"
            assert request["pull_request"] == 20
            assert request["base_sha"] == "a" * 40
            assert request["head_sha"] == "b" * 40
            assert request["correlation_id"] == "cli-corr"
            assert request["paths"] == ["src/app.py"]
            assert request["kind"] == "reviewer"
            assert request["model"] == "remote/model"
            assert "remote-task" in request["packet"] and "diff body" in request["packet"]
            body = json.dumps({
            "schema": "review-artifact.v1", "repository": request["repository"],
            "pull_request": request["pull_request"], "base_sha": request["base_sha"],
            "head_sha": request["head_sha"], "worker_kind": request["worker_kind"],
            "execution_location": "cluster", "provider": request["model"],
            "correlation_id": request["correlation_id"], "findings": [],
            "finish_reason": "completed", "fallback": None, "latency_ms": 1,
            }).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_): pass
server = http.server.HTTPServer(("localhost", 0), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(sys.argv[1], sys.argv[2])
server.socket = context.wrap_socket(server.socket, server_side=True)
with open(sys.argv[3], "w") as stream: stream.write(str(server.server_port))
server.serve_forever()
PY
python3 "$TMP/https-gateway.py" "$TMP/cert.pem" "$TMP/key.pem" "$TMP/port" &
SERVER_PID=$!
for _ in $(seq 1 50); do [[ -s "$TMP/port" ]] && break; sleep 0.02; done
[[ -s "$TMP/port" ]] || { printf 'HTTPS gateway did not start\n' >&2; exit 1; }
cat > "$TMP/remote-routes.json" <<'EOF'
{"schema":"dispatch-routes.v1","routes":{"remote/model":{"default_harness":"codex","harnesses":["codex"],"target":"remote","kinds":["review"],"fallback":{"policy":"none"}},"opencode-go/qwen3.8-flash":{"default_harness":"opencode","harnesses":["opencode"],"target":"remote","kinds":["review"],"fallback":{"policy":"remote-unavailable","harness":"opencode","target":"local"}}}}
EOF
remote_port="$(<"$TMP/port")"
if ! DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model remote/model --routes "$TMP/remote-routes.json" --kind review \
  --diff "$TMP/change.diff" --repository example-org/example --pull-request 20 --base-sha "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
  --head-sha "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" --correlation-id cli-corr --path src/app.py \
  --task remote-task --workdir "$TMP/work" --output "$TMP/out-remote" >"$TMP/stdout-remote" 2>"$TMP/err-remote"; then
  printf 'remote CLI dispatch unexpectedly failed\n' >&2; cat "$TMP/err-remote" >&2; exit 1
fi
check test -s "$TMP/out-remote"
check grep -qF '"execution_location":"cluster"' "$TMP/out-remote"
check grep -qF '"worker_kind":"codex"' "$TMP/out-remote"

if ! HOME="$TMP/caller-home" DISPATCH_OPENCODE_HOME="$TMP/opencode-home" \
  DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token \
  DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" PATH="$TMP/bin:/usr/bin:/bin" \
  "$CLI" --model opencode-go/qwen3.8-flash --routes "$TMP/remote-routes.json" --kind review \
  --diff "$TMP/change.diff" --repository example-org/example --pull-request 21 \
  --base-sha "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" --head-sha "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" \
  --correlation-id cli-qwen-fallback --path src/app.py --task qwen-local-fallback \
  --workdir "$TMP/work" --output "$TMP/out-opencode-fallback" \
  >"$TMP/stdout-opencode-fallback" 2>"$TMP/err-opencode-fallback"; then
  printf 'remote Qwen fallback dispatch unexpectedly failed\\n' >&2
  cat "$TMP/err-opencode-fallback" >&2
  exit 1
fi
check test -s "$TMP/out-opencode-fallback"
check grep -qF '"provider":"opencode-go/qwen3.8-flash"' "$TMP/out-opencode-fallback"
check grep -qF '"worker_kind":"opencode"' "$TMP/out-opencode-fallback"
check grep -qF '"execution_location":"requester"' "$TMP/out-opencode-fallback"
check grep -qF '"fallback":"cluster-unavailable-local-opencode"' "$TMP/out-opencode-fallback"

printf 'before' > "$TMP/work/selected.txt"
cat > "$TMP/implement-routes.json" <<'EOF'
{"schema":"dispatch-routes.v1","routes":{"remote/model":{"default_harness":"codex","target":"remote","kinds":["implement"],"fallback":{"policy":"none"}}}}
EOF
for scenario in change no-op; do
  status=0
  DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
    PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model remote/model --routes "$TMP/implement-routes.json" --kind implement \
    --repository example-org/example --correlation-id cli-implement --path selected.txt --task "$scenario" \
    --workdir "$TMP/work" --output "$TMP/out-implement-$scenario" >"$TMP/stdout-implement-$scenario" 2>"$TMP/err-implement-$scenario" || status=$?
  if [[ "$scenario" == change ]]; then check test "$status" -eq 0; else check test "$status" -eq 6; fi
  check test -s "$TMP/out-implement-$scenario"
  check grep -qF '"schema":"implement-artifact.v2"' "$TMP/out-implement-$scenario"
  check grep -qF '"model":"remote/model"' "$TMP/out-implement-$scenario"
  check grep -qF before "$TMP/work/selected.txt"
done
check grep -qF '"changes":[]' "$TMP/out-implement-no-op"

cat > "$TMP/kimi-implement-routes.json" <<'EOF'
{"schema":"dispatch-routes.v1","routes":{
  "opencode-go/kimi-k2.7-code":{"default_harness":"opencode","target":"remote","kinds":["implement"],"fallback":{"policy":"none"}},
  "opencode-go/kimi-k3":{"default_harness":"opencode","target":"remote","kinds":["implement"],"fallback":{"policy":"none"}},
  "opencode-go/Other_Model":{"default_harness":"opencode","target":"remote","kinds":["implement"],"fallback":{"policy":"none"}},
  "opencode-go/kimi-fallback":{"default_harness":"opencode","target":"remote","kinds":["implement"],"fallback":{"policy":"remote-unavailable","harness":"opencode","target":"local"}}}}
EOF
for kimi_model in opencode-go/kimi-k2.7-code opencode-go/kimi-k3; do
  safe="${kimi_model##*/}"
  if ! DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
    PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model "$kimi_model" --routes "$TMP/kimi-implement-routes.json" --kind implement \
    --repository example-org/example --correlation-id "cli-$safe" --path selected.txt --task change \
    --workdir "$TMP/work" --output "$TMP/out-$safe" >"$TMP/stdout-$safe" 2>"$TMP/err-$safe"; then
    printf 'kimi remote implement dispatch unexpectedly failed\n' >&2
    cat "$TMP/err-$safe" >&2
    exit 1
  fi
  check grep -qF '"schema":"implement-artifact.v2"' "$TMP/out-$safe"
  check grep -qF "\"model\":\"$kimi_model\"" "$TMP/out-$safe"
  check grep -qF '"worker_kind":"opencode"' "$TMP/out-$safe"
done
if DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model opencode-go/Other_Model --routes "$TMP/kimi-implement-routes.json" --kind implement \
  --repository example-org/example --correlation-id cli-bad-model --path selected.txt --task change \
  --workdir "$TMP/work" --output "$TMP/out-bad-model" >"$TMP/stdout-bad-model" 2>"$TMP/err-bad-model"; then
  printf 'unapproved OpenCode implement model unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'unsupported OpenCode implement model' "$TMP/err-bad-model"
if DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model opencode-go/kimi-fallback --routes "$TMP/kimi-implement-routes.json" --kind implement \
  --repository example-org/example --correlation-id cli-fallback --path selected.txt --task change \
  --workdir "$TMP/work" --output "$TMP/out-fallback" >"$TMP/stdout-fallback" 2>"$TMP/err-fallback"; then
  printf 'OpenCode implement fallback unexpectedly succeeded\n' >&2; exit 1
fi
check grep -qF 'fallback policy none' "$TMP/err-fallback"
if DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model opencode-go/kimi-k3 --routes "$TMP/kimi-implement-routes.json" --kind implement \
  --repository example-org/example --correlation-id cli-mismatch --path selected.txt --task mismatch \
  --workdir "$TMP/work" --output "$TMP/out-mismatch" >"$TMP/stdout-mismatch" 2>"$TMP/err-mismatch"; then
  printf 'wrong-model artifact unexpectedly accepted\n' >&2; exit 1
fi
check grep -qF 'malformed implementation artifact' "$TMP/err-mismatch"
if DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model opencode-go/kimi-k3 --routes "$TMP/kimi-implement-routes.json" --kind implement \
  --repository example-org/example --correlation-id cli-incomplete --path selected.txt --task incomplete \
  --workdir "$TMP/work" --output "$TMP/out-incomplete" >"$TMP/stdout-incomplete" 2>"$TMP/err-incomplete"; then
  printf 'incomplete artifact unexpectedly accepted\n' >&2; exit 1
fi
check grep -qF 'malformed implementation artifact' "$TMP/err-incomplete"

boundary_task="$(python3 -c 'print("x" * 16384, end="")')"
if ! DISPATCH_MAX_TASK_BYTES=120000 DISPATCH_TIMEOUT_SECONDS=900 \
  DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model remote/model --routes "$TMP/implement-routes.json" --kind implement \
  --repository example-org/example --correlation-id cli-implement-boundary --path selected.txt \
  --task "$boundary_task" --workdir "$TMP/work" --output "$TMP/out-implement-boundary" \
  >"$TMP/stdout-implement-boundary" 2>"$TMP/err-implement-boundary"; then
  printf 'valid maximum remote implementation limits were rejected\n' >&2
  cat "$TMP/err-implement-boundary" >&2
  exit 1
fi
check test -s "$TMP/out-implement-boundary"
check grep -qF '"schema":"implement-artifact.v2"' "$TMP/out-implement-boundary"

# A backend added by one script and one registration, in a copy of the tree,
# must run with no other change to the dispatch core.
SEAM="$TMP/seam-repo"
mkdir -p "$SEAM"
cp -R "$ROOT/dispatch" "$ROOT/contracts" "$SEAM/"
cat > "$SEAM/dispatch/adapters/example_backend.py" <<'EOF'
from dispatch.adapters.base import Adapter

def detect_limit(diagnostic):
    lines = diagnostic.strip().splitlines()
    return "quota" if lines and lines[-1] == "fatal: quota exhausted" else None


ADAPTER = Adapter(name="example-backend", roles=frozenset({"implement"}), env=("DISPATCH_EXAMPLE_HOME",),
                  detect_limit=detect_limit)
EOF
python3 - "$SEAM/dispatch/adapters/__init__.py" <<'EOF'
import pathlib
import sys

registry = pathlib.Path(sys.argv[1])
registry.write_text(registry.read_text(encoding="utf-8").replace(
    "\n_REGISTERED = (",
    "\nfrom dispatch.adapters import example_backend\n_REGISTERED = (example_backend, ",
), encoding="utf-8")
EOF
check grep -qF '_REGISTERED = (example_backend, ' "$SEAM/dispatch/adapters/__init__.py"
cat > "$SEAM/dispatch/cli/backends/example-backend.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$DISPATCH_TASK" in
  limit) exit 75 ;;
  broken) exit 7 ;;
  quota) printf 'fatal: quota exhausted\n' >&2; exit 9 ;;
  mentions-quota) printf 'fatal: quota exhausted\nfatal: disk full\n' >&2; exit 9 ;;
  stdout-quota) printf 'fatal: quota exhausted\n'; exit 9 ;;
  slow) printf 'transcript line\n' >&2; sleep 6 & printf '%s' "$!" > "$DISPATCH_WORKDIR/slow-child.pid"; wait ;;
  trap-term)
    marker="$DISPATCH_WORKDIR/trap-term.marker"; : > "$marker"
    trap 'rm -f -- "$marker"' EXIT; trap 'exit 143' TERM
    sleep 6 ;;
esac
{ printenv | grep -E '^DISPATCH_(EXAMPLE|OPENCODE|CODEX)_' | sort; printf 'ran\n'; } > "$DISPATCH_OUTPUT"
EOF
chmod +x "$SEAM/dispatch/cli/backends/example-backend.sh"
SEAM_CLI="$SEAM/dispatch/cli/varde-agent-dispatch"

# Run from the original tree: its dispatch package must not shadow the copy's registry.
if ! (cd "$ROOT" && DISPATCH_EXAMPLE_HOME=/example-home DISPATCH_OPENCODE_HOME=/opencode-home DISPATCH_CODEX_SANDBOX=read-only \
  "$SEAM_CLI" --harness example-backend --kind implement --task run --workdir "$TMP/work" \
  --output "$TMP/out-seam" >"$TMP/stdout-seam" 2>"$TMP/err-seam"); then
  printf 'registration-only backend unexpectedly failed\n' >&2
  cat "$TMP/err-seam" >&2
  exit 1
fi
check grep -qxF 'DISPATCH_EXAMPLE_HOME=/example-home' "$TMP/out-seam"
check grep -qxF 'ran' "$TMP/out-seam"
check_not() { if "$@"; then printf 'unexpected match: %s\n' "$*" >&2; exit 1; fi; pass=$((pass + 1)); }
check_not grep -q 'DISPATCH_OPENCODE_HOME\|DISPATCH_CODEX_SANDBOX' "$TMP/out-seam"

seam_status=0
"$SEAM_CLI" --harness example-backend --kind implement --task limit --workdir "$TMP/work" \
  --output "$TMP/out-seam-limit" >"$TMP/stdout-seam-limit" 2>"$TMP/err-seam-limit" || seam_status=$?
check test "$seam_status" -eq 75
check grep -qF 'limit reached' "$TMP/err-seam-limit"
check test ! -e "$TMP/out-seam-limit"

seam_status=0
"$SEAM_CLI" --harness example-backend --kind implement --task broken --workdir "$TMP/work" \
  --output "$TMP/out-seam-broken" >"$TMP/stdout-seam-broken" 2>"$TMP/err-seam-broken" || seam_status=$?
check test "$seam_status" -eq 7
check_not grep -qF 'limit reached' "$TMP/err-seam-broken"

# The adapter's own detector turns the CLI's fatal quota report into exit 75,
# and the caller still sees the backend's stderr.
seam_status=0
"$SEAM_CLI" --harness example-backend --kind implement --task quota --workdir "$TMP/work" \
  --output "$TMP/out-seam-quota" >"$TMP/stdout-seam-quota" 2>"$TMP/err-seam-quota" || seam_status=$?
check test "$seam_status" -eq 75
check grep -qF 'limit reached (quota)' "$TMP/err-seam-quota"
check grep -qxF 'fatal: quota exhausted' "$TMP/err-seam-quota"

seam_status=0
"$SEAM_CLI" --harness example-backend --kind implement --task mentions-quota --workdir "$TMP/work" \
  --output "$TMP/out-seam-mention" >"$TMP/stdout-seam-mention" 2>"$TMP/err-seam-mention" || seam_status=$?
check test "$seam_status" -eq 9
check_not grep -qF 'limit reached' "$TMP/err-seam-mention"

# Only stderr reaches the limit detector; the backend's stdout stays on the caller's stdout.
seam_status=0
"$SEAM_CLI" --harness example-backend --kind implement --task stdout-quota --workdir "$TMP/work" \
  --output "$TMP/out-seam-stdout" >"$TMP/stdout-seam-stdout" 2>"$TMP/err-seam-stdout" || seam_status=$?
check test "$seam_status" -eq 9
check grep -qxF 'fatal: quota exhausted' "$TMP/stdout-seam-stdout"
check_not grep -qF 'fatal: quota exhausted' "$TMP/err-seam-stdout"

# TERM ends the run at once, not after the backend finishes, and still removes
# the private stderr copy and the staged output.
mkdir -p "$TMP/sig"
TMPDIR="$TMP/sig" "$SEAM_CLI" --harness example-backend --kind implement --task slow --workdir "$TMP/work" \
  --output "$TMP/sig/out" >/dev/null 2>&1 &
sig_pid=$!
sleep 1
sig_sent=$SECONDS
kill -TERM "$sig_pid"
sig_status=0
wait "$sig_pid" || sig_status=$?
check test "$sig_status" -eq 143
check test $((SECONDS - sig_sent)) -le 2
sleep 0.3
check test -s "$TMP/work/slow-child.pid"
check_not kill -0 "$(cat "$TMP/work/slow-child.pid")" 2>/dev/null
check test -z "$(find "$TMP/sig" -name 'agent-dispatch-*' -o -name '.agent-dispatch-*')"

# A backend that traps TERM keeps running after the signal, and bash then reports
# its killed child on stderr. The stderr copier must outlive it, or that write
# raises SIGPIPE and the backend dies before its own cleanup trap runs.
TMPDIR="$TMP/sig" "$SEAM_CLI" --harness example-backend --kind implement --task trap-term --workdir "$TMP/work" \
  --output "$TMP/sig/out-trap" >/dev/null 2>&1 &
trap_pid=$!
sleep 1
check test -e "$TMP/work/trap-term.marker"
kill -TERM "$trap_pid"
wait "$trap_pid" || true
for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -e "$TMP/work/trap-term.marker" ]] || break; sleep 0.3; done
check_not test -e "$TMP/work/trap-term.marker"

# A registered backend serves only the roles it declares.
seam_status=0
printf 'diff\n' > "$TMP/seam.diff"
"$SEAM_CLI" --harness example-backend --kind review --diff "$TMP/seam.diff" --task run --workdir "$TMP/work" \
  --output "$TMP/out-seam-role" >"$TMP/stdout-seam-role" 2>"$TMP/err-seam-role" || seam_status=$?
check test "$seam_status" -eq 3
check grep -qF 'cannot serve kind review' "$TMP/err-seam-role"

printf 'dispatch CLI tests: %s passed\n' "$pass"
