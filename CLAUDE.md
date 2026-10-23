# Archipelago fork: working notes

This fork aims to be a clean, generic harness for comparing many models on the same tasks. Each model gets a short prompt and a world of files, chooses its own files, tools and approach, and is scored with rubrics. These notes cover what we learned from reading the upstream repo and running `examples/simple_task` locally. The remaining sections list the cleanup and fixes planned for this fork.

Upstream is `Mercor-Intelligence/archipelago`. Its commits are automated syncs ("Sync archipelago from Mercor-io/studio@…"). Expect large drops in each upstream sync, so keep our changes in small, isolated commits.

## Layout

- `environment/`: a Docker FastAPI sandbox on `:8080`. Endpoints: `/health`, `/data/populate?subsystem=filesystem`, `/apps` (MCP gateway config), `/mcp/`, `/data/snapshot`, `/docs`. `POST /apps` starts each configured MCP server over stdio and returns 503 if they aren't ready within about 5 minutes. Entry point: `environment/runner/main.py`.
- `agents/`: the agent runner, started with `uv run python -m runner.main` and routed through LiteLLM. The harnesses are in `agents/runner/agents/`. The one we use is `react_toolbelt_agent`. It starts with only meta-tools, so the model must find and add tools itself.
- `grading/`: the grading runner, also `uv run python -m runner.main`. It has 67 verifier types (the CAD ones were removed in this fork), an LLM judge, and scoring methods (`template` is a plain average, and weighted scoring is available).
- `mcp_servers/`: 9 servers that work on local files only: filesystem, spreadsheets, documents, pdfs, mail (mbox), calendar (.ics), chat, code, presentations. Each server has its own `.venv`. This fork removed the external market-data servers (`fmp`, `edgar_sec`).
- `examples/`: thin wrappers. `simple_task/main.py` runs populate → `/apps` → agent → snapshot → grading.
- `docs/upstream-internals.md`: what upstream filtered out before publishing (agentic verifier, private packages, app servers), the Mercor services that stay dormant locally (Datadog, Redis, S3, LLM gateway, webhooks…), and what this fork removed. Check it before reviving any of them.

## Running locally

```bash
set -a && source agents/.env && set +a     # runner does NOT load .env itself; key = OPENROUTER_API_KEY
cd examples/simple_task && bash run.sh     # run.sh had no exec bit upstream
```

- Use uv 0.13.4 or newer. Older versions can't parse `exclude-newer = "7 days"`. Always use `uv sync --locked`. An unlocked sync pulls a newer `mcp` that breaks `from mcp import McpError`.
- Model strings look like `openrouter/anthropic/claude-sonnet-5.5`. The judge is set in `grading_settings.json` with `llm_judge_model`.
- `mcp_config.json` needs the server's own venv Python (`/app/mcp_servers/<server>/.venv/bin/python`) and `MCP_TRANSPORT=stdio`. Without it you get `ModuleNotFoundError: mcp_schema`, or the server starts over HTTP on :5000.
- With `ENV=local` and the env vars unset, Mercor-internal code paths are inactive: the LLM gateway, Mercor's document cache, cost ledgers, Datadog, Redis, Modal and webhooks.
- `simple_task` passed with score 1.0 on Sonnet 5.5 via OpenRouter. It took 5 calls, 14,281 tokens and about 13 seconds.

### Local patches (uncommitted, from the test run)

- `examples/simple_task/{orchestrator_config,grading_settings,mcp_config}.json` were switched to OpenRouter and the venv/stdio settings above.
- `examples/simple_task/patches/read_image_file.py` is bind-mounted by `environment/docker-compose.override.yml`. It sniffs the image format from magic bytes because the snapshot's JPEGs are named `.png` and Anthropic rejects them. This should become a real fix in `mcp_servers/filesystem` (see below).

## Fix-up backlog

### Must fix
1. **Image MIME detection.** Move the magic-byte sniffing into `mcp_servers/filesystem/.../tools/read_image_file.py`, and check the pdfs and documents image tools for the same bug. Then delete the override and the patch.
2. **Errors vs. fails.** If any verifier errors, for example because the judge's API call fails, the whole run gets `final_score: null` and `grading_run_status: error`. The leaderboard must count these as harness failures and rerun them, not score them.
3. **Judge prompt is garbled.** The judge's "original task" section contained `ValidatorIterator(…)` instead of the prompt text, and image messages aren't converted to text. Fix this in the grading prompt builder.
4. **Context window fallback.** When LiteLLM doesn't know a model's context size, the runner assumes 128,000 tokens, which shifts when ReSum summarisation starts. Require `context_window_tokens` per model.
5. **Env loading.** Make the agent and grading runners load their `.env`, or fail early when keys are missing. A missing key currently surfaces later as an OpenRouter 401 in the judge.

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
