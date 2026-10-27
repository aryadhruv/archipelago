# To-do

The work list for this fork. Move an item to **Done** when its PR merges, and
record any change in behavior in [`fork-changes.md`](./fork-changes.md).

## Open

### Build

1. **Sub-agents.** `agents/runner/utils/subagent.py::spawn_subagent()` exists,
   but nothing calls it and no harness exposes it as a tool. To allow
   delegation, add a `delegate(task, context)` tool to `react_toolbelt_agent`.
   Then decide whether every model gets it, because it must be the same for
   all models. Sub-agents share the parent's MCP gateway and `trajectory_id`,
   so they act on the same world and their token use must be credited to the
   parent run.
   - Not the same thing: Virtual Coworker Agents
     (`environment/runner/coordinator/`) are personas that task authors start
     through event triggers, such as a tool call being seen or time elapsing.
     The model being tested can't start them. Tool calls carry an `actor_id`
     (`target_agent` / VCA id / `coordinator`).
2. **App-state verifiers without servers.** `grading/` has verifiers for
   third-party app state (QuickBooks, Xero, Workday, SAP, BambooHR,
   Greenhouse, TaxJar), but the repo has no MCP servers for those apps, so
   they can't run as shipped. Either remove them or pair each with a server.

### Small fixes

- The committed `examples/simple_task/mcp_config.json` uses plain `python`
  without `MCP_TRANSPORT=stdio`, so the example fails on a fresh clone with
  `ModuleNotFoundError: mcp_schema`. Use the server's venv Python and stdio.
- Optional: bind the environment port to `127.0.0.1` in
  `environment/docker-compose.yml`, so the sandbox isn't reachable from the
  network.

### Task and world design

- Keep worlds and tasks separate, as the HuggingFace `mercor/apex-agents`
  dataset does with `world_id` and `task_input_files`. Build 2–3 company
  worlds with many tasks each.
- App data is plain files you can generate: mail as mbox under
  `/.apps_data/mail`, calendar as `.ics` under `/.apps_data/calendar`, chat
  under `/.apps_data/chat` (layout not confirmed yet).
- When a criterion checks a file, the prompt must say the output goes in a
  file. Otherwise some models answer in chat and file-based criteria fail.
- Set what evidence each criterion is judged on (final answer, specific file
  types, or everything). Use exact checks such as `golden_file_match`,
  `pattern_match_check`, `spreadsheet_verifier` and `tool_call_check` for
  figures, and the LLM judge for completeness, tone and process.
- Document extraction order is Mercor cache → Reducto (`REDUCTO_API_KEY`) →
  local parser. Locally only the last two are available, so compare grades
  with and without Reducto for PDFs and complex spreadsheets.

### Fairness rules

Apply these in every grid.

- Use the same harness, `max_steps`, timeout and tool set for every model.
  Count a timeout as a fail.
- Pin one judge model that isn't one of the models being tested, and
  spot-check it against human grading.
- Before the full grid, do a one-task smoke run of each model with every
  enabled server, so provider quirks don't show up as low scores.

## Done

- **Removed external and Mercor-internal pieces.** `fmp`, `edgar_sec` and the
  CAD verifiers; wrote `upstream-internals.md` (#1).
- **Image MIME detection.** `read_image_file` sniffs PNG/JPEG/GIF/WebP magic
  bytes and falls back to the extension (#2).
- **Errors vs. fails.** Grading already refused to score around errored
  criteria; the agent's outer timeout now records a `wall_clock` fault (#2).
- **Judge prompt.** Block-list message content no longer reaches the judge as
  `ValidatorIterator(...)`. New code that reads `trajectory.messages[*]["content"]`
  must call `resolve_lazy_content(msg)` first (#2).
- **Context window fallback.** `CONTEXT_WINDOW_FALLBACK_ENABLED=false` raises
  instead of assuming 128k tokens (#2).
- **Env loading.** Each runner loads its own `.env` and exits early if the
  model's provider key is missing (#2).
- **Code sandbox packages** (#3).
- **Grid runner** (`grid/`). Runs tasks × models × repeats in parallel from
  inputs in the HF apex-agents layout: one image per grid tagged with the
  commit, its own port and container per run, an output dir per attempt,
  retries with backoff for infra errors, explicit context windows, and the
  errors-vs-fails rules (see `grid/README.md`). State is in SQLite, so a
  stopped grid resumes.
- **Dashboard** (`grid serve`): setup and preflight, Mission Control, the
  Live Map (live, focus with the world's files, replay), run detail,
  leaderboard and infra, on the API's REST endpoints and event stream.
- **Leaderboard** (`GET /api/leaderboard`): mean score ± CI, pass rate,
  tokens, time, errors, fails and tool use per model, and a task × model
  matrix.
- **Parallel runs.** One environment per port (`archipelago-<port>`),
  `ENV_IMAGE` pinning, default bridge network (#4). The examples remove an
  old-layout container before starting on port 8080 (#5).
