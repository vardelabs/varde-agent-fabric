# Inference

Local inference adapters, a broker protocol, reviewed output grammars, and service templates belong here.

## Safety boundary

A broker is compute infrastructure. It accepts bounded structured requests, authenticates the local caller, schedules a fixed runtime, and returns a strictly validated artifact. It does not receive repository-write, shell, GitHub, or posting authority.

## Rules

- Do not expose an unauthenticated network listener as a tenant boundary.
- Keep real model paths, model weights, sockets, and logs out of Git. The operational ignore rules cover root-level state plus declared `dispatch/`, `inference/`, and `eval/` runtime directories for logs, weights, audits, and sockets. Private overlays are ignored for each declared component. Neutral documentation and synthetic fixtures under `eval/fixtures/` remain reviewable, including files in directories named `logs/`, `weights/`, or `runtime/`. Sensitive local-artifact extensions and common private-key names remain ignored wherever they appear.
- Put versioned output grammars in `grammars/`.
- Put generic service templates in `systemd/`. Deployment overlays stay private.
