# Contributing

This repository is private during early development but every reachable commit must be safe to publish.

## Before opening a pull request

1. Run `./tests/test-skeleton.sh` and the relevant component tests.
2. Confirm the diff contains no secrets, private hostnames, tenant paths, real source diffs, customer material, model weights, or operational logs.
3. Keep deployment-specific configuration in a private overlay or local environment.
4. Add a regression test for any security or lifecycle defect.

## Boundaries

Do not grant dispatch adapters or inference services platform-posting, repository-write, or arbitrary shell authority. A named orchestrator remains responsible for policy validation and outward actions.
