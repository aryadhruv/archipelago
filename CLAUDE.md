# Archipelago fork: working notes

This fork aims to be a clean, generic harness for comparing many models on the same tasks. Each model gets a short prompt and a world of files, chooses its own files, tools and approach, and is scored with rubrics. These notes cover what we learned from reading the upstream repo and running `examples/simple_task` locally. The remaining sections list the cleanup and fixes planned for this fork.

Upstream is `Mercor-Intelligence/archipelago`. Its commits are automated syncs ("Sync archipelago from Mercor-io/studio@…"). Expect large drops in each upstream sync, so keep our changes in small, isolated commits.

## Layout

- `environment/`: a Docker FastAPI sandbox on `:8080`. Endpoints: `/health`, `/data/populate?subsystem=filesystem`, `/apps` (MCP gateway config), `/mcp/`, `/data/snapshot`, `/docs`. `POST /apps` starts each configured MCP server over stdio and returns 503 if they aren't ready within about 5 minutes. Entry point: `environment/runner/main.py`.
- `agents/`: the agent runner, started with `uv run python -m runner.main` and routed through LiteLLM. The harnesses are in `agents/runner/agents/`. The one we use is `react_toolbelt_agent`. It starts with only meta-tools, so the model must find and add tools itself.
- `grading/`: the grading runner, also `uv run python -m runner.main`. It has 67 verifier types (the CAD ones were removed in this fork), an LLM judge, and scoring methods (`template` is a plain average, and weighted scoring is available).
- `mcp_servers/`: 9 servers that work on local files only: filesystem, spreadsheets, documents, pdfs, mail (mbox), calendar (.ics), chat, code, presentations. Each server has its own `.venv`. This fork removed the external market-data servers (`fmp`, `edgar_sec`).
- `examples/`: thin wrappers. `simple_task/main.py` runs populate → `/apps` → agent → snapshot → grading.
- `docs/fork-changes.md`: what this fork changed relative to upstream (removed servers and verifiers, added settings, fixes). Update it with every change in behavior.
- `docs/upstream-internals.md`: what upstream filtered out before publishing (agentic verifier, private packages, app servers), the Mercor services that stay dormant locally (Datadog, Redis, S3, LLM gateway, webhooks…), and what this fork removed. Check it before reviving any of them.

## Running locally

```bash
# agents/.env and grading/.env each need OPENROUTER_API_KEY; each runner loads its own
cd examples/simple_task && bash run.sh     # run.sh had no exec bit upstream
```

- Use uv 0.13.4 or newer. Older versions can't parse `exclude-newer = "7 days"`. Always use `uv sync --locked`. An unlocked sync pulls a newer `mcp` that breaks `from mcp import McpError`.
- Model strings look like `openrouter/anthropic/claude-sonnet-5.5`. The judge is set in `grading_settings.json` with `llm_judge_model`.
- `mcp_config.json` needs the server's own venv Python (`/app/mcp_servers/<server>/.venv/bin/python`) and `MCP_TRANSPORT=stdio`. Without it you get `ModuleNotFoundError: mcp_schema`, or the server starts over HTTP on :5000.
- With `ENV=local` and the env vars unset, Mercor-internal code paths are inactive: the LLM gateway, Mercor's document cache, cost ledgers, Datadog, Redis, Modal and webhooks.
- `simple_task` passed with score 1.0 on Sonnet 5.5 via OpenRouter. It took 5 calls, 14,281 tokens and about 13 seconds.

### Local patches (uncommitted, from the test run)

- `examples/simple_task/{orchestrator_config,grading_settings,mcp_config}.json` were switched to OpenRouter and the venv/stdio settings above.
- The old clone bind-mounted a patched `read_image_file.py` through `environment/docker-compose.override.yml`. That is now fixed in the server (backlog item 1), so don't carry the override over.

## Fix-up backlog

### Must fix
1. ~~**Image MIME detection.**~~ Done: `read_image_file` sniffs PNG/JPEG/GIF/WebP magic bytes and falls back to the extension. The documents, pdfs and presentations image tools re-encode to JPEG, so they were never affected.
2. ~~**Errors vs. fails.**~~ Grading already does the right thing, and the outer agent timeout now records a `wall_clock` fault. The grid runner and leaderboard (items 7–8) must apply this rule:
   - Grading: a verifier that raises or returns `status: error` (judge API failure, auth error) makes the whole run `grading_run_status: error` with `final_score: null`. Every scoring method refuses to score around errored criteria, and judge calls retry transient errors 5 times first. Rerun grading; never record a score.
   - Agent `status: failed` is the model's fault (steps or output budget exhausted, context overflow, bad request). Grade it and count it as scored.
   - Agent `status: error` is infra (rate limit, 5xx, connection, MCP timeout). Rerun it, unless `fault` is `{"kind": "budget_exhausted", "budget": "wall_clock"}`: that is a timeout, which the fairness rules count as a fail (score 0).
   - Agent `status: cancelled` means the run was torn down externally. Rerun it.
