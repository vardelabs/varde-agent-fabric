# Contributing

Treat this repository as public.
Every reachable commit must be safe to publish.
A mistake stays in history even after a later commit removes it.

The fabric is the portable runtime layer, not a deployment repository. Keep host-specific policy, credentials, private prompts, model weights, and platform integrations in a private overlay. The public code may define configuration schemas and extension points, but must not contain deployment values. When a change needs a concrete value, add a setting with a neutral default and let the overlay set it.

## Public Safety

- **Nothing private.** No secrets, private hostnames, tenant paths, deployment values, model weights, real source diffs (a synthetic diff in a fixture is fine), or operational logs.
- **No customer names or data,** anywhere: code, comments, docs, tests, fixtures, branch names, commit messages, or issue and PR text. Write "a firm" or "the example firm", never an invented firm name, which can collide with a real one.
- **No internal names in content.** Do not name internal people, agents, hosts, or private repos in code, comments, docs, tests, or fixtures. Use a generic role ("the orchestrator", "a worker", "the reviewer"). Agent names are public, since agents sign commits, but code stays deployment-neutral. A test that needs an agent or tenant name uses an `example-` name, such as `/data/example-agent`; the prefix marks the name as fake, and a reviewer still checks that a fixture holds no real name. A tenant here is an agent's account on a host, never a customer.
- **Comments state the constraint or the reason,** never a reviewer, a review round, or a date.
- **If private material lands, stop and tell the operator.** This covers a secret or a customer identifier in a commit, a branch, or a PR, and an internal name in content. The standard attribution in commits and PR footers does not count. Rotate any exposed secret, because deleting it does not make it private again. A follow-up commit does not fix it, because history keeps it. Rewriting history is the operator's decision.

`scripts/check-public-safety.sh` catches private keys, AWS access key IDs, tenant paths, runtime paths, and Discord webhook URLs. It catches names only when `PUBLIC_SAFETY_DENYLIST` points at a deployment-owned list, one name a line, outside the repo; without it, the name rules depend on review.

## Before Opening a Pull Request

1. Run `./tests/test-skeleton.sh`, `./tests/test-public-safety.sh`, and the relevant component tests. CI runs the full suite on every pull request as the `Skeleton validation` check.
2. Confirm the diff follows Public Safety above.
3. Keep deployment-specific configuration in a private overlay or local environment.
4. Add a regression test for any security, contract, routing, or lifecycle defect.
5. If you add a component root, document its runtime-state boundary and add matching ignore and regression coverage.
6. Update the relevant issue when changing a contract, route, adapter, deployment boundary, or migration dependency.

## Component Boundaries

- `dispatch/` owns bounded work lifecycle, route selection interfaces, harness adapters, and result capture.
- `contracts/` owns versioned transport-neutral request and artifact schemas plus validators.
- `inference/` owns generic broker protocols, grammars, clients, and service templates.
- `eval/` owns public-safe replay interfaces, synthetic fixtures, and measurements.
- Private overlays own deployment values, credentials, host enforcement, private prompts, and outward integrations.

Do not grant dispatch adapters or inference services platform-posting, repository-write, or arbitrary shell authority.
A named orchestrator remains responsible for policy validation, exact-head checks, queue ownership, and outward actions.

## Configuration and Provenance

A result is usable only when it matches the expected schema, request identity, and execution contract, not because it parses as JSON. New routes must keep `model`, `harness`, `target`, and `kind` separate, validate compatible combinations, and record the effective route and fallback decision in provenance. A remote-unavailable fallback must be explicit and must not apply to authorization, malformed-result, stale-head, or contract failures.

## Issue and Change Scope

Keep public runtime changes separate from private deployment rollout work. A portable change names its public contract and tests. A deployment change names the private overlay, host class, credentials boundary, and end-to-end probe without adding those values here. Do not close a migration issue until active callers and the installed runtime have been verified.
