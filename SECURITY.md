# Security Policy

## Reporting a Vulnerability

Report a vulnerability privately through GitHub: open the repository's
**Security** tab and choose **Report a vulnerability**. Do not open a public
issue for a security problem.

If you cannot use GitHub's private reporting, email hello@vardelabs.com with
"Security: varde-agent-fabric" in the subject. Send the details only after we
reply with a private channel.

Include the affected component (`dispatch/`, `contracts/`, `inference/`, or
`eval/`), the commit you tested, the steps to reproduce, and the impact you
expect.

## What to Expect

We acknowledge a report within 5 business days. We then confirm or reject the
issue, agree a disclosure date with you, and credit you in the fix unless you
ask us not to.

## Scope

This repository is pre-1.0. Only the latest commit on `main` receives fixes.

These count as vulnerabilities here:

- a worker or adapter that gains repository-write, shell, platform-posting, or
  credential authority it was not granted
- a malformed, stale, or mismatched artifact that passes contract validation
- a route that falls back between local and remote targets without an explicit
  policy
- a scrub or size limit that a caller-supplied input can bypass

A private deployment overlay, its credentials, and its hosts are out of scope.
Report a problem with a model provider or a backend CLI to that project.
