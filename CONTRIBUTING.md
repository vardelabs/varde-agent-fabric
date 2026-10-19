# Contributing

Treat this repository as public.
Every reachable commit must be safe to publish.

The fabric is the portable runtime layer, not a deployment repository. Keep
host-specific policy, credentials, private prompts, model weights, and platform
integrations in a private overlay. The public code may define configuration
schemas and extension points, but must not contain deployment values.

## Before opening a pull request

1. Run `./tests/test-skeleton.sh`, `./tests/test-public-safety.sh`, and the relevant component tests.
2. Confirm the diff contains no secrets, private hostnames, tenant paths, real source diffs, private deployment values, model weights, or operational logs.
3. Keep deployment-specific configuration in a private overlay or local environment.
4. Add a regression test for any security, contract, routing, or lifecycle defect.
5. If you add a component root, document its runtime-state boundary and add matching ignore and regression coverage.
6. Update the relevant issue when changing a contract, route, adapter, deployment boundary, or migration dependency.

## Component boundaries

- `dispatch/` owns bounded work lifecycle, route selection interfaces, harness adapters, and result capture.
- `contracts/` owns versioned transport-neutral request and artifact schemas plus validators.
- `inference/` owns generic broker protocols, grammars, clients, and service templates.
- `eval/` owns public-safe replay interfaces, synthetic fixtures, and measurements.
- Private overlays own deployment values, credentials, host enforcement, private prompts, and outward integrations.

## Boundaries

Do not grant dispatch adapters or inference services platform-posting, repository-write, or arbitrary shell authority.
A named orchestrator remains responsible for policy validation, exact-head checks, queue ownership, and outward actions.

## Configuration and provenance

Prefer explicit configuration over hardcoded deployment assumptions. New routes
must keep `model`, `harness`, `target`, and `kind` separate, validate compatible
combinations, and record the effective route and fallback decision in provenance.
A remote-unavailable fallback must be explicit and must not apply to authorization,
malformed-result, stale-head, or contract failures.

## Issue and change scope

Keep public runtime changes separate from private deployment rollout work. A
portable change should identify its public contract and tests. A deployment change
should identify the private overlay, host class, credentials boundary, and
end-to-end probe without adding those values here. Do not close a migration issue
until active callers and the installed runtime have been verified.
