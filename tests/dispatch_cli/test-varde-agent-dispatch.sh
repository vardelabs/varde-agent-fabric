#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
CLI="$ROOT/dispatch/cli/varde-agent-dispatch"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/dispatch-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/work"
printf 'diff\n' > "$TMP/change.diff"

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
while (($#)); do
  if [[ "$1" == '-o' ]]; then output="$2"; shift 2; else shift; fi
done
printf '{"status":"ok"}\n' > "$output"
EOF
chmod +x "$TMP/bin/codex"

if ! PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --harness codex --kind review --diff "$TMP/change.diff" --task x --workdir "$TMP/work" --output "$TMP/out" >"$TMP/stdout" 2>"$TMP/err"; then
  printf 'fake codex dispatch unexpectedly failed\n' >&2
  cat "$TMP/err" >&2
  exit 1
fi
check test -s "$TMP/out"
check grep -qF "$TMP/out" "$TMP/stdout"

printf 'dispatch CLI tests: %s passed\n' "$pass"
