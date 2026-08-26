# Dispatch

Bounded-work launch interfaces and harness adapters belong here.

## Rules

- A dispatch receives explicit work and returns an artifact.
- Adapters do not post, approve, merge, push, or receive platform credentials.
- Keep harness-specific behavior behind a narrow adapter boundary.
- Put shared request and response shapes in `../contracts/`, not here.

## Read-scope policy

`read_scope.py` is the portable policy primitive for deployment adapters. A
private overlay supplies normalized absolute private roots. The loader reads the
overlay through a no-follow file descriptor, requires a private file and parent
owned by the effective user, and requires each root to be an owned, non-world-
writable directory. `systemd_inaccessible_paths()` repeats root validation at
the adapter boundary and returns deterministic `InaccessiblePaths` properties
while refusing to hide the worker's own workdir.

This is an explicit denylist, not an allowlist. It prevents access to configured
private roots when the host systemd adapter enforces the returned properties. It
does not make `--workdir` or a Codex `-C` flag a filesystem boundary. A complete
allowlist requires a host mount namespace or equivalent kernel boundary. Private
roots and overlays are deployment values and must never be committed here or
accepted from task text.

No production dispatch implementation is included in the skeleton yet.
