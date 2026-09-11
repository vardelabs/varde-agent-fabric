# varde-agent-fabric

> Safe, portable runtime components for bounded multi-agent work.

`varde-agent-fabric` is a public-ready foundation for dispatching bounded work,
exchanging versioned artifacts, and operating local inference safely. It provides
infrastructure, not an autonomous GitHub actor: named agents retain policy,
validation, and any outward action.

## Scope

- **Dispatch:** bounded task launch, isolation, routing, timeout, and result capture.
- **Contracts:** versioned request, artifact, provenance, and unavailable-result schemas with strict validators.
- **Routing:** model-first resolution of model, harness, target, kind, and explicit fallback policy.
- **Inference:** narrowly scoped local-runtime adapters, broker protocols, and generic remote-worker interfaces.
- **Evaluation:** replay tools, synthetic fixtures, contract checks, and safety-focused measurements.

The public fabric is the portable runtime core. A deployment may provide a private
configuration overlay for approved models, host policy, credentials, network
listeners, and integration adapters. Those values are not defaults in this
repository and are never required for the public tests.

A typical flow is:

```text
caller -> bounded dispatch request -> route resolution -> local or remote worker
       <- validated artifact and provenance <-------------------------------
```

The worker returns an artifact. An outer, deployment-owned orchestrator remains
responsible for policy decisions, exact-head rechecks, queue ownership, and any
outward action.

## Non-goals

- Hosting credentials, production model weights, private deployment values, real source diffs, or real review artifacts.
- Giving a model service repository-write, shell, or platform-posting authority.
- Treating a loopback HTTP endpoint or private network reachability as complete authorization.
- Storing Varde-specific routing, host paths, prompts, or delivery integrations in this repository.

## Repository layout

| Path | Purpose |
| --- | --- |
| `.github/` | CI workflow definitions and their index. |
| `dispatch/` | Bounded-work dispatch interfaces and adapters. |
| `contracts/` | Versioned, transport-neutral artifact contracts. |
| `inference/` | Local inference broker, client, grammars, and service templates. |
| `eval/` | Replay evaluation interfaces and synthetic fixtures. |
| `docs/` | Architecture, threat model, and operational design. |
| `scripts/` | Documented repository-maintenance scripts. |
| `tests/` | Cross-component safety and compatibility tests. |

Each root has its own README with filing and use rules.

## Public-ready rule

Every reachable commit must be safe to publish.
Do not add credentials, private hostnames, tenant paths, production prompts, real
source diffs, model weights, or operational logs. Keep deployment-specific values in a private overlay or
local configuration outside this repository.

## Status

The repository is a skeleton. It establishes the public boundary and intended
module ownership. Runtime code and shared-contract migrations will arrive as
small, independently reviewed changes.

## License

No license has been selected yet. Until one is added, all rights are reserved.
