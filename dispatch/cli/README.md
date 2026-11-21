# `varde-agent-dispatch`

A small, public-safe command for invoking a bounded local backend and capturing
one output artifact. The repository contains no credentials, deployment
configuration, private host paths, or provider secrets. The CLI requires Bash
4.4 or newer.

## Contract

```text
DISPATCH_CODEX_SANDBOX=workspace-write \
varde-agent-dispatch \
  --model example/model \
  --routes /path/to/routes.json \
  --kind implement \
  --task "bounded task" \
  --workdir /path/to/worktree
```

### Isolated local review

`--workdir` must name an existing absolute directory. For a local review isolated
from the caller's working copy, create a Git worktree and pass its path. The
worktree isolates Git changes, it is not a filesystem sandbox. The CLI does not
create or remove worktrees.

```bash
# Run from a repository checkout at the exact review head.
git -C /path/to/repository worktree add --detach /path/to/review-worktree HEAD
dispatch/cli/varde-agent-dispatch \
  --model example/model \
  --routes dispatch/routes.example.json \
  --kind review \
  --diff /path/to/review.diff \
  --task "Review the supplied change packet" \
  --workdir /path/to/review-worktree \
  --output /path/to/review-artifact.json
```

Use a route whose target is `local`. Local dispatch invokes the selected backend
directly, does not contact the gateway, and does not silently fall back to a
remote target.

Remote review routes require the configured remote target, gateway credentials,
and all of the review identity fields and changed paths. The CLI rejects an
incomplete remote request before network access. A complete invocation includes:

```bash
dispatch/cli/varde-agent-dispatch \
  --model remote/model \
  --routes /path/to/private-routes.json \
  --kind review \
  --diff /path/to/review.diff \
  --task "Review the supplied change packet" \
  --workdir /path/to/review-worktree \
  --repository owner/repository \
  --pull-request 123 \
  --base-sha "$BASE_SHA" \
  --head-sha "$HEAD_SHA" \
  --correlation-id "$CORRELATION_ID" \
  --path src/file-a.py \
  --path tests/test-file-a.py \
  --output /path/to/review-artifact.json
```

