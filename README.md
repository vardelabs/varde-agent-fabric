# varde-agent-fabric

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
| `docs/` | Architecture, threat model, and operational design |
| `scripts/` | Repository maintenance and validation scripts |
| `tests/` | Cross-component safety and compatibility tests |

## Project status

The repository is under active construction. The public boundary and initial
dispatch core are in place. The next roadmap slices are:

1. Generalize portable dispatch contracts and configuration, [#10](https://github.com/vardelabs/varde-agent-fabric/issues/10)
2. Resolve model-first routes, [#6](https://github.com/vardelabs/varde-agent-fabric/issues/6)
3. Define the private deployment overlay, [#11](https://github.com/vardelabs/varde-agent-fabric/issues/11)
4. Add the authenticated cluster service, [#7](https://github.com/vardelabs/varde-agent-fabric/issues/7)
5. Integrate remote-first Codex dispatch, [#5](https://github.com/vardelabs/varde-agent-fabric/issues/5)
6. Add public evaluation and release gates, [#12](https://github.com/vardelabs/varde-agent-fabric/issues/12)
7. Complete vault caller migration and duplicate cleanup, [#8](https://github.com/vardelabs/varde-agent-fabric/issues/8)

## Public-ready rule

Every reachable commit must be safe to publish.
Keep credentials, private deployment values, model weights, real source diffs,
private prompts, and operational logs outside this repository. The detailed
contributor checklist and portable versus deployment-owned boundary are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Contributing

Run the skeleton and relevant component tests before opening a pull request.
Cluster CI runs the full suite as the `Skeleton validation` check. The job
lives in `vardelabs/toolkits`; see `ci/README.md` there.
Changes that affect contracts, routing, adapters, security boundaries, or
migration dependencies should update the relevant issue and include focused
regression coverage.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

This project is licensed under the Apache License, Version 2.0. See
[`LICENSE`](LICENSE) for the complete text.
