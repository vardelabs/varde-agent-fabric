# Dispatch

Bounded-work launch interfaces and harness adapters belong here.

## Rules

- A dispatch receives explicit work and returns an artifact.
- Adapters do not post, approve, merge, push, or receive platform credentials.
- Keep harness-specific behavior behind a narrow adapter boundary.
- Put shared request and response shapes in `../contracts/`, not here.

## Read-scope policy

`private_overlay.json` supplies normalized absolute private roots. The loader reads the
overlay through a no-follow file descriptor, requires a regular file whose file and
ancestors are owned by root or the effective user and are not group/world writable,
and requires each root to be a non-world-writable directory. Leaf ownership is not
required, so a deployment can deny another tenant's directory. If the worker runs as
the same effective user as the dispatcher, the deployment must use root-owned policy
files or a separate unprivileged worker uid, otherwise that worker can rewrite the
policy for a later spawn. `systemd_inaccessible_paths()` repeats root validation at
the adapter boundary and returns deterministic `InaccessiblePaths` properties
while refusing to hide the worker's own existing, non-symlinked workdir.

This is an explicit denylist, not an allowlist. It prevents access to configured
private roots when the host systemd adapter enforces the returned properties. It
does not make `--workdir` or a Codex `-C` flag a filesystem boundary. A complete
allowlist requires a host mount namespace or equivalent kernel boundary. Private roots
and overlays are deployment values and must never be committed here or accepted from
task text.
This primitive intentionally supports directory deny targets only. File targets such
as credentials are outside this slice and require the deployment integration to
choose a broader directory or add a separate policy primitive.

No production dispatch implementation is included in the skeleton yet.
