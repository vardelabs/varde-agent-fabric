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

The current public backends are `codex`, `opencode`, and `gemini`. The OpenCode adapter
supports CLI v2. Each backend is registered in `dispatch/adapters/`; see
[Adding a Backend](#adding-a-backend). The dispatcher passes only an explicit
runtime contract to a backend:

- `DISPATCH_TASK`
- `DISPATCH_WORKDIR`
- `DISPATCH_OUTPUT`
- `DISPATCH_KIND`
- optional `DISPATCH_DIFF`
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
`passthrough` remains a legacy compatibility spelling. `--kind review` additionally
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

### Gemini CLI

The `gemini` backend runs the Gemini CLI in headless mode
(`--prompt`, `--output-format json`) and writes the final response text to the
output. It needs an explicit model id, which must be the full id the CLI
reports, not an alias. The prompt and the review diff go to the CLI on stdin, never in its
arguments.

It uses only the CLI's own Google sign-in. Set `DISPATCH_GEMINI_HOME` to a
dedicated home that holds that sign-in, outside the caller home, under the same
rules as `DISPATCH_OPENCODE_HOME`.

Each run gets its own home, 0700, which `HOME` and `GEMINI_CLI_HOME` point to and
which is removed when the run ends, by signal too. Its `.gemini/settings.json` is
the adapter's. Only the sign-in files of the dedicated home's `.gemini` are linked
in (`oauth_creds.json`, `google_accounts.json`, `installation_id`, and any
`*credential*` file), so the CLI's session state stays in the per-run home and goes
with it. A refreshed access token is not written back, because the CLI replaces
the link with a new file, so each run refreshes from the stored sign-in. The
dedicated home's own `settings.json` is never read. The adapter
cannot use a system settings file, because the CLI loads one only when root owns
it and every parent directory. The CLI always loads user settings, and a run
never adds workspace settings above them (see below), so the adapter's settings
are final. They set these values:

- `security.auth.enforcedType` to `oauth-personal`, so the CLI refuses an API key
  or any other auth type. The dispatcher also forwards no key variable.
- `advanced.ignoreLocalEnv`, so the CLI loads no plain `.env` file in or above
  the folder it runs in. Without it, an untrusted workdir could set the CLI's
  auth variables, such as `GOOGLE_CLOUD_PROJECT`, from its `.env`. It does not
  cover `.gemini/.env` in a trusted folder; see `implement` below.
- `tools.exclude` for `google_web_search` and `web_fetch`. The CLI's read-only
  policy allows both, and text in a reviewed diff could steer workdir content
  into a search or a fetch.
- Folder trust prompts and auto-update turned off.

A `review` runs in the workdir with it untrusted
(`GEMINI_CLI_TRUST_WORKSPACE=false`), so the workdir's `.gemini` settings and
extensions do not load. The CLI then runs in `default` approval mode, where
headless mode refuses every tool that needs approval, so the review writes
nothing. `implement` and `passthrough` require `DISPATCH_GEMINI_ALLOW_WRITE=1`.
An edit needs a trusted folder, so they run in a new empty folder inside the
dedicated home, trusted, in `auto_edit` mode, and reach the worktree through
`--include-directories`. The worktree's `.gemini` settings and `.env` are never
loaded, and the enforced sign-in holds there too. In a trusted folder the CLI
loads `.gemini/.env` from the folder and every parent, unfiltered. So the run
refuses when any parent of that folder already holds `.gemini/.env`, or is
owned by another user or writable by group or others, where another user could
plant one. A dedicated home under `/tmp` is therefore refused for these kinds;
put it under a private directory. The CLI splits an included directory on
commas, trims Unicode whitespace from it, and resolves `..` without following
links. So the adapter passes the resolved workdir, and refuses one whose path is
not printable ASCII, holds a comma, or has a name ending in a space.

The adapter reports "limit reached" (exit 75) only when the CLI reports a quota
error: its JSON error has type `TerminalQuotaError` or `RetryableQuotaError`, or
code 429. Either one is enough. The CLI raises these for a 429 from the model
API, and exits with status 173, which is 429 modulo 256. A run that exits 0 is
never a limit, even when stderr logs a 429 that a retry cleared. On a failed
run, only the CLI's last error report counts, never a retry it logged or an
object a tool error echoed earlier. A run killed before it reported is a plain
failure.

The adapter never accepts an answer from a model other than the one requested.
In headless mode the CLI has no fallback handler, so with an explicit model and
outside `plan` approval mode it reports a quota error rather than switching
models. The adapter still reads `stats.models`: if any model other than the
requested one answered a main or subagent turn, or the requested model answered
no main turn, the
run fails loudly with status 1 and its text is discarded. It is never reported
as a limit. Calls on other models in utility roles, such as routing or loop
detection, are normal. Subagents are also turned off in the adapter's settings,
and so is the edit correction that rewrites an edit on another model.
Any other CLI failure returns the CLI's status with its error type and message.

To sign in, run the CLI once, interactively, as the worker user, with the
dedicated home. Choose "Sign in with Google" and finish the browser flow. The
CLI stores the credential in the dedicated home.

```text
env -i HOME=/path/to/gemini-home GEMINI_CLI_HOME=/path/to/gemini-home PATH="$PATH" TERM="$TERM" gemini
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

The route requires `DISPATCH_GATEWAY_URL` (HTTPS) and `DISPATCH_GATEWAY_TOKEN` in
the caller environment. It rejects review PR, SHA, and diff arguments, unsafe
or duplicate paths, symlinks anywhere in a file path, and excessive input.
The response is a validated `implement-artifact.v1` diff proposal, not an
applied change. Exit 0 means the artifact proposes at least one change. A
validated empty proposal is still published but exits 6, so callers must not
mistake successful worker completion for a file modification. Authentication
failures and unavailability never trigger a local implementation fallback.
Remote reviews retain their existing route.

## Adding a Backend

`dispatch/adapters/` is the one registry for every role that calls a model,
in this repository and in a deployment that imports it. A backend is two files
and one registration line. No other part of the dispatcher changes.

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
     `review` role, and its stamp is `cluster-unavailable-local-<name>`.
   - `implement_model` bounds the model an implement request may name for this
     backend. It needs the `implement` role.
   - `parse_review(raw)` turns the backend's review output into findings. It
     needs the `review` role.
   - `detect_limit(stderr)` returns `quota`, `rate-limit`, or `None` from the
     backend's own error report. Match only the CLI's fatal report, never model
     text.
3. Add the module to `_REGISTERED` in `dispatch/adapters/__init__.py`.

`adapters.resolve(role, harness)` returns a backend only for a role it
declares. A deployment registers a private backend with
`adapters.register(Adapter(...))` from its own module, so the backend joins the
same registry without appearing in this repository. A local run through this
CLI resolves the harness for its role (`review` for `--kind review`,
`implement` for `implement` and `passthrough`), so a backend serves only the
roles it declares, and runs the backend's script in `backends/`. The remote
runner's local fallback resolves the `review` role the same way. An adapter's
`parse_review` and `detect_limit` let any other caller read the backend's
output and limits without its own copy of that logic.

The wire contracts in `contracts/` check a worker name's shape only, and a
remote route accepts any worker name. The side that runs the job decides which
of its workers may serve it, so a new backend can fill any role, a reviewer slot
included, with no contract change.

Copy the fake-CLI pattern in `tests/dispatch_cli/test-varde-agent-dispatch.sh`
to test the backend without a provider account or network access.

## Public boundary

This directory is intentionally not a deployment service. It does not contain
network listeners, Tailscale configuration, private-root policy, broker socket
paths, GitHub delivery, chat integration, or credentials. Those belong in a
private deployment repository or in a separate reviewed protocol adapter.