The current public backends are `codex`, `opencode`, `grok`, `vibe`, and `agy`. The OpenCode adapter
supports CLI v2. Each backend is registered in `dispatch/adapters/`; see
[Adding a Backend](#adding-a-backend). The dispatcher passes only an explicit
runtime contract to a backend:

- `DISPATCH_TASK`
- `DISPATCH_WORKDIR`
- `DISPATCH_OUTPUT`
- `DISPATCH_KIND`
- optional `DISPATCH_DIFF`
- `DISPATCH_WRITE_PATHS`, for `--kind write` only: the `--path` values, one a line
- optional `DISPATCH_MODEL`
- optional `DISPATCH_TARGET`
- routed `DISPATCH_HARNESS_OVERRIDDEN`
- routed `DISPATCH_FALLBACK_POLICY`
- routed `DISPATCH_FALLBACK_HARNESS`
- routed `DISPATCH_FALLBACK_TARGET`
- the variables that backend's registration lists, when the caller sets them
  (for example `DISPATCH_CODEX_SANDBOX` to `codex`, and the
  `DISPATCH_OPENCODE_*` settings to `opencode`). A backend never receives
  another backend's variables.

A run that hits its provider's usage limit exits 75, so a caller can wait or
fall back instead of treating it as a failure. The dispatcher prints
`limit reached` to stderr and writes no output. It reaches 75 in two ways: the
backend exits 75 itself, or the backend fails and its adapter's `detect_limit`
finds a limit in the backend's stderr. That stderr still streams to the caller.
The message then names the kind, `quota` or `rate-limit`. The `codex` adapter
reads only Codex's last line, when it starts with `error` and names a rate,
usage, or quota limit. The `opencode` adapter reads only OpenCode's last
top-level `error` event. Model text on stderr is never matched. Any other
non-zero status is returned unchanged. On a remote route's local fallback, a
limit is a fallback failure like any other.

The caller owns authentication, deployment policy, and remote transport. When
`--model` is supplied, `--routes` provides the portable model-first registry;
`--harness` is optional and must be approved for that model. The public adapter
does not inherit the caller's environment,
credentials, provider configuration, or shell aliases.

`--kind implement` is the canonical write-capable implementation task.
`passthrough` remains a legacy compatibility spelling. `--kind write` asks a
content writer for new text for the files named by `--path`, each a relative
file path under the workdir, and refuses `--diff` and the review arguments. It
runs only on a local route and returns a proposal that nothing applies. `--kind review` additionally
requires `--diff` and exposes its canonical path through `DISPATCH_DIFF`.
Implementation jobs reject a supplied diff rather than silently changing task
semantics. Codex preserves the
ungated `passthrough` behavior, inheriting the caller's sandbox (read-only by
default), while canonical `implement` requires
`DISPATCH_CODEX_SANDBOX=workspace-write`.
The OpenCode adapter maps `review` to its `plan` agent and
`implement` and `passthrough` to its `build` agent. OpenCode implementation jobs require
`DISPATCH_OPENCODE_ALLOW_WRITE=1`; the default is fail-closed. It requires an explicit model and reads
provider authentication from OpenCode's native user configuration. The
dispatcher does not pass OpenCode provider keys or add OpenCode as a Codex
fallback. Review diffs are attached with OpenCode's native `--file` option.
On 2.x, when a review's final text is not findings, the backend resumes the
run's session once, with every tool denied, and asks for the findings alone.
That covers a run OpenCode ends at the `plan` agent's step limit, whose forced
turn writes a work summary. With `DISPATCH_DEADLINE_EPOCH` (unix seconds), the
exploring run stops early enough to leave the final turn its time, and a review
with too little time left fails with exit status 124. The limits are in
`backends/lib/review-steps.sh`. The `grok` backend does the same.
The adapter reads `opencode --version` and supports the 1.x and 2.x CLIs. On
1.x it invokes `--pure`, `--format json`, `--agent`, `--model`, `--dir`, and
`--file`. On 2.x, which removed `--pure` and `--dir`, it runs from the workdir
with `--standalone` so no shared background service starts, and sets
`OPENCODE_DISABLE_PROJECT_CONFIG=1` so plugins and config in the workdir are
not loaded. On both versions it sets `OPENCODE_DISABLE_AUTOUPDATE=1`, so a run
never replaces the pinned CLI with a downloaded one. The installed CLI help
surface is smoke tested when available.
The adapter output is OpenCode's raw JSONL event stream from `--format json`;
callers that need the final response must select the final `type=text` event.
When `opencode run` exits non-zero, the adapter writes no output file. It
prints only the run's top-level `type=error` events to stderr, capped at 4 KiB, because
other events can carry model text, and exits with OpenCode's status.
Set `DISPATCH_OPENCODE_MODELS_PATH` to pin the OpenCode models catalog, for a
provider that the public models list does not carry. It must be an absolute
regular file that is not group- or world-writable. When it is set, the adapter
exports it as `OPENCODE_MODELS_PATH` and sets `OPENCODE_DISABLE_MODELS_FETCH=1`,
so the run uses exactly that catalog. When it is unset, nothing changes.

Set `DISPATCH_OPENCODE_HOME` to a dedicated home directory containing only
the OpenCode configuration and authentication needed for this worker. The
adapter refuses to use the caller's general home directory, because an
OpenCode agent with file tools could otherwise read unrelated user credentials.
This dedicated home reduces credential exposure but is not a full filesystem
sandbox; the provider auth in it remains readable by the OpenCode process and
its tools. Use this local harness only for trusted bounded tasks until a
stronger worker sandbox is supplied by deployment policy.

The two CLI versions store provider authentication differently. A 1.x home
holds it in `auth.json`. A 2.x home keeps it in its own database and ignores
`auth.json`, so a home set up for 1.x lists no models under 2.x and runs fail
with `Model unavailable`. To set up a 2.x home, run the login as the worker
user, from an interactive terminal, and paste the key at the masked prompt.
Scrub the environment as the adapter does: an exported `XDG_DATA_HOME` would
otherwise send the credential outside the home, and the list check would still
pass.

