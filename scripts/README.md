# Scripts

Small repository-maintenance scripts live here.

## Rules

- Scripts must document their input, output, exit behavior, and failure mode.
- A script is not a security control unless it is wired into a documented, tested workflow.
- Do not add heuristic secret scanning as a publication certification mechanism. Public-ready review is a human security gate.

## Current scripts

- `validate-skeleton.sh`: verifies the required skeleton files and documented boundary markers. It exits zero only when the checked layout is present. It is not a content, secret, or history scanner.
