#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
CLI="$ROOT/dispatch/cli/varde-agent-dispatch"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/dispatch-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/work"
printf 'diff body\n' > "$TMP/change.diff"

pass=0
check() { "$@"; pass=$((pass + 1)); }

check bash -n "$CLI" "$ROOT/dispatch/cli/backends/codex.sh"
check test -x "$CLI"
check test -x "$ROOT/dispatch/cli/backends/codex.sh"

if "$CLI" --harness missing --kind passthrough --task x --workdir "$TMP/work" 2>"$TMP/err"; then
  printf 'unsupported harness unexpectedly succeeded\n' >&2; exit 1
fi
check grep -q 'unsupported harness' "$TMP/err"

if "$CLI" --harness codex --kind review --task x --workdir "$TMP/work" 2>"$TMP/err"; then
  printf 'review without diff unexpectedly succeeded\n' >&2; exit 1
fi
check grep -q -- '--diff' "$TMP/err"

mkdir -p "$TMP/bin"
cat > "$TMP/bin/codex" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
output=''
prompt=''
while (($#)); do
  if [[ "$1" == '-o' ]]; then output="$2"; shift 2
  elif [[ "$1" == '--' ]]; then shift; prompt="${1:-}"; break
  else shift
  fi
done
[[ -z "${DISPATCH_UNSAFE_SENTINEL:-}${OPENAI_API_KEY:-}" ]] || exit 90
printf 'model=%s\nsandbox=%s\n' "${DISPATCH_MODEL:-}" "${DISPATCH_CODEX_SANDBOX:-}" > "$output"
printf '%s\n' "$prompt" >> "$output"
EOF
chmod +x "$TMP/bin/codex"

if ! DISPATCH_CODEX_SANDBOX=workspace-write DISPATCH_UNSAFE_SENTINEL=must-not-forward OPENAI_API_KEY=must-not-forward \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex --kind review \
  --diff "$TMP/change.diff" --model test/model --task x --workdir "$TMP/work" \
  --output "$TMP/out" >"$TMP/stdout" 2>"$TMP/err"; then
  printf 'fake codex dispatch unexpectedly failed\n' >&2
  cat "$TMP/err" >&2
  exit 1
fi
check test -s "$TMP/out"
check grep -qF "$TMP/out" "$TMP/stdout"
check grep -qF 'model=test/model' "$TMP/out"
check grep -qF 'sandbox=workspace-write' "$TMP/out"
check grep -qF 'BEGIN DIFF' "$TMP/out"
check grep -qF 'diff body' "$TMP/out"

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

mkdir -p "$TMP/single"
if ! PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex --kind passthrough \
  --task x --workdir "$TMP/single" --output "$TMP/out-root" >"$TMP/stdout-root" 2>"$TMP/err-root"; then
  printf 'single-component workdir unexpectedly failed\n' >&2
  cat "$TMP/err-root" >&2
  exit 1
fi

printf 'dispatch CLI tests: %s passed\n' "$pass"