```text
env -i HOME=/path/to/opencode-home PATH="$PATH" TERM="$TERM" opencode auth login <provider> --standalone
env -i HOME=/path/to/opencode-home PATH="$PATH" opencode auth list --standalone
```

The prompt refuses a non-interactive stdin. Do not use `--answer` for a key:
it puts the key in the process arguments, where other local users can read it.

### Grok

The `grok` backend reviews with Grok through OpenCode's built-in xAI provider,
under the **SuperGrok Subscription** sign-in, never an API key. It serves
`review` only and needs an `xai/<model>` id. Its output is OpenCode's event
stream, so the adapter uses OpenCode's review parser and limit detector. It
uses the shared isolation in `backends/lib/review-isolation.sh`: the
dedicated home rules, `PATH` entries resolved and kept outside the workdir, the
CLI under `env -i`, a private scratch with its own temp directory that is
removed on every exit once the CLI's whole process tree has stopped, and a
refusal of a `TMPDIR` or sign-in home inside the workdir.

- **Sign-in:** OpenCode keeps sign-ins in the same SQLite database as its
  sessions, and a session holds the reviewed diff. Each run therefore works on a
  private copy of `DISPATCH_GROK_HOME/.local/share/opencode/opencode.db`, made
  with SQLite's backup API. Afterwards, only a value the run itself changed,
  of an existing `xai` credential, is written back, so a refreshed token is
  kept. It is written only while the sign-in still holds the value the run
  started from, so an earlier run never overwrites a token a later one
  refreshed. No session and no new credential flows back.
- **Configuration:** the per-run home is empty, so no global config, agent,
  skill, plugin, or MCP server loads. `OPENCODE_DISABLE_PROJECT_CONFIG=1` stops
  the workdir's `opencode.json`, `.opencode`, `.claude`, `.agents`, and
  instruction files loading. `--standalone` keeps the run off a shared
  background service. Only the `xai` provider is enabled, and the `plan` agent
  runs with every tool denied but `read`, `glob`, and `grep`. A deny list would
  miss code mode's `execute`, which can reach the network and move the
  session's directory, and any tool a later release adds.
- **Instruction files:** after a read, OpenCode loads any `AGENTS.md` between
  the file and the workdir as an instruction, and no setting stops it. That
  gives reviewed text no more than the diff already has: the read tools are the
  only tools, so it can only steer the review.
- **Workdir:** OpenCode treats the folder of the nearest `.git` or `.hg` at or
  above the workdir as inside it, so the workdir must be that checkout's top
  level. A stray marker, such as an empty `/tmp/.git`, therefore refuses every
  workdir below it, so place review workdirs where none sits above them. It also decides
  from the path text whether a file is inside, so a run refuses a workdir that
  holds a link resolving outside it.
- **Limits:** only OpenCode's own 429 or `*.quota` error event reads as "limit
  reached". A 403 is reported as a plan that is not entitled to the model. A CLI
  exit of 75 for any other reason becomes 1.

To sign in, run OpenCode once, interactively, as the worker user, with the
dedicated home. It prints a URL and a code to enter on any device, then waits.
`--standalone` keeps it off the shared background service, which stalls under a
bare environment. OpenCode also tries to open a browser with `xdg-open` and
quits when there is none, so on a server put a no-op `xdg-open` first on
`PATH`, for example `/path/to/grok-home/bin/xdg-open` linked to `/bin/true`.
`opencode models --standalone` prints nothing in this setup, even for a
configured provider, so it cannot confirm a sign-in. A one-prompt
`opencode run --standalone --model xai/<model>` does, and a 403 there means the
plan is not entitled.

```text
env -i HOME=/path/to/grok-home PATH=/path/to/grok-home/bin:"$PATH" TERM="$TERM" OPENCODE_DISABLE_CHANNEL_DB=1 OPENCODE_DISABLE_AUTOUPDATE=1 opencode auth login xai --method device --standalone
```

### Mistral Vibe

The `vibe` backend reviews with the Mistral Vibe CLI in headless mode. It reads
an API key from `DISPATCH_VIBE_HOME/.env` (`MISTRAL_API_KEY=...`), a regular
file, never a link. It serves `review` only and needs a `mistral/<model>` id,
where the part after the slash is the Mistral API model name. It uses the
shared isolation in `backends/lib/review-isolation.sh`. The CLI runs
under `env -i` with only `PATH`, `HOME`, `TMPDIR`, and `VIBE_HOME`, all in a
private scratch, so a key, proxy, or endpoint override in the caller's
environment never reaches it.

