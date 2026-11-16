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

## Goals

- Keep routing, model execution, permissions, and outward actions apart. Callers submit bounded work, the fabric runs an approved route, workers return validated artifacts, and an outer orchestrator owns policy and outward actions.
- A worker never gains repository-write, shell, GitHub, or Discord authority because it can run a model or a harness.
- Stay portable. Deployment values live in a private overlay, never here.

## Quickstart

Needs Python 3 and Bash 4.4 or newer (standard library only), Git, and for a live dispatch a supported backend CLI on `PATH` (see the [CLI guide](dispatch/cli/README.md) for the list). The tests need neither backend, credentials, nor network.

```bash
git clone https://github.com/vardelabs/varde-agent-fabric
cd varde-agent-fabric
./tests/test-skeleton.sh
./tests/test-public-safety.sh
```

For a first dispatch, copy `dispatch/routes.example.json`, point a route at an installed backend, and follow the [CLI guide](dispatch/cli/README.md).

## Layout

| Path | Purpose |
| --- | --- |
| `dispatch/` | Bounded work lifecycle, harness adapters, route resolution, and the `varde-agent-dispatch` CLI |
| `contracts/` | Versioned, transport-neutral request and artifact schemas and validators |
| `inference/` | Rules for broker protocols, grammars, and service templates (no code yet) |
| `eval/` | Replay interfaces and synthetic fixtures |
| `docs/` | Design documents |
| `scripts/` | Repository maintenance and validation scripts |
| `tests/` | Cross-component safety and compatibility tests |

Status: pre-1.0. Interfaces can still change between commits. Open work is in [issues](https://github.com/vardelabs/varde-agent-fabric/issues).

## Public-ready rule

Every reachable commit must be safe to publish.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the rules and the [code of conduct](CODE_OF_CONDUCT.md). Report a vulnerability privately through [`SECURITY.md`](SECURITY.md).

## License

Apache License, Version 2.0. See [`LICENSE`](LICENSE).
