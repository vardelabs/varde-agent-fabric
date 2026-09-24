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