- **Configuration:** the per-run `VIBE_HOME` holds a link to the key file and a
  `config.toml` the backend writes, so no agent, skill, MCP server, connector,
  or plugin of the operator's loads. `strict_config` refuses a setting the CLI
  would ignore. The workdir is never trusted (`--trust` is not passed), so the
  reviewed tree's `.vibe/` config is ignored; a trusted one can change the
  default agent.
- **Tools:** the run uses the `ask` agent with auto-approve disabled. In
  headless mode, `ask` denies every tool whose permission is `ask`, which covers
  `bash`, `edit`, `write_file`, and the web tools, also when called from
  `run_typescript`. Each of those is also set to `never`. `read_file` stays,
  and `outside_workdir = "deny"` refuses any path, a link included, that
  resolves outside the workdir. The `enabled_tools` setting does not limit the
  tools offered to the model, so the backend does not rely on it.
- **Result:** the CLI writes `export.json` to a private output directory on
  every exit. That record decides the outcome: `finished` with exit 0 returns
  the last assistant message's text, an error code of `rate_limit` exits 75,
  and an `outcome` of `deadline` is reported as out of time. Any other outcome
  fails, and its reason names only the outcome and error-code tokens, never the
  error message, which can quote a provider response. A CLI exit of 75 for any
  other reason becomes 1.
- **Deadline:** with `DISPATCH_DEADLINE_EPOCH`, the CLI gets `--time-limit` a
  few seconds before the deadline, and a watchdog stops it at the deadline.

Install the CLI with a CPython that matches its wheel, for example
`uv tool install --python 3.12 mistral-vibe`. Without a matching wheel, the
package builds from source and needs a Rust toolchain.

### Antigravity CLI (agy)

The `agy` backend is a content writer. It serves `write` only: the dispatcher
refuses `review` and `implement` for it. It runs the Antigravity CLI in print
mode under the CLI's own Google sign-in, kept in `DISPATCH_AGY_HOME`, which must
be an existing folder outside the caller home. It needs an explicit model id,
such as one `agy models` lists. It uses the shared isolation in
`backends/lib/review-isolation.sh`.

- **Job folder:** the backend copies each `--path` file that exists in the
  workdir into a private job folder, and agy runs there. A named file that does
  not exist yet may be created. A named path that is a link, a special file, a
  file with more than one link, over 320 KiB, or not UTF-8 is refused. Nothing
  else from the workdir is copied, and the workdir is never written.
- **Tools:** session-only settings (`--settings`, never saved to the home) set
  `accept-edits`, so the run may edit files in its workspace, and
  `request-review`, so every other tool asks, which a headless run refuses. Deny
  rules name commands, MCP tools, and URL reads outright, and
  `allowNonWorkspaceAccess` is off. The backend never passes
  `--dangerously-skip-permissions`, and slash commands are off.
- **Result:** the run must report `status` `SUCCESS` with exit 0 and no
  `denied_actions`. The job folder must then hold only the named files and the
  folders on the way to them. Any other entry, a link included, discards the
  run. The output is a `write-proposal.v1` (`contracts/write_contracts.py`) that
  lists each changed path with its `before_sha256` and `after_text`, null for a
  created or deleted file, and agy's final reply. A failure names only the
  report's `status` and `finished_reason` tokens, never its error text.
- **Limits and time:** a quota or rate-limit error in the report's `error`
  field exits 75; the response text is never matched. `--print-timeout` is
  `DISPATCH_AGY_TIMEOUT_SECONDS` (default 600, at most 3600), and a watchdog
  stops the CLI a few seconds later and reports out of time (124).
- **Sign-in:** agy runs with `HOME` set to the sign-in home itself, so a token
  it refreshes saves in place. The backend holds a lock on that folder for the
  run, so two runs never refresh at once. The CLI gets only `PATH`, `HOME`,
  `TMPDIR` (in the private scratch), and `AGY_CLI_DISABLE_AUTO_UPDATE=1`, so a
  provider key such as `GEMINI_API_KEY` never reaches it.
