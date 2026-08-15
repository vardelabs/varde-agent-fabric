# Contracts

Versioned, transport-neutral contracts for bounded work belong here.

## Rules

- Schemas and validators evolve together and are tested together.
- A contract must reject unknown or malformed data deliberately.
- Do not add filesystem paths, credentials, or references to caller-private data.
- Keep language-specific clients outside this directory unless they are validators.

The first contract migration will be reviewed separately.
