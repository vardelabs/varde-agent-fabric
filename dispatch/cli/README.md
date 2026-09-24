# `varde-agent-dispatch`

A small, public-safe command for invoking a bounded local backend and capturing
one output artifact. The repository contains no credentials, deployment
configuration, private host paths, or provider secrets. The CLI requires Bash
4.4 or newer.

## Contract

```text
DISPATCH_CODEX_SANDBOX=workspace-write \
varde-agent-dispatch \
  --model example/model \
  --routes /path/to/routes.json \
  --kind implement \
  --task "bounded task" \
  --workdir /path/to/worktree
```
The current public backends are `codex` and `opencode`. The dispatcher passes only an explicit
runtime contract to a backend:

- `DISPATCH_TASK`
- `DISPATCH_WORKDIR`
- `DISPATCH_OUTPUT`
- `DISPATCH_KIND`
- optional `DISPATCH_DIFF`
- optional `DISPATCH_MODEL`
- optional `DISPATCH_TARGET`
- routed `DISPATCH_HARNESS_OVERRIDDEN`
- routed `DISPATCH_FALLBACK_POLICY`
- routed `DISPATCH_FALLBACK_HARNESS`
- routed `DISPATCH_FALLBACK_TARGET`

The caller owns authentication, deployment policy, and remote transport. When
`--model` is supplied, `--routes` provides the portable model-first registry;
`--harness` is optional and must be approved for that model. The public adapter
does not inherit the caller's environment,
credentials, provider configuration, or shell aliases.

`--kind implement` is the canonical write-capable implementation task.
`passthrough` remains a legacy compatibility spelling. `--kind review` additionally
requires `--diff` and exposes its canonical path through `DISPATCH_DIFF`.
Implementation jobs reject a supplied diff rather than silently changing task
semantics. Codex preserves the
ungated `passthrough` behavior, inheriting the caller's sandbox (read-only by
default), while canonical `implement` requires
`DISPATCH_CODEX_SANDBOX=workspace-write`.
The OpenCode adapter maps `review` to its `plan` agent and
`implement` and `passthrough` to its `build` agent. OpenCode implementation jobs require
`DISPATCH_OPENCODE_ALLOW_WRITE=1`; the default is fail-closed. It requires an explicit model and reads
provider authentication from OpenCode's native user configuration. The
dispatcher does not pass OpenCode provider keys or add OpenCode as a Codex
fallback. Review diffs are attached with OpenCode's native `--file` option.
The adapter invokes `--pure`, `--format json`, `--agent`, `--model`, `--dir`,
and `--file`; the installed CLI help surface is smoke tested when available.
The adapter output is OpenCode's raw JSONL event stream from `--format json`;
callers that need the final response must select the final `type=text` event.
Set `DISPATCH_OPENCODE_HOME` to a dedicated home directory containing only
the OpenCode configuration and authentication needed for this worker. The
adapter refuses to use the caller's general home directory, because an
OpenCode agent with file tools could otherwise read unrelated user credentials.
This dedicated home reduces credential exposure but is not a full filesystem
sandbox; the provider auth in it remains readable by the OpenCode process and
its tools. Use this local harness only for trusted bounded tasks until a
stronger worker sandbox is supplied by deployment policy.

The task and review diff are each bounded, and their combined prompt must fit
`DISPATCH_MAX_TASK_BYTES` (default 120000 bytes). The dispatcher rejects an
oversized input before the backend reads it. The default Codex sandbox is
`read-only`; the only alternate public mode is `workspace-write`.

`--model` may also be supplied through `DISPATCH_MODEL`. Whenever an explicit
`--model` is set, the registry must be supplied through `--routes` or
`DISPATCH_ROUTES_FILE`; the dispatcher refuses to guess a route. An explicit
`--harness` override is allowed only when the registry approves it. For
compatibility, callers using `--harness` with only the legacy `DISPATCH_MODEL`
forward that model without registry resolution. `--routes` without a model is
rejected. The caller can set `DISPATCH_CODEX_SANDBOX` to one of the public
modes. These values are forwarded explicitly to the backend. `HOME` is retained
for normal CLI runtime behavior, so this boundary does not claim to remove
credentials stored by a provider CLI. Provider authentication remains a
deployment concern.

## Remote implementation

A route with `target: remote`, `kinds: ["implement"]`, default harness `codex`,
and fallback policy `none` sends only explicitly named relative files:

```text
varde-agent-dispatch --model example/model --routes /path/to/remote-routes.json \
  --kind implement --task "bounded task" --workdir /path/to/worktree \
  --repository org/repo --correlation-id request-id --path src/file.py \
  --output /path/to/artifact.json
```

The route requires `DISPATCH_GATEWAY_URL` (HTTPS) and `DISPATCH_GATEWAY_TOKEN` in
the caller environment. It rejects review PR, SHA, and diff arguments, unsafe
or duplicate paths, symlinks anywhere in a file path, and excessive input.
The response is a validated `implement-artifact.v1` diff proposal, not an
applied change. Authentication failures and unavailability never trigger a
local implementation fallback. Remote reviews retain their existing route.

## Public boundary

This directory is intentionally not a deployment service. It does not contain
network listeners, Tailscale configuration, private-root policy, broker socket
paths, GitHub delivery, chat integration, or credentials. Those belong in a
private deployment repository or in a separate reviewed protocol adapter.