- **Conversations:** agy keeps each run's conversation in the home. After the
  run, still under the lock, the backend deletes that one conversation, found by
  the `conversation_id` in the report, and no other. A run that ends with no
  report, such as one stopped at its time limit, leaves its conversation behind,
  so still give that user a disk quota.

To sign in, run agy once, interactively, as the worker user, with the
dedicated home, and finish the Google sign-in. A one-prompt headless answer
with no tools then confirms it.

```text
env -i HOME=/path/to/agy-home PATH="$PATH" TERM="$TERM" AGY_CLI_DISABLE_AUTO_UPDATE=1 agy
```

The task and review diff are each bounded, and their combined prompt must fit
`DISPATCH_MAX_TASK_BYTES` (default 120000 bytes). The dispatcher rejects an
oversized input before the backend reads it. The default Codex sandbox is
`read-only`; the only alternate public mode is `workspace-write`.

`--model` may also be supplied through `DISPATCH_MODEL`. Whenever an explicit
`--model` is set, the registry must be supplied through `--routes` or
`DISPATCH_ROUTES_FILE`; the dispatcher refuses to guess a route. An explicit
`--harness` override is allowed only when the registry approves it. For
compatibility, callers using `--harness` with only the legacy `DISPATCH_MODEL`
forward that model without registry resolution. `--routes` without a model is
rejected. The caller can set `DISPATCH_CODEX_SANDBOX` to one of the public
modes. These values are forwarded explicitly to the backend. `HOME` is retained
for normal CLI runtime behavior, so this boundary does not claim to remove
credentials stored by a provider CLI. Provider authentication remains a
deployment concern.

## Remote implementation

A route with `target: remote`, `kinds: ["implement"]`, any harness the remote
side runs, and fallback policy `none` sends only explicitly named relative files:

Implementation tasks are additionally limited to 16 KiB, matching
`implement-request.v1`. For all remote routes, `DISPATCH_TIMEOUT_SECONDS` must
be from 1 through 900 seconds, matching the wire contracts. These checks happen
in the CLI before it opens a gateway connection.

```text
varde-agent-dispatch --model example/model --routes /path/to/remote-routes.json \
  --kind implement --task "bounded task" --workdir /path/to/worktree \
  --repository org/repo --correlation-id request-id --path src/file.py \
  --output /path/to/artifact.json
```

The route requires `DISPATCH_GATEWAY_URL` (HTTPS) and either `DISPATCH_GATEWAY_TOKEN`
or the pair `DISPATCH_GATEWAY_BROKER` and `DISPATCH_GATEWAY_BROKER_SECRET` in
the caller environment. With the broker pair, the request runs as a gateway job
that the client polls through the broker. It rejects review PR, SHA, and diff arguments, unsafe
or duplicate paths, symlinks anywhere in a file path, and excessive input.
The response is a validated `implement-artifact.v1` diff proposal, not an
applied change. Exit 0 means the artifact proposes at least one change. A
validated empty proposal is still published but exits 6, so callers must not
mistake successful worker completion for a file modification. Authentication
failures and unavailability never trigger a local implementation fallback.
Remote reviews retain their existing route.

## Remote write

A route with `target: remote`, `kinds: ["write"]`, and fallback policy `none`
runs a content writer on the remote side, for a writer that runs only under
another account's sign-in. The harness must hold the `write` role, the same as
a local write, and a model is required. The CLI sends a `write-request.v1`
(`contracts/write_contracts.py`) to `/v1/writes`: every `--path`, and the text
of each named file that exists. A missing path is one the writer may create.
The files are read the way the local backend stages them, so a link, a special
file, a file with more than one link, over 320 KiB, or not UTF-8 is refused.

```text
varde-agent-dispatch --model example/writer --routes /path/to/remote-routes.json \
  --kind write --task "bounded task" --workdir /path/to/worktree \
  --repository org/repo --correlation-id request-id --path docs/page.md \
  --output /path/to/proposal.json
```

