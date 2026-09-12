# Evaluation

Replay evaluation interfaces and public-safe synthetic fixtures belong here.

## Rules

- Keep real pull requests, customer code, and internal review records out of this repository.
- Fixtures must be synthetic or explicitly cleared for publication.
- Report capability and safety measurements with reproducible inputs and tool revisions.
- Fixtures with sensitive-looking extensions such as `.pem`, `.key`, `.bin`, or `.gguf` are intentionally ignored by the repository policy. Use neutral synthetic fixture names or place cleared exceptions in a documented, reviewed location; do not rely on a negation to make a real credential or model publishable.
- `eval/audit/`, `eval/runtime/`, `eval/sockets/`, `eval/logs/`, `eval/weights/`, and `eval/private-overlay` are private operational paths. Public fixture directories such as `eval/fixtures/runtime/`, `eval/fixtures/logs/`, and `eval/fixtures/weights/` remain reviewable when they contain neutral, synthetic files.
## Running the public evaluation

```bash
python3 -m eval.public_eval
python3 -m unittest -q tests/test_public_eval.py
```

The synthetic suite treats only a validated completed artifact as clean.
Malformed, stale, and unavailable worker outcomes must remain rejected.

The supplemental public-safety scan checks tracked text and binary content for
high-signal private markers. It is a narrow CI aid, not a publication
certification mechanism.
