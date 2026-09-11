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

The public CLI currently provides a narrow local dispatch core:

```bash
dispatch/cli/varde-agent-dispatch \
  --harness codex \
  --kind passthrough \
  --task "Run the bounded task" \
  --workdir /path/to/workdir
```

The CLI validates inputs, applies size limits, scrubs the backend environment,
and returns a bounded output artifact. Its current interface is intentionally
small. Model registry resolution and remote service integration are tracked in
the project roadmap.

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
Changes that affect contracts, routing, adapters, security boundaries, or
migration dependencies should update the relevant issue and include focused
regression coverage.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

No license has been selected yet. Until one is added, all rights are reserved.
