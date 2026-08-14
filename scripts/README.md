# Scripts

Small repository-maintenance scripts live here.

## Rules

- Scripts must document their input, output, exit behavior, and failure mode.
- Keep this directory flat. Nested script directories are rejected by the layout validator.
- A script is not a security control unless it is wired into a documented, tested workflow.
- Do not add heuristic secret scanning as a publication certification mechanism. Public-ready review is a human security gate.

## Current scripts

- `validate-skeleton.sh`: verifies the required skeleton files, boundary markers, required ignore rules, workflow safety invariants, and script indexes. It exits zero only when the checked layout and policy markers are present. It is not a content, secret, or history scanner.
