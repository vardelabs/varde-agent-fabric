# Contracts

Versioned, transport-neutral contracts for bounded work belong here.

## Rules

- Schemas and validators evolve together and are tested together.
- A contract must reject unknown or malformed data deliberately.
- Do not add filesystem paths, credentials, or references to caller-private data.
- Keep language-specific clients outside this directory unless they are validators.

## Review contracts

`review_contracts.py` defines the transport-neutral `review-request.v1` and
`review-artifact.v1` shapes. Both reject unknown keys, enforce bounded fields,
and require the request identity (`repository`, pull request, base SHA, head SHA,
and correlation ID) to be echoed by the artifact. The contract contains no
credentials, filesystem paths, prompts, or GitHub write authority.

The gateway client and worker adapter validate these contracts at their
boundaries. A transport response is unusable unless it matches the request's
head, base, correlation ID, and worker kind.

## Implementation contracts

`implement_contracts.py` defines `implement-request.v1` with exactly
`schema`, `repository`, `worker_kind` (`codex`), `correlation_id`, `task`,
`files` (objects with `path` and UTF-8 `content`), and `timeout_seconds`.
`ImplementRequest.snapshot_sha256` is derived locally as SHA-256 of compact,
UTF-8 JSON encoding of the files sorted by path (insertion order inside each
file object is `path`, `content`). It is not a wire request field.

`implement-artifact.v1` contains exactly `schema`, `repository`, `worker_kind`,
`correlation_id`, `snapshot_sha256`, `provider`, `execution_location`, `changes`
(objects with `path`, `before_sha256` as null or lowercase SHA-256, and
`after_text` as null or UTF-8 text), `model_output`, `finish_reason`
(`completed`), `fallback` (null), and nonnegative `latency_ms`. Request and
artifact JSON are each capped at 320 KiB. The gateway client checks all four
identity fields including the locally computed snapshot digest, and binds every
change to the snapshot: a modified or deleted path must carry the matching
preimage hash, while a created path carries a null preimage. Changes never
apply, commit, or push automatically. The public runner returns the artifact
only.

An empty `changes` list is a completed worker attempt, not evidence that files
were modified: the implementation CLI publishes its validated artifact and exits
6 for that case (0 means at least one proposed change). A null-preimage create
is checked against existing unselected paths at collection time, but this is
not an atomic apply guarantee. A future applier must create with exclusive,
no-follow filesystem operations and revalidate the destination at apply time.
Portable paths reject case variants of `.git`, control characters, and Unicode
normalization aliases within one file set.

## Scheduled-job contracts

`scheduled_job_contracts.py` defines two shapes for jobs that a scheduler runs on a cron.

`scheduled-job-definition.v1` is what a plugin ships for one job. It has an `id`, a `description`, `cron`, `timezone`, and `timeout_seconds`. It must set exactly one of `prompt` (a model job) or `command` (a shell job). Keys that only matter for model output (`model`, `post_mode`, `post_to`, `mcp_config`, `max_post_chars`, `expected_stdout_start`, `plain_bullets`) are refused on a command job. `requires` names each value a tenant must supply, and the definition uses each one as a `{{name}}` placeholder in `prompt`, `command`, `post_to`, `working_dir`, `vault_sync`, or an `env` value. A placeholder with no `requires` entry, or a `requires` entry no placeholder uses, is refused. `overridable` lists the keys a tenant may change. A definition cannot set `enabled` or `one_shot`, because those are the tenant's choice.

`scheduled-job-enablement.v1` is one file per tenant. Each entry in `jobs` either uses a shipped job (`use: "<plugin>/<job-id>"`, with `values`, optional `overrides`, and `enabled`) or declares a tenant-only job under `inline`, written in the definition's job keys without placeholders.

`resolve_job` merges a definition and a tenant entry into the flat job a runner executes. It refuses a missing or unknown value and an override the definition does not offer. A value placed into `command` is shell-quoted, so a tenant value can never add shell syntax to a shipped command. Write a `command` placeholder bare, never inside quotes, or the quotes become part of the argument.

The contracts carry no host paths or credentials. A tenant's values, which may name its own paths and credential files, live only in that tenant's enablement file. The runner, not the contract, owns posting, repository writes, and run state.
