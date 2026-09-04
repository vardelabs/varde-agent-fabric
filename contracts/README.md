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
