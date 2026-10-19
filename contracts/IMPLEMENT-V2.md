# Implementation protocol v2

`implement-request.v2` is the v1 request plus required non-empty `model`.
`implement-artifact.v2` is the v1 artifact plus required `model`, and `provider`
names the model that served the job. All other key sets, UTF-8 path
restrictions, snapshot digest, completed-only results and aggregate limits
remain unchanged.

`fallback` is null when the requested model served the job, and then `provider`
equals `model`. A receiving deployment may serve a job on a sibling route when
the requested provider refuses it before doing any work. The artifact then
carries `fallback` as exactly `{"from": <model>, "reason": "rate-limit"}`, and
`provider` names the sibling model, which must differ from `model`. Only a v2
artifact can carry a fallback. `rate-limit` is the only reason. Whether a
fallback is allowed at all is the receiving deployment's policy. The caller's
route still uses fallback policy `none`, because the caller never runs a
fallback of its own.

OpenCode implementation accepts any model shaped `<provider>/<name>`, where
`<provider>` is 1 to 32 characters of lowercase letters, digits and `-`, and
`<name>` is 1 to 64 characters of lowercase letters, digits, `.` and `-`. Both
start with a letter or digit, and the model is bound to worker kind `opencode`.
The contract bounds only the shape: the receiving deployment grants exact
models, so a new OpenCode provider or model needs no fabric release. Codex may
use v2 too.
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
