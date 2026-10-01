# Implementation protocol v2

`implement-request.v2` is the v1 request plus required non-empty `model`.
`implement-artifact.v2` is the v1 artifact plus required `model` equal to
`provider`. All other key sets, UTF-8 path restrictions, snapshot digest,
completed-only results, no-fallback and aggregate limits remain unchanged.

OpenCode implementation accepts exactly `opencode-go/kimi-k2.7-code` and
`opencode-go/kimi-k3`, bound to worker kind `opencode`. Codex may use v2 too.
Legacy Codex v1 envelopes remain accepted without a requested-model field.
OpenCode cannot use v1. The public remote CLI emits v2 for both harnesses, forwarding its required
model argument. Receivers
must compare artifact model, provider, cluster execution location, repository,
worker, correlation ID, snapshot digest and changed-file preimages.

Route permission alone is not write authorization or process confinement.
Implementation is opt-in through an explicit route with fallback policy `none`
and deployment-owned authorization. The public example routes remain unchanged.
Results describe proposed snapshot changes and do not apply them locally.
