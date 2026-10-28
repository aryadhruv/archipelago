# Archipelago fork: working notes

This fork aims to be a clean, generic harness for comparing many models on the same tasks. Each model gets a short prompt and a world of files, chooses its own files, tools and approach, and is scored with rubrics. These notes cover what we learned from reading the upstream repo and running `examples/simple_task` locally. The remaining sections list the cleanup and fixes planned for this fork.

Upstream is `Mercor-Intelligence/archipelago`. Its commits are automated syncs ("Sync archipelago from Mercor-io/studio@…"). Expect large drops in each upstream sync, so keep our changes in small, isolated commits.

## Layout

- `environment/`: a Docker FastAPI sandbox on `:8080`. Endpoints: `/health`, `/data/populate?subsystem=filesystem`, `/apps` (MCP gateway config), `/mcp/`, `/data/snapshot`, `/docs`. `POST /apps` starts each configured MCP server over stdio and returns 503 if they aren't ready within about 5 minutes. Entry point: `environment/runner/main.py`.
- `agents/`: the agent runner, started with `uv run python -m runner.main` and routed through LiteLLM. The harnesses are in `agents/runner/agents/`. The one we use is `react_toolbelt_agent`. It starts with only meta-tools, so the model must find and add tools itself.
- `grading/`: the grading runner, also `uv run python -m runner.main`. It has 67 verifier types (the CAD ones were removed in this fork), an LLM judge, and scoring methods (`template` is a plain average, and weighted scoring is available).
- `mcp_servers/`: 9 servers that work on local files only: filesystem, spreadsheets, documents, pdfs, mail (mbox), calendar (.ics), chat, code, presentations. Each server has its own `.venv`. This fork removed the external market-data servers (`fmp`, `edgar_sec`).
- `grid/`: the grid runner. `python -m grid run <grid.json>` runs tasks × models × repeats in parallel and records state in `grid.db` (see `grid/README.md`).
- `examples/`: thin wrappers. `simple_task/main.py` runs populate → `/apps` → agent → snapshot → grading.
- `docs/fork-changes.md`: what this fork changed relative to upstream (removed servers and verifiers, added settings, fixes). Update it with every change in behavior.
- `docs/upstream-internals.md`: what upstream filtered out before publishing (agentic verifier, private packages, app servers), the Mercor services that stay dormant locally (Datadog, Redis, S3, LLM gateway, webhooks…), and what this fork removed. Check it before reviving any of them.
- `docs/grid-analytics.md`: how each dashboard number is calculated and aggregated.
- `docs/todo.md`: the fork's work list.

## Running locally

```bash
# agents/.env and grading/.env each need OPENROUTER_API_KEY; each runner loads its own
cd examples/simple_task && bash run.sh     # run.sh had no exec bit upstream
```

- Always use `uv sync --locked`: the lockfiles pin every dependency, and this fork dropped upstream's `exclude-newer` cutoffs. The image pins uv 0.13.4. An unlocked sync pulls a newer `mcp` that breaks `from mcp import McpError`.
- Model strings look like `openrouter/anthropic/claude-sonnet-5.5`. The judge is set in `grading_settings.json` with `llm_judge_model`.
- `mcp_config.json` needs the server's own venv Python (`/app/mcp_servers/<server>/.venv/bin/python`) and `MCP_TRANSPORT=stdio`. Without it you get `ModuleNotFoundError: mcp_schema`, or the server starts over HTTP on :5000.
- With `ENV=local` and the env vars unset, Mercor-internal code paths are inactive: the LLM gateway, Mercor's document cache, cost ledgers, Datadog, Redis, Modal and webhooks.
- The examples (`simple_task`, `grid`, `hil`) run text-only tasks on free OpenRouter models in every role, so any text model can run them.

### Local patches

- The old clone bind-mounted a patched `read_image_file.py` through `environment/docker-compose.override.yml`. That is now fixed in the server, so don't carry the override over.

## To-do

The work list (open items, the grid runner's requirements, task design and fairness rules, and what's done) lives in `docs/todo.md`. Update it as items land.

## Licensing
The code is Apache-2.0. The APEX-Agents dataset is for evaluation only.
