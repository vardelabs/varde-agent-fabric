# CLAUDE.md: varde-agent-fabric

This repo will be public. Read `README.md` for the architecture and `CONTRIBUTING.md` for the pull request checklist before changing anything. Every reachable commit must be safe to publish, so a mistake here cannot be fixed by a later commit.

## Rules

- **Nothing private in the repo.** Do not add secrets, private hostnames, tenant paths, deployment values, model weights, real source diffs, or operational logs. These belong in a private overlay (see `CONTRIBUTING.md`).
- **No customer names or data.** Do not name a customer firm, its staff, or its data anywhere: code, comments, docs, tests, fixtures, branch names, commit messages, or issue and PR text. Write "a firm" or "the example firm", never an invented firm name, which can collide with a real one.
- **No internal names in content.** Do not name internal people, agents, hosts, or private repos in code, comments, docs, tests, or fixtures. Use a generic role ("the orchestrator", "a worker", "the reviewer"). A test that needs a tenant-shaped path uses an invented name.
- **No reviewer credits in comments.** A comment states the constraint or the reason. It does not credit a reviewer, a review round, or a date.
- **Run the checks before a PR:** `./tests/test-skeleton.sh`, `./tests/test-public-safety.sh`, and the relevant component tests. `scripts/check-public-safety.sh` catches keys, tokens, tenant paths, and webhooks. It does not catch names, so the two name rules above depend on review.
- **Keep the public and private sides apart.** A portable change names its public contract and tests. Deployment rollout work stays in the private overlay.
