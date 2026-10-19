# varde-agent-fabric

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3](https://img.shields.io/badge/python-3-blue.svg)
![Bash 4.4+](https://img.shields.io/badge/bash-4.4%2B-blue.svg)
![Status: pre-1.0](https://img.shields.io/badge/status-pre--1.0-orange.svg)

Safe, portable runtime components for bounded multi-agent work.

`varde-agent-fabric` provides the execution layer between an orchestrator and a
bounded worker. It handles dispatch, versioned artifacts, narrow model-runtime
interfaces, and evaluation. It is not an autonomous GitHub actor or a general
purpose agent platform.

## Why this exists

Multi-agent systems become difficult to trust when routing, model execution,
permissions, and outward actions are mixed together. This project keeps those
concerns separate:

- callers submit bounded work
- the fabric resolves and executes an approved route
- workers return validated artifacts
- an outer orchestrator owns policy and outward actions

A worker does not receive repository-write, shell, GitHub, or Discord authority
merely because it can run a model or a harness.

## Requirements

- Python 3 and Bash 4.4 or newer. The runtime uses the standard library only.
- Git, for isolated local review in a worktree.
- A supported backend CLI on `PATH` for a live dispatch: `codex` or `opencode`.
  The test suite needs neither.

## Quickstart

```bash
git clone https://github.com/vardelabs/varde-agent-fabric.git
cd varde-agent-fabric
./tests/test-skeleton.sh
./tests/test-public-safety.sh
```

Both scripts run hermetic, synthetic checks and need no credentials or network.
For a first dispatch, copy `dispatch/routes.example.json`, point a route at a
backend you have installed, and follow the [Dispatch](#dispatch) example below.

## Architecture

```text
caller
  |
  v
bounded request
  |
  v
route resolution: model, harness, target, kind
  |
  +--> local worker
  |
  +--> private remote worker
  |
  v
validated artifact and provenance
  |
  v
orchestrator-owned policy and delivery
```

The public repository contains the portable runtime. A deployment can provide a
private overlay for approved routes, credentials, host policy, private prompts,
network listeners, and integration adapters. Those deployment values do not
belong in this repository.

## Components

### Dispatch

`dispatch/` contains bounded-work interfaces, lifecycle handling, harness
adapters, routing integration, and result capture.

The public CLI resolves model-first routes from the supplied registry. The
example registry uses the shipped `codex` backend:

```bash
DISPATCH_CODEX_SANDBOX=workspace-write \
dispatch/cli/varde-agent-dispatch \
  --model example/model \
  --routes dispatch/routes.example.json \
  --kind implement \
  --task "Run the bounded task" \
  --workdir /path/to/workdir
```

For an isolated local review, create or reuse a Git worktree and pass it with
`--workdir`. This isolates Git changes, not filesystem access. The CLI does not
create a worktree. `--workdir` must be an existing absolute directory, and a
`target: local` route does not call the gateway. See the [CLI guide](dispatch/cli/README.md#isolated-local-review)
for the canonical command example.

The CLI validates inputs, applies size limits, scrubs the backend environment,
and returns a bounded output artifact. A legacy `--harness`-only form remains
available for callers that do not use model routing. Remote review routes are
supported when a private deployment route and credentials are configured. They
require repository, pull request, base SHA, head SHA, correlation ID, and at
least one changed path. Remote and local targets remain distinct and do not
silently fall back to one another.

### Contracts

`contracts/` contains versioned, transport-neutral request and artifact schemas
and their validators. A result is not usable merely because it looks like JSON.
It must match the expected schema, request identity, and execution contract.

### Inference

`inference/` contains generic local-runtime adapters, broker protocols, output
grammars, and service templates. Inference services are compute boundaries, not
platform actors.

### Evaluation

`eval/` contains replay interfaces and synthetic fixtures for measuring contract
behavior, safety properties, and model or adapter changes without publishing
real source material or operational state.

## Repository layout

| Path | Purpose |
| --- | --- |
| `dispatch/` | Bounded work lifecycle and harness adapters |
| `contracts/` | Versioned request and artifact contracts |
| `inference/` | Broker protocols, grammars, clients, and service templates |
| `eval/` | Replay evaluation and synthetic fixtures |
| `docs/` | Design documents |
| `scripts/` | Repository maintenance and validation scripts |
| `tests/` | Cross-component safety and compatibility tests |

## Project status

Pre-1.0. The public boundary, the dispatch core, model-first route resolution,
the versioned contracts, and the public evaluation and release gates are in
place. Interfaces can still change between commits. Open work is tracked in
[issues](https://github.com/vardelabs/varde-agent-fabric/issues).

## Public-ready rule

Every reachable commit must be safe to publish.
Keep credentials, private deployment values, model weights, real source diffs,
private prompts, and operational logs outside this repository. The detailed
contributor checklist and portable versus deployment-owned boundary are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Contributing

Run the skeleton and relevant component tests before opening a pull request.
CI runs the full suite on every pull request as the `Skeleton validation` check.
Changes that affect contracts, routing, adapters, security boundaries, or
migration dependencies should update the relevant issue and include focused
regression coverage.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and the
[code of conduct](CODE_OF_CONDUCT.md).

## Security

Report a vulnerability privately. See [`SECURITY.md`](SECURITY.md).

## License

This project is licensed under the Apache License, Version 2.0. See
[`LICENSE`](LICENSE) for the complete text.
