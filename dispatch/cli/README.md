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

The default Codex model is `gpt-5.6-luna`. Set `DISPATCH_MODEL` or pass
`--model` to select another approved model. The default Codex sandbox is
`read-only`; set `DISPATCH_CODEX_SANDBOX` only to one of the documented Codex
sandbox modes.

## Public boundary

This directory is intentionally not a deployment service. It does not contain
network listeners, Tailscale configuration, private-root policy, broker socket
paths, GitHub delivery, chat integration, or credentials. Those belong in a
private deployment repository or in a separate reviewed protocol adapter.
