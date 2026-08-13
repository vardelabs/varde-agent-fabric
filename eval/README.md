# Evaluation

Replay evaluation interfaces and public-safe synthetic fixtures belong here.

## Rules

- Keep real pull requests, customer code, and internal review records out of this repository.
- Fixtures must be synthetic or explicitly cleared for publication.
- Report capability and safety measurements with reproducible inputs and tool revisions.
- Fixtures with sensitive-looking extensions such as `.pem`, `.key`, `.bin`, or `.gguf` are intentionally ignored by the repository policy. Use neutral synthetic fixture names or place cleared exceptions in a documented, reviewed location; do not rely on a negation to make a real credential or model publishable.
- `eval/private-overlay` and its file variants are private operational paths. Public fixture directories such as `eval/fixtures/runtime/`, `eval/fixtures/logs/`, and `eval/fixtures/weights/` remain reviewable when they contain neutral, synthetic files.
- Evaluation results do not grant a model platform authority.
