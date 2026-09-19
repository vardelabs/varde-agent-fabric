# Dispatch

Bounded-work launch interfaces and harness adapters belong here.

## Rules

- A dispatch receives explicit bounded work and returns a bounded artifact.
- Adapters do not post, approve, merge, push, or receive platform credentials.
- Keep harness-specific behavior behind a narrow adapter boundary.
- Put shared request and response shapes in `../contracts/`, not here.

## Route configuration

`route_registry.py` defines the portable model-first route configuration. The
provider-neutral shape is illustrated by `routes.example.json`; deployments
supply their approved values outside this repository.

Each route keeps these axes separate:

- `model`: the canonical model identity
- `default_harness`: the normal invocation adapter
- `harnesses`: compatible explicit overrides
- `target`: the execution location, either `local` or `remote`
- `kinds`: the bounded work contracts allowed for the route
- `fallback`: either `none`, or `remote-unavailable` for a remote primary route
  with the local `codex` fallback

The shipped local adapters are `codex` and `opencode`. `opencode` means the
OpenCode CLI harness; its provider and exact model remain runtime route policy,
so OpenCode Go, OpenCode Zen, and other providers are not interchangeable by
name. The adapter uses the dedicated OpenCode home for native authentication but receives no provider secret through the dispatcher.
The local adapter requires `DISPATCH_OPENCODE_HOME`, a dedicated OpenCode home
that does not contain unrelated caller credentials. This is credential-scope
reduction, not a filesystem sandbox; deployment policy must supply stronger
containment before using it for untrusted tasks.
The CLI rejects missing, nested, or non-directory dedicated homes before
starting the backend. OpenCode review requires a regular diff file; passthrough
does not accept a diff attachment and requires explicit
`DISPATCH_OPENCODE_ALLOW_WRITE=1`.

Resolution is model-first. An explicit harness is accepted only when it is
listed for that model. Missing models, unsupported kinds, malformed routes, and
ambiguous fallback policies fail closed. The resolved route records whether the
harness was overridden and preserves the fallback policy for provenance.

## Public boundary

This directory contains portable interfaces, artifact contracts, and transport
adapters. Deployment-owned policy values, host identity, network listeners,
credentials, private paths, tenant configuration, and platform delivery
integrations belong outside this repository. The portable adapters may validate
those values or exchange bounded artifacts, but they do not contain deployment
secrets or claim to enforce a host boundary by themselves.

Filesystem and process isolation are deployment responsibilities. A working
directory flag and a backend sandbox option are not substitutes for a host-level
boundary. Any deployment that needs stronger isolation must provide and verify that
boundary in a private integration layer without committing its policy or host
identifiers here. In particular, a service manager may accept a filesystem-denial
property without enforcing it, so deployments must verify enforcement with a
positive-control probe before relying on it.
