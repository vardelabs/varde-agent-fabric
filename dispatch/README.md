# Dispatch

Bounded-work launch interfaces and harness adapters belong here.

## Rules

- A dispatch receives explicit work and returns an artifact.
- Adapters do not post, approve, merge, push, or receive platform credentials.
- Keep harness-specific behavior behind a narrow adapter boundary.
- Put shared request and response shapes in `../contracts/`, not here.

## Public boundary

This directory contains portable interfaces and adapters only. Deployment-owned
policy, host identity, network listeners, credentials, private paths, tenant
configuration, and platform delivery integrations belong outside this repository.

A dispatch adapter must accept explicit bounded input, invoke only its declared
backend, and return a bounded artifact. It must not post, approve, merge, push,
open a shell service, or receive platform credentials. Shared request and response
shapes belong in `../contracts/`.

Filesystem and process isolation are deployment responsibilities. A working
directory flag and a backend sandbox option are not substitutes for a host-level
boundary. Any deployment that needs stronger isolation must provide and verify that
boundary in a private integration layer without committing its policy or host
identifiers here. In particular, a service manager may accept a filesystem-denial
property without enforcing it, so deployments must verify enforcement with a
positive-control probe before relying on it.
