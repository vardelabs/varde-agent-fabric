# Continuous integration

Repository workflows live in `.github/workflows/` and are part of the public-ready skeleton boundary.

## Rules

- Keep workflows read-only by default and pin third-party actions to reviewed commit SHAs.
- A workflow must run the documented validation it claims to run.
- Do not use `pull_request_target` for untrusted pull-request code.

## Current workflows

- `skeleton.yml`: validates the required skeleton layout, documentation markers, and repository-maintenance checks.
