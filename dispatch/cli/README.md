# `varde-agent-dispatch`

A small, public-safe command for invoking a bounded local backend and capturing
one output artifact. The repository contains no credentials, deployment
configuration, private host paths, or provider secrets.

## Contract

```text
varde-agent-dispatch \
  --model example/model \
  --routes /path/to/routes.json \
  --kind passthrough \
  --task "bounded task" \
  --workdir /path/to/worktree
```

The current public backend is `codex`. The dispatcher passes only an explicit
runtime contract to a backend:

- `DISPATCH_TASK`
- `DISPATCH_WORKDIR`
- `DISPATCH_OUTPUT`
- optional `DISPATCH_DIFF`
- optional `DISPATCH_MODEL`
- optional `DISPATCH_TARGET`
- routed `DISPATCH_HARNESS_OVERRIDDEN`
- routed `DISPATCH_FALLBACK_POLICY`

The caller owns authentication, deployment policy, and remote transport. When
`--model` is supplied, `--routes` provides the portable model-first registry;
`--harness` is optional and must be approved for that model. The public adapter
does not inherit the caller's environment,
credentials, provider configuration, or shell aliases.

`--kind passthrough` runs the task as supplied. `--kind review` additionally
requires `--diff` and exposes its canonical path through `DISPATCH_DIFF`.

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

## Public boundary

This directory is intentionally not a deployment service. It does not contain
network listeners, Tailscale configuration, private-root policy, broker socket
paths, GitHub delivery, chat integration, or credentials. Those belong in a
private deployment repository or in a separate reviewed protocol adapter.
