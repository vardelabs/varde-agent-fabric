# Inference

Local inference adapters, a broker protocol, reviewed output grammars, and service templates belong here.

## Safety boundary

A broker is compute infrastructure. It accepts bounded structured requests, authenticates the local caller, schedules a fixed runtime, and returns a strictly validated artifact. It does not receive repository-write, shell, GitHub, or posting authority.

## Rules

- Do not expose an unauthenticated network listener as a tenant boundary.
- Keep real model paths, model weights, sockets, and logs out of Git. `logs/` and `weights/` directories are ignored at any depth. The operational ignore rules cover root-level state, component state under `dispatch/` and `inference/`, and the declared `eval/private-overlay` boundary; documentation and synthetic fixtures outside those paths remain reviewable.
- Put versioned output grammars in `grammars/`.
- Put generic service templates in `systemd/`. Deployment overlays stay private.
