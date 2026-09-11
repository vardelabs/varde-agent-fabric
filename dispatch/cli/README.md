# `varde-agent-dispatch`

A small, public-safe command for invoking a bounded local backend and capturing
one output artifact. The repository contains no credentials, deployment
configuration, private host paths, or provider secrets.

## Contract

```text
varde-agent-dispatch \
  --harness codex \
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

The caller owns authentication, deployment policy, remote transport, and any
model registry. The public adapter does not inherit the caller's environment,
credentials, provider configuration, or shell aliases.

`--kind passthrough` runs the task as supplied. `--kind review` additionally
requires `--diff` and exposes its canonical path through `DISPATCH_DIFF`.

The caller can pass `--model` or set `DISPATCH_MODEL`, and can set
`DISPATCH_CODEX_SANDBOX` to an approved Codex sandbox mode. These values are
forwarded explicitly to the backend. `HOME` is retained for normal CLI runtime
behavior, so this boundary does not claim to remove credentials stored by a
provider CLI. Provider authentication remains a deployment concern.

## Public boundary

This directory is intentionally not a deployment service. It does not contain
network listeners, Tailscale configuration, private-root policy, broker socket
paths, GitHub delivery, chat integration, or credentials. Those belong in a
private deployment repository or in a separate reviewed protocol adapter.
