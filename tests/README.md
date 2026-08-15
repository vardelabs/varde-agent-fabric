# Tests

Cross-component safety, compatibility, and synthetic integration tests belong here.

## Rules

- Tests must not need production credentials, models, sockets, or repositories.
- Prefer hermetic temporary directories and synthetic packets.
- Git fixture checks must neutralize ambient global, system, signing, hook, and ignore configuration before asserting repository behavior.
- Add a regression test for every security or lifecycle defect fixed in this repository.
