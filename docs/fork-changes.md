# Changes from upstream

This fork of [Mercor-Intelligence/archipelago](https://github.com/Mercor-Intelligence/archipelago)
is kept as a generic harness for comparing many models on the same tasks. It
differs from upstream as follows. [`upstream-internals.md`](./upstream-internals.md)
covers what upstream itself filters out or leaves dormant, and how to restore
what we removed.

## Removed

- **External-data MCP servers.** `fmp` and `edgar_sec` call external
  market-data APIs, so they can't run fully locally. The nine remaining servers
  work on files inside the world: filesystem, documents, spreadsheets,
  presentations, pdfs, mail, calendar, chat, code.
- **CAD verifiers.** `kicad`, `freecad` and `cad_llm_judge`, with their
  dependencies (cadquery, OCP, trimesh, …).
- **`exclude-newer` cutoffs.** Upstream's rolling `exclude-newer = "7 days"`
  and its per-package exemptions are gone from `agents/`, `environment/` and
  `grading/`. Re-locking on different days resolved different sets, and uv
  releases older than 0.13.4 couldn't parse them. The lockfiles pin every
  dependency, and `uv sync --locked` installs exactly those. The environment
  image now pins uv 0.13.4.

## Added

- **Per-runner `.env` loading.** The agent and grading CLIs each load their own
  `.env` (`agents/.env`, `grading/.env`); variables already set win. Each exits
  at startup if the orchestrator or judge model's provider key is missing.
  The check is skipped when `LLM_GATEWAY_API_BASE` is set. When both
  `LITELLM_PROXY_API_BASE` and `LITELLM_PROXY_API_KEY` are set, it instead
  calls the proxy's `GET /v1/models` and exits unless the key is accepted and
  each model (without its `litellm_proxy/` prefix) is listed or matched by a
  wildcard route. `/v1/models` leaves out some aliases the proxy still routes
  (router group aliases, per-key aliases); `LITELLM_PROXY_CHECK_MODELS=false`
  then checks only the key. The grid's preflight uses the same check.
- **LiteLLM proxy routing.** With `LITELLM_PROXY_API_BASE` and
  `LITELLM_PROXY_API_KEY` set, the agent runner, the grader and the simulated
  human all send every call through the proxy, so model strings are its aliases.
  The context-window table drops a `litellm_proxy/` prefix only before a known
  lane (`litellm_proxy/anthropic/...`); a bare alias, or one in another lane
  (`azure/...`), needs `context_window_tokens`. The
  grid's provider label drops `litellm_proxy/` and reads the vendor from a
  Bedrock-style id (`us.anthropic.claude-...` gives `anthropic`).
- **`CONTEXT_WINDOW_FALLBACK_ENABLED`** (agents, default `true`). Set it to
  `false` to raise instead of assuming a 128k-token window for a model whose
  window is unknown. This is always the case for `openrouter/` models, so with
  `false`, set `context_window_tokens` in the agent config for every model.
- **Parallel environments.** The environment's compose file no longer fixes the
  container name. The host port comes from `ENV_PORT` (default `8080`) and
  the compose project is named after it (`archipelago-<port>`), so each port
  is its own environment. All share the image `archipelago-environment:local`,
  overridable with `ENV_IMAGE`. Containers use Docker's default bridge
  network rather than one network per project. The examples build `ENV_URL`
  from `ENV_PORT` unless `ENV_URL` is set, and skip `--build` when
  `ENV_IMAGE` is set.
  - Upgrading: a container from the old layout (project `environment`,
    container `archipelago-environment`) keeps holding port 8080, and the new
    one fails with "port is already allocated". The examples remove that
    container themselves when they run on port 8080, as their `down` used to.
    For manual `docker compose up`, remove it and its old image
    (`environment-environment`) once with
    `docker compose -p environment down --rmi local`.

- **Grid runner** (`grid/`, new). `python -m grid validate|run|status
  <grid.json>` runs every task × model × repeat through the environment, agent
  and grading runners, several at once, from inputs in the HF apex-agents
  layout. It builds one image per grid tagged with the commit, gives each run
  its own port and compose project and each attempt its own output dir, reruns
  infra errors (not model failures), and keeps state in SQLite so a stopped
  grid resumes. Unlike `examples/hugging_face_task`, it grades runs whose agent
  `failed` (they count), and grades against a snapshot taken after the world
  and the task overlay are loaded. See `grid/README.md`.
  `run` and `serve` also append their own output to `<grid_dir>/grid.log`,
  and every attempt keeps its container's logs in `env.log`, not only
  attempts that hit an infra error.
  When a grid finishes, `run` and `serve` write `results.csv` and
  `results.json` (the same data as the export endpoints) next to `grid.db`.
  Each grading attempt keeps its own `grades.<n>.json`, and the `grade` event
  in `grid.db` carries each criterion's judge verdict and rationale.
  Preflight fails when OpenRouter no longer serves one of the grid's models
  (tested, judge or human), instead of each run retrying a 404. Grid's
  default `mcp_config.json` starts each MCP server with
  `FASTMCP_SHOW_SERVER_BANNER=false`, `FASTMCP_CHECK_FOR_UPDATES=off` and
  `LOGURU_LEVEL=INFO`, so `env.log` keeps warnings and errors without the
  banner and DEBUG `tools/list` dumps.

- **Per-call usage log in `react_toolbelt_agent`.** After each model call it
  logs `message_type="llm_usage"` at DEBUG with `prompt_tokens`,
  `completion_tokens` and `latency_ms`, so a live view can count tokens while
  the run is going. `trajectory.json`'s `usage.call_log` has the same numbers
  at the end.
- **Dashboard** (`grid serve`). A plain HTML/CSS/JavaScript UI and the API
  behind it: REST and server-sent events over a grid's `grid.db`, with start,
  pause, stop, smoke test and retry. Served at `http://127.0.0.1:7070/`. See
  `grid/README.md`.
- **Simulated human** (`mcp_servers/human`, new; grid `human` setting). An
  `ask_human(question)` MCP tool lets the model being tested ask the person
  who assigned the task. An LLM plays that person, from the task prompt, a
  persona in `world_descriptions.json`, and `.apps_data/human/knowledge.md`.
  It shares files from `.apps_data/human/files/` by uploading them to
  `/from_user/` rather than reading them; it answers through a
  `reply(answer, share)` tool, so only the files it lists are sent. It runs on the host, one server per
  agent attempt, which the sandbox's gateway reaches over HTTP at
  `host.docker.internal`; its provider key (from `agents/.env`) and its folder
  never enter the container. Grid containers map `host.docker.internal` to
  `host-gateway`, and `.apps_data/human/` is left out of populate. See
  `grid/README.md`.
- **Append-only scratchpad** (`react_toolbelt_agent`, config `scratchpad`,
  default off; on in the grid's default agent config). Two always-available
  tools: `scratchpad_write(note)` appends a note of up to 8000 characters, and
  `scratchpad_read(last_n?)` returns notes oldest first (the newest that fit
  in 40,000 characters, saying how many were left out). Their descriptions ask
  the model to log its plan first and then a note after each step.
  - Append-only by design: there is no tool to edit or delete a note, and the
    notes live in the agent process, outside the sandbox, so filesystem and
    code tools can't reach them.
  - Each entry records its step and elapsed time, plus a SHA-256 over its
    content and the previous entry's hash (`scratchpad.verify_chain`). The
    chain is a checksum, not a seal: whoever can edit the trajectory can
    recompute it. Each entry is also written to `agent_events.jsonl`
    (`message_type` `scratchpad_entry`) as it is made, and that log is the
    record to check the trajectory against.
  - Saved in the trajectory as `output.scratchpad`. Notes survive context
    compaction, since `scratchpad_read` returns them word for word. Scratchpad
    calls in the same batch as `final_answer` are handled first, so they are
    never dropped or left without a tool result.
  - The grid's judge doesn't see the notes. The grid wires up only
    `output_llm`, which reads the final answer, the world's file changes and
    the user and assistant text of the messages; notes are in tool calls and
    tool results, which it leaves out. A model that repeats its notes in plain
    text exposes them, as with any text reply. Verifiers that read tool calls
    (`tool_call_llm_check`, `tool_call_check`, the `db_*_llm_tools` checks,
    `vca_behavior_llm_check`) would see them if a task used one.
  - Grid: when the agent config turns the scratchpad on (`true` or `"true"`,
    parsed as the agent parses it, and only for `react_toolbelt_agent`), the
    grid adds `defaults/scratchpad_prompt.md` to the system prompt, the same
    wording for every model. Run Detail shows the notes, and reads them from
    the event log when the grid killed the agent before it wrote its output.
  - The model is told the scratchpad is private, and nothing in the tool or
    prompt text mentions reviewers or grading, because models change what
    they write when told they're watched. The notes are in fact logged and
    saved with the run. So a run measures how a model behaves when it
    believes it's unobserved; read its notes that way.
- **No "Think Before Acting" in the default prompt.** The grid's
  `defaults/system_prompt.md` and `examples/simple_task`'s prompt drop the
  section asking the model to explain its reasoning in 1-3 sentences before
  each tool call. Models reason before acting anyway, and no model-facing
  text asks for "thinking". The agent README's recommended prompt still has
  the upstream wording.
- **"Working on your own" in the default prompt.** The grid's
  `defaults/system_prompt.md` tells the model that its text messages aren't
  read until `final_answer` (so it should ask through a tool if it has one,
  as in human-in-the-loop grids), that any files the task refers to are in
  the filesystem, and that a file it is asked for must be created with a
  tool. Models stopped to ask for
  confirmation, or pasted a deliverable into chat, and ended the run with
  nothing saved.
- **Round-trip preflight** (grid). After the key and OpenRouter checks pass,
  `validate`, `run` and the dashboard's preflight make one short call per
  model through the agent runner, asking it to call a dummy tool. An error
  that will repeat (HTTP 400, 401, 402, 403, 404 or 422: not found, auth,
  no credits, no access, bad request) fails preflight. The status counts as
  well as the error class, since litellm maps OpenRouter's 402 and 403 to a
  generic `APIError`. A temporary error, writing the call as text, or not calling the
  tool is a warning, so one flaky provider doesn't block the grid.
- **Shorter validation errors** (every MCP server's
  `validation_error_sanitizer.py`). Each bad argument name is echoed back,
  and a malformed call can turn a whole payload (a CSV, say) into one key.
  A name over 80 characters, or with a newline, is cut to its first 60 and
  its length. Every error keeps its field and message; only the echoed name
  is shorter.
- **Skipped PDF pages are named** (`read_pdf_pages`). Pages past the end
  were already dropped silently. The result's errors now say which pages
  were skipped and how many the document has.

## Fixed

- **Mail mbox lock** (mail server). `mailbox.lock()` never waits, so overlapping
  mail calls failed: `ExternalClashError` escaped the `OSError` handler, and
  two threads of one process locking in the same second hit `FileExistsError`
  on mailbox's pre-lock file. `send_mail` (and reply/forward) and the mailbox
  reader now lock through `locked_mbox`, which serializes the process's threads
  per mbox, then retries the file lock with backoff for up to
  `APP_MAIL_LOCK_TIMEOUT_SEC` (default 10) before reporting the mailbox busy.
  It removes a dot-lock older than `APP_MAIL_STALE_LOCK_SEC` (default 60), left
  by a killed process.
- **Environment image** (13.4 GB of layers to 5.0 GB; a warm rebuild takes
  about 2.5 min, down from 15–20).
  - The ignore file is `environment/Dockerfile.dockerignore`. BuildKit never
    read `environment/.dockerignore`, because the context is the repo root, so
    host venvs and `environment/.env` were copied into the image.
  - Every `uv sync` is `--locked`. Six server lockfiles lacked metadata for
    their local `mercor-mcp-shared` package, and the build had been
    re-locking them silently. They're refreshed, with no version changes.
  - Server venvs skip their dev groups (basedpyright and its bundled Node,
    ruff, pytest). The runner's venv keeps them, because `runner/utils/s3.py`
    imports `types_aiobotocore_s3` at runtime. The image sets `UV_NO_SYNC=1`,
    but the gateway passes a stdio server only its config `env`, so a config
    that starts servers with `uv run` must set `UV_NO_SYNC` there too (as
    `examples/hugging_face_task` does). Otherwise uv reinstalls the dev
    groups when the server starts.
  - The uv and npm caches and apt's package lists are cache mounts, so they
    stay out of the image; uv's cache alone was 3 GB. All apt packages install
    in one step.
  - The server venvs and the code sandbox mirror install in one step that
    ends with `hardlink`, so identical files are stored once (about 4.5 GB).
  - A failed step now fails the build: the code step's `;` chain is now `&&`,
    and each server sync in the loop exits on error. The unused GitHub-token secret is gone.
  - New build args `BASE_IMAGE` and `APT_MIRROR` (set through
    `ARCHIPELAGO_BASE_IMAGE` and `ARCHIPELAGO_APT_MIRROR` for `docker
    compose`) let restricted networks use a registry or apt mirror. The
    Dockerfile has no `# syntax` line, so BuildKit doesn't fetch a frontend
    from Docker Hub. The mirror must serve `/debian` and `/debian-security`, and
    an `https://` mirror needs a base image with CA certificates. The mirror
    URL is visible in `docker history`. The grid's image tag hashes only the
    source trees, so pass `--rebuild` after changing either.

- **`read_image_file`** detects PNG, JPEG, GIF and WebP from the file's bytes, not
  its extension, so a misnamed JPEG is no longer sent as `image/png`.
- **Judge task prompt.** Message content made of blocks no longer reaches the
  judge as `ValidatorIterator(...)`. This affected `output_llm`,
  `criterion_output_verifier`, `deep_research`, `posttraining_tool_call_check`
  and `response_tool_verifier`.
- **Agent timeout fault.** The runner's outer `AGENT_TIMEOUT_SECONDS` timeout now
  records a `budget_exhausted` / `wall_clock` fault, so it can be told apart
  from an infrastructure error.
- **Unanswered calls after `final_answer`** (`react_toolbelt_agent`).
  `final_answer` returned early, whether it was accepted or rejected for open
  todos, so other calls in the same batch got no result and the next request
  failed with a 400. Each of those calls now gets a "Not run" result asking the
  model to call it again.
- **Calls before `final_answer` dropped** (`react_toolbelt_agent`). MCP calls
  run after the batch loop, and `final_answer` returned before it, so in
  `[write a file, final_answer]` the write never ran: the answer was accepted,
  the run ended without the file, and the model was scored as if it hadn't
  done the work. Calls before `final_answer` in a batch now run first; only
  calls after it get "Not run".
- **Run Detail on odd event-log lines** (grid). A line in `agent_events.jsonl`
  whose `extra` wasn't an object made the scratchpad fallback raise, and Run
  Detail returned 500. Such lines are now skipped.
