# Contracts

Versioned, transport-neutral contracts for bounded work belong here.

## Rules

- Schemas and validators evolve together and are tested together.
- A contract must reject unknown or malformed data deliberately.
- A worker name is checked by shape only. Which worker may serve a role is the
  executing side's decision, made from its adapter registry and its own
  configuration, so a new worker needs no contract change.
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
`schema`, `repository`, `worker_kind` (a worker name), `correlation_id`, `task`,
`files` (objects with `path` and UTF-8 `content`), and `timeout_seconds`.
`ImplementRequest.snapshot_sha256` is derived locally as SHA-256 of compact,
UTF-8 JSON encoding of the files sorted by path (insertion order inside each
file object is `path`, `content`). It is not a wire request field.

`implement-artifact.v1` contains exactly `schema`, `repository`, `worker_kind`,
`correlation_id`, `snapshot_sha256`, `provider`, `execution_location`, `changes`
(objects with `path`, `before_sha256` as null or lowercase SHA-256, and
`after_text` as null or UTF-8 text), `model_output`, `finish_reason`
(`completed`), `fallback` (always null in v1; only a v2 artifact can
record a fallback, see `IMPLEMENT-V2.md`), and nonnegative `latency_ms`. Request and
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

`scheduled-job-definition.v1` is what a plugin ships for one job. It has an `id`, a `description`, `cron`, `timezone`, and `timeout_seconds`. It must set exactly one of `prompt` (a model job) or `command` (a shell job). Keys that only matter for model output (`model`, `post_mode`, `post_to`, `mcp_config`, `max_post_chars`, `expected_stdout_start`, `plain_bullets`) are refused on a command job. `requires` names each value a tenant must supply, and the definition uses each one as a `{{name}}` placeholder in `prompt`, a `command` argument, `post_to`, `working_dir`, `vault_sync`, or an `env` value. A placeholder with no `requires` entry, a `requires` entry no placeholder uses, and a placeholder in any other field are all refused. `overridable` lists the keys a tenant may change. A definition cannot set `enabled` or `one_shot`. A tenant turns a shipped job on or off with `enabled`, and `one_shot` exists only on a tenant's own inline jobs.

`scheduled-job-enablement.v1` is one file per tenant. Each entry in `jobs` either uses a shipped job (`use: "<plugin>/<job-id>"`, with `values`, optional `overrides`, and `enabled`) or declares a tenant-only job under `inline`, written in the definition's job keys without placeholders.

`resolve_job` merges a definition and a tenant entry into the flat job a runner executes. It refuses a missing or unknown value and an override the definition does not offer. `command` is either shell text with no placeholders, or a list of arguments. In a list, a placeholder must be a whole argument, an argument that holds a placeholder with other text is refused, and the arguments total at most 4096 characters. The contract does not parse shell grammar, so shell text can never hold a placeholder. `resolve_job` and `inline_job` join a list with `shlex.join`, so each filled value reaches the program as exactly one argument. What that program then does with the argument (for example `sh -c {{script}}`) is the definition's responsibility. The jobs that `resolve_job` and `inline_job` return are checked again against every field bound, with the joined `command` capped at 64 KiB, and `resolve_job` takes the plugin name, so an entry binds only to the plugin it names. `timezone` must be a named IANA zone (`localtime`, `posixrules`, and `Factory` are refused, because they depend on the host). Tenant values and `env` values cannot hold control characters, and each `cron` field may hold only digits, letters, `*`, `,`, `/`, and `-`.

The contracts carry no host paths or credentials. A tenant's values, which may name its own paths and credential files, live only in that tenant's enablement file. The runner, not the contract, owns posting, repository writes, and run state.