The gateway answers with a `write-artifact.v1`. The CLI checks its identity
against the request, its named paths, and each change's `before_sha256`
against the text it sent. Then it reads the named files again, and if one
changed during the run, it discards the proposal. The output is the inner
`write-proposal.v1`, the same shape a local write returns, and it exits 0 also
when nothing changed. The workdir is never written. Credentials, the timeout
bound, and the no-fallback rule are those of a remote implementation.

## Adding a Backend

`dispatch/adapters/` is the one registry for every role that calls a model,
in this repository and in a deployment that imports it. A backend is two files
and one registration line. No other part of the dispatcher changes.

Before writing a backend that runs under a subscription or account sign-in,
check that the sign-in works for the target account type, headless, as the
service user the deployment will run it as, which has no login shell. Note
where the credential is stored: a system keyring, for example, may not exist for
that user. Tests against the CLI source or a stub cannot show this: they prove
what the CLI does with a credential, never that the service still accepts that
account type.

1. `dispatch/cli/backends/<name>.sh`, executable. It reads the runtime contract
   above from its environment, runs the tool, and writes one artifact to
   `DISPATCH_OUTPUT`. It exits 0 on success, 75 when the provider's usage limit
   is reached, and any other non-zero status on failure.
2. `dispatch/adapters/<module>.py`, defining
   `ADAPTER = Adapter(name="<name>", roles=frozenset({...}), env=(...))`.
   - `roles` names what the backend can serve, such as `review`, `implement`,
     or `write`. A role is a plain name, so a new role needs no core change.
   - `env` lists the caller variables the dispatcher forwards to this backend
     only. Each must start with `DISPATCH_` and must not be a contract variable.
   - `local_review_fallback` is set only when the backend can serve a remote
     review route locally while the gateway is unavailable. It needs the
     `review` role, and its stamp is `remote-unavailable-local-<name>`.
   - `implement_model` bounds the model an implement request may name for this
     backend. It needs the `implement` role.
   - `parse_review(raw)` turns the backend's review output into findings that
     satisfy the review contract. It needs the `review` role. The shared rules in
     `dispatch/adapters/review_output.py` map model wording onto the contract:
     - Severity aliases such as `critical` or `major` read as red, `minor` or
       `warning` as yellow, and `nit` or `info` as green. An unknown severity is
       red.
     - `path` or `filename` read as `file`, and a `title` with a `body`,
       `description`, or `detail` reads as the message. A finding with no text
       it recognises keeps its raw JSON as the message.
     - For OpenCode, whose answer streams as text events, a fence that wraps
       the whole final answer is unwrapped. Codex writes only its final message,
       which must be strict JSON. Prose around JSON is never rescued.
   - `detect_limit(stderr)` returns `quota`, `rate-limit`, or `None` from the
     backend's own error report. Match only the CLI's fatal report, never model
     text.
3. Add the module to `_REGISTERED` in `dispatch/adapters/__init__.py`.

`adapters.resolve(role, harness)` returns a backend only for a role it
declares. A deployment registers a private backend with
`adapters.register(Adapter(...))` from its own module, so the backend joins the
same registry without appearing in this repository. A local run through this
CLI resolves the harness for its role (`review` for `--kind review`, `write`
for `--kind write`, `implement` for `implement` and `passthrough`), so a backend
serves only the roles it declares, and runs the backend's script in `backends/`. The remote
runner's local fallback resolves the `review` role the same way. An adapter's
`parse_review` and `detect_limit` let any other caller read the backend's
output and limits without its own copy of that logic.

The wire contracts in `contracts/` check a worker name's shape only, and a
remote review or implement route accepts any worker name. A remote write route
sends only a harness with the `write` role, because the remote side runs the
same local backend. The side that runs the job decides which
of its workers may serve it, so a new backend can fill any role, a reviewer slot
included, with no contract change.

Copy the fake-CLI pattern in `tests/dispatch_cli/test-varde-agent-dispatch.sh`
to test the backend without a provider account or network access.

## Public boundary

This directory is intentionally not a deployment service. It does not contain
network listeners, Tailscale configuration, private-root policy, broker socket
paths, GitHub delivery, chat integration, or credentials. Those belong in a
private deployment repository or in a separate reviewed protocol adapter.
