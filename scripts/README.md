# Scripts

Small repository-maintenance scripts live here.

## Rules

- Scripts must document their input, output, exit behavior, and failure mode.
- Keep this directory flat. Nested script directories are rejected by the layout validator.
- A script is not a security control unless it is wired into a documented, tested workflow.
- Do not treat heuristic scanning as a publication certification mechanism. Public-ready review is a human security gate; supplemental scans must be narrow, documented, and wired into tested workflows.

## Current scripts

- `validate-skeleton.sh`: verifies the required skeleton files, boundary markers, required ignore rules, the absence of GitHub Actions workflows, and script indexes. It exits zero only when the checked layout and policy markers are present. It is not a content, secret, or history scanner.
- `check-public-safety.sh`: supplemental narrow scan for high-signal private paths, credential markers, and operational tokens. It is wired into CI and is not publication certification.
- `verify-release.sh`: verifies a supplied Git commit, release artifact SHA-256, and rollback ancestor. It prints a JSON evidence record and exits nonzero on malformed, missing, mismatched, or unrelated inputs.
