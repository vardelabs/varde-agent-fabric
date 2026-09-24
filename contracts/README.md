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
identity fields including the locally computed snapshot digest and restricts
changed paths to the supplied snapshot. The public runner returns the artifact
only. It does not apply, commit, or push the proposed changes.
