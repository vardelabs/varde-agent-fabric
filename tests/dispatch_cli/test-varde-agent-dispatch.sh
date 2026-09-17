#!/usr/bin/env bash
set -euo pipefail
unset DISPATCH_MODEL DISPATCH_ROUTES_FILE DISPATCH_CODEX_SANDBOX DISPATCH_MAX_TASK_BYTES

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
CLI="$ROOT/dispatch/cli/varde-agent-dispatch"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/dispatch-test.XXXXXX")"
trap '[[ -z "${SERVER_PID:-}" ]] || kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$TMP"' EXIT

mkdir -p "$TMP/work"
printf 'diff body\n' > "$TMP/change.diff"
cat > "$TMP/routes.json" <<'EOF'
{
  "schema": "dispatch-routes.v1",
  "routes": {
    "test/model": {
      "default_harness": "codex",
      "harnesses": ["codex"],
      "target": "local",
      "kinds": ["review", "passthrough"],
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
      "target": "local",
      "kinds": ["passthrough"],
      "fallback": {"policy": "remote-unavailable", "harness": "codex", "target": "remote"}
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
printf 'model=%s\nsandbox=%s\ntarget=%s\noverridden=%s\nfallback=%s\nfallback_harness=%s\nfallback_target=%s\n' "${DISPATCH_MODEL:-}" "${DISPATCH_CODEX_SANDBOX:-}" "${DISPATCH_TARGET:-}" "${DISPATCH_HARNESS_OVERRIDDEN:-}" "${DISPATCH_FALLBACK_POLICY:-}" "${DISPATCH_FALLBACK_HARNESS:-}" "${DISPATCH_FALLBACK_TARGET:-}" > "$output"
printf '%s\n' "$prompt" >> "$output"
EOF
chmod +x "$TMP/bin/codex"

if ! DISPATCH_CODEX_SANDBOX=workspace-write DISPATCH_UNSAFE_SENTINEL=must-not-forward OPENAI_API_KEY=must-not-forward \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --kind review \
  --diff "$TMP/change.diff" --model test/model --routes "$TMP/routes.json" --task x --workdir "$TMP/work" \
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
check grep -qF 'overridden=false' "$TMP/out"
check grep -qF 'fallback=none' "$TMP/out"
check grep -qF 'BEGIN DIFF' "$TMP/out"
check grep -qF 'diff body' "$TMP/out"

if ! PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model fallback/model --routes "$TMP/routes.json" \
  --kind passthrough --task x --workdir "$TMP/work" --output "$TMP/out-fallback" \
  >"$TMP/stdout-fallback" 2>"$TMP/err-fallback"; then
  printf 'fallback route dispatch unexpectedly failed\n' >&2
  cat "$TMP/err-fallback" >&2
  exit 1
fi
check grep -qF 'fallback=remote-unavailable' "$TMP/out-fallback"
check grep -qF 'fallback_harness=codex' "$TMP/out-fallback"
check grep -qF 'fallback_target=remote' "$TMP/out-fallback"

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
import http.server, json, ssl, sys
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        assert self.path == "/v1/reviews"
        assert self.headers["Authorization"] == "Bearer cli-test-token"
        assert request["worker_kind"] == "codex"
        body = json.dumps({
            "schema": "review-artifact.v1", "repository": request["repository"],
            "pull_request": request["pull_request"], "base_sha": request["base_sha"],
            "head_sha": request["head_sha"], "worker_kind": request["worker_kind"],
            "execution_location": "cluster", "provider": "codex-test",
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
{"schema":"dispatch-routes.v1","routes":{"remote/model":{"default_harness":"codex","harnesses":["codex"],"target":"remote","kinds":["review"],"fallback":{"policy":"none"}}}}
EOF
remote_port="$(<"$TMP/port")"
if ! DISPATCH_GATEWAY_URL="https://localhost:$remote_port" DISPATCH_GATEWAY_TOKEN=cli-test-token DISPATCH_GATEWAY_CA_FILE="$TMP/cert.pem" \
  PATH="$TMP/bin:/usr/bin:/bin" "$CLI" --model remote/model --routes "$TMP/remote-routes.json" --kind review \
  --diff "$TMP/change.diff" --repository vardelabs/example --pull-request 20 --base-sha "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
  --head-sha "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" --correlation-id cli-corr --path src/app.py \
  --task remote-task --workdir "$TMP/work" --output "$TMP/out-remote" >"$TMP/stdout-remote" 2>"$TMP/err-remote"; then
  printf 'remote CLI dispatch unexpectedly failed\n' >&2; cat "$TMP/err-remote" >&2; exit 1
fi
check test -s "$TMP/out-remote"
check grep -qF '"execution_location":"cluster"' "$TMP/out-remote"
check grep -qF '"worker_kind":"codex"' "$TMP/out-remote"

printf 'dispatch CLI tests: %s passed\n' "$pass"
