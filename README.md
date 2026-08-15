# varde-agent-fabric

> Safe, portable runtime components for bounded multi-agent work.

`varde-agent-fabric` is a public-ready foundation for dispatching bounded work,
exchanging versioned artifacts, and operating local inference safely. It provides
infrastructure, not an autonomous GitHub actor: named agents retain policy,
validation, and any outward action.

## Scope

- **Dispatch:** bounded task launch, isolation, and result capture.
- **Contracts:** versioned request and artifact schemas with strict validators.
- **Inference:** narrowly scoped local-runtime adapters and broker protocol.
- **Evaluation:** replay tools and safety-focused fixtures.

## Non-goals

- Hosting credentials, production model weights, customer code, or real review
  artifacts.
- Giving a model service repository-write, shell, or platform-posting authority.
- Treating a loopback HTTP endpoint as a cross-user authorization boundary.

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
