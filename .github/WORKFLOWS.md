# Continuous integration

Repository workflows live in `.github/workflows/` and are part of the public-ready skeleton boundary.

## Rules

- Keep workflows read-only by default and pin third-party actions to reviewed commit SHAs.
- A workflow must run the documented validation it claims to run.
- Do not use `pull_request_target` for untrusted pull-request code.
- `skeleton.yml` syntax-checks and runs both repository-maintenance scripts, then checks the event-specific change range for whitespace with an empty-tree fallback when the push base is absent.

## Current workflows

- `skeleton.yml`: validates the required skeleton layout, documentation markers, repository-maintenance checks, and the public dispatch CLI smoke suite.