3. ~~**Judge prompt is garbled.**~~ Done. Pydantic validates list-of-blocks message content into a read-once `ValidatorIterator`, and the judge's task-prompt extractors called `str()` on it. `output_llm` now resolves the content first and renders images as `[image_url omitted]`. `criterion_output_verifier`, `deep_research`, `posttraining_tool_call_check` and `response_tool_verifier` now use `runner.utils.task_prompt`. Any new code that reads `trajectory.messages[*]["content"]` must call `resolve_lazy_content(msg)` first.
4. ~~**Context window fallback.**~~ Done. Every `openrouter/...` model string misses the window table (OpenRouter isn't a known lane, on purpose, since a router can serve a smaller window), so the runner silently assumed 128,000 tokens and moved the point where ReSum summarisation starts. Setting `CONTEXT_WINDOW_FALLBACK_ENABLED=false` makes the agent runner raise instead. The grid runner must set it, pass `context_window_tokens` for every model, and check this before launching: an agent that raises ends as `status: error`, which item 2's rule would retry forever.
5. ~~**Env loading.**~~ Done. Each runner's CLI loads its own `<runner>/.env` (variables already set win). It then exits before any work if the orchestrator or judge model's provider key is missing (`litellm.validate_environment`; skipped when `LLM_GATEWAY_API_BASE` is set). The old 401 came from litellm's import-time `load_dotenv()`, which only looks in the cwd: the example runs grading from `grading/`, which had no `.env`.

### Build
6. **Parallel runs.** The compose file fixes the container name `archipelago-environment` and port `8080`, so concurrent runs collide. Make the compose project name and port configurable per worker. The scripts already read `ENV_URL`.
7. **Grid runner.** Write one driver that calls the three entry points directly with per-run arguments for the model × task × repeat grid, instead of copying an example's `main.py` for each task.
8. **Leaderboard.** Combine `trajectory.json` (steps and token usage) and `grades.json` across runs.
9. **Sub-agents.** `agents/runner/utils/subagent.py::spawn_subagent()` exists, but nothing calls it and no harness exposes it as a tool. To allow delegation, add a `delegate(task, context)` tool to `react_toolbelt_agent`. Then decide whether every model gets it, because it must be the same for all models. Sub-agents share the parent's MCP gateway and `trajectory_id`, so they act on the same world and their token use must be credited to the parent run.
   - Not the same thing: Virtual Coworker Agents (`environment/runner/coordinator/`) are personas that task authors start through event triggers, such as a tool call being seen or time elapsing. The model being tested can't start them. Tool calls carry an `actor_id` (`target_agent` / VCA id / `coordinator`).
10. **App-state verifiers without servers.** `grading/` has verifiers for third-party app state (QuickBooks, Xero, Workday, SAP, BambooHR, Greenhouse, TaxJar), but the repo has no MCP servers for those apps, so these verifiers can't run as shipped. Either remove them or pair each one with a server.

### Task and world design
- Keep worlds and tasks separate, as the HuggingFace `mercor/apex-agents` dataset does with `world_id` and `task_input_files`. Build 2–3 company worlds with many tasks each.
- App data is plain files you can generate: mail as mbox under `/.apps_data/mail`, calendar as `.ics` under `/.apps_data/calendar`, chat under `/.apps_data/chat` (layout not confirmed yet).
- When a criterion checks a file, the prompt must say the output goes in a file. Otherwise some models answer in chat and file-based criteria fail.
- Set what evidence each criterion is judged on (final answer, specific file types, or everything). Use exact checks such as `golden_file_match`, `pattern_match_check`, `spreadsheet_verifier` and `tool_call_check` for figures, and the LLM judge for completeness, tone and process.
- Document extraction order is Mercor cache → Reducto (`REDUCTO_API_KEY`) → local parser. Locally only the last two are available, so compare grades with and without Reducto for PDFs and complex spreadsheets.

### Fairness rules
- Use the same harness, `max_steps`, timeout and tool set for every model. Count a timeout as a fail.
- Pin one judge model that isn't one of the models being tested, and spot-check it against human grading.
- Before the full grid, do a one-task smoke run of each model with every enabled server, so provider quirks don't show up as low scores.

## Licensing
The code is Apache-2.0. The APEX-Agents dataset is for evaluation only.
