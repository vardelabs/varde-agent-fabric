# Implementation protocol v2

`implement-request.v2` is the v1 request plus required non-empty `model`.
`implement-artifact.v2` is the v1 artifact plus required `model` equal to
`provider`. All other key sets, UTF-8 path restrictions, snapshot digest,
completed-only results, no-fallback and aggregate limits remain unchanged.

OpenCode implementation accepts any model shaped `opencode-go/<name>`, where
`<name>` is 1 to 64 characters of lowercase letters, digits, `.` and `-`, starting
with a letter or digit, bound to worker kind `opencode`. The contract bounds only
the shape: the receiving deployment grants exact models, so a new OpenCode Go model
needs no fabric release. Codex may use v2 too.
Receivers continue to accept legacy Codex v1 envelopes without a requested-model
field. OpenCode cannot use v1. The public remote CLI emits v2 whenever `--model`
is supplied, for either harness. Consequently, a Codex caller using v2 must use a
route key that exactly matches the Codex worker's pinned `--model`; arbitrary
Codex route keys are no longer accepted by that worker.

Deploy a receiver whose fabric pin supports v2 before updating callers to emit
v2. An older gateway pin rejects v2 requests as malformed. Receivers must compare
artifact model, provider, cluster execution location, repository, worker,
correlation ID, snapshot digest and changed-file preimages.

Route permission alone is not write authorization or process confinement.
Implementation is opt-in through an explicit route with fallback policy `none`
and deployment-owned authorization. The public example routes remain unchanged.
Results describe proposed snapshot changes and do not apply them locally.
