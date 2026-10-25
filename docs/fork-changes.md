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

## Added

- **Per-runner `.env` loading.** The agent and grading CLIs each load their own
  `.env` (`agents/.env`, `grading/.env`); variables already set win. Each exits
  at startup if the orchestrator or judge model's provider key is missing.
  The check is skipped when `LLM_GATEWAY_API_BASE` is set.
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

## Fixed

- **`read_image_file`** detects PNG, JPEG, GIF and WebP from the file's bytes, not
  its extension, so a misnamed JPEG is no longer sent as `image/png`.
- **Judge task prompt.** Message content made of blocks no longer reaches the
  judge as `ValidatorIterator(...)`. This affected `output_llm`,
  `criterion_output_verifier`, `deep_research`, `posttraining_tool_call_check`
  and `response_tool_verifier`.
- **Agent timeout fault.** The runner's outer `AGENT_TIMEOUT_SECONDS` timeout now
  records a `budget_exhausted` / `wall_clock` fault, so it can be told apart
  from an infrastructure error.
