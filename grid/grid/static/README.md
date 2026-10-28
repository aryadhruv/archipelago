# Archipelago dashboard

The grid runner's dashboard. A grid runs every task on every model, several
times. Each run starts a Docker sandbox, loads a world of files, lets an agent
work through MCP tool servers, and then grades the result against the task's
rubric.

`python -m grid serve <grid.json>` serves this folder at `/` together with the
API, and again at `/demo/`, where it runs on the fixtures in `mock/` and a
scripted simulator (`js/mock_stream.js`). The demo is for showing the dashboard
while a real grid runs, and for working on the UI without Docker or model
calls. The mode follows the path only: if the API fails at `/`, the page shows
the error, never the demo data. The fixtures double as the API contract: `grid/tests/
test_contract.py` checks that the API serves every field they have.

Plain HTML, CSS and ES modules. No libraries, no CDN, no build step.

## Open it

```bash
cd grid && uv run --locked python -m grid serve path/to/grid.json
# real data: http://127.0.0.1:7070/
# demo data: http://127.0.0.1:7070/demo/
```

URL option:

- `?api=http://host:port`: call an API on another origin. The API doesn't
  send CORS headers, so this works only through a proxy that adds them.

## Screens

| Route | Screen | What it shows |
|---|---|---|
| `#/setup` | Setup / Overview (default) | Grid size (tasks × models × repeats = runs), image tag, git SHA, judge, simulated human (when the grid has one); cards for tasks (expandable prompt, criteria count, servers), models (provider, context window, concurrency, color) and worlds (size, file count, apps); the preflight checklist; **Smoke test** and **Start grid**. |
| `#/live` | Mission Control | KPI strip (scored, running, queued, retrying, gave up, progress, elapsed, ETA, tokens, retries); the task × model heatmap (one segment per repeat; running segments pulse and show a phase icon; scored segments use the score ramp; retrying and gave-up segments use the warning and critical status styles with icons); the activity ticker; the worker pool. Click any segment to open the run. |
| `#/map` | Live Map | Agents (left) and MCP servers plus the toolbelt (right); a **Human** hub when the grid has a simulated human. A packet travels agent → server on each `tool_call` and comes back green or red on `tool_result`. Edge width grows with cumulative calls; hubs show calls/min and an in-flight ring. Hover edges, hubs and agents for details. Modes: **Live** (all models or one; click an agent to filter), **Focus a run** (one live run plus its world file tree, which lights up the files it touches), **Replay** (a finished run at 1×, 4× or 16×). Deep links: `#/map?model=model_a`, `#/map?run=r-0057`, `#/map?replay=r-0001`. |
| `#/run/<id>` | Run Detail | Header (model, task, world, attempt, state, score, duration, tokens), collapsible step timeline (reasoning, tool call with args, result with ok/error and ms; an `ask_human` call shows as **Asked the user** with the question, the answer and a chip for each shared file), tokens per model call, final answer, per-criterion grading with rationale, attempt history with classification reasons, **Replay on map**. |
| `#/leaderboard` | Leaderboard | Ranked table (mean ± 95% CI, pass rate, runs, avg tokens, avg time, errors, fails), task × model score matrix, score vs tokens and score vs duration scatters, per-model tool-usage profile, CSV and JSON export (generated in the browser). |
| `#/infra` | Infra / Logs | Filterable table of infra events, retries, give-ups and their classifications; the worker/port list. |

The top bar has the grid ID, a live indicator (Not started, Live, Paused,
Finished), a "Demo data" badge under `/demo/`, and a dark/light toggle (dark by
default; the choice is kept in `localStorage`).

### Mock behaviour

- Before **Start grid**, the screens show the fixture snapshot and the
  indicator says "Not started". `getSummary()` reports `state: "idle"` until
  then, even though `summary.json` itself is a mid-run snapshot.
- After Start, the simulator resumes the runs that were in flight in the
  snapshot, keeps `max_parallel` runs going, and works through the queue (about
  30–45 seconds per run). Tool results take 200–3000 ms, about 7% of them fail,
  some runs are retried and a few give up after three attempts.
- Runs `r-0001`, `r-0022` and `r-0011` have full detail and event fixtures. Other
  runs show what the simulator produced in this session, with a note saying so.
- The fixture grid has a simulated human. `organize_project_folder` and
  `draft_offsite_agenda` can ask it, and the simulator does so in about 12% of
  those tasks' steps, with canned answers (some share a file). Run `r-0001`
  asks it twice.
- **Smoke test** waits about 3 seconds and returns a canned result for every
  model.

## Data contract

Each fixture is the exact response body of one endpoint. `js/api.js` is the
only module that knows where data comes from; switching to the backend changes
nothing else.

| Function in `js/api.js` | Endpoint | Fixture |
|---|---|---|
| `getGrid()` | `GET /api/grid` | `mock/grid.json` |
| `getPreflight()` | `GET /api/preflight` | `mock/preflight.json` |
| `getSummary()` | `GET /api/summary` | `mock/summary.json` |
| `getRuns()` | `GET /api/runs` | `mock/runs.json` |
| `getRun(id)` | `GET /api/runs/{id}` | `mock/run_<id>.json` |
| `getRunEvents(id)` | `GET /api/runs/{id}/events` | `mock/events_<id>.json` |
| `getLeaderboard()` | `GET /api/leaderboard` | `mock/leaderboard.json` |
| `getWorldFiles(id)` | `GET /api/worlds/{id}/files` | `mock/world_<world_id>.json` |
| `subscribe(onEvent)` | `GET /api/stream` (SSE, one event per `data:` line) | driven by `js/mock_stream.js` |
| `startGrid()` / `pauseGrid()` / `resumeGrid()` | `POST /api/grid/start`, `/pause`, `/resume` | simulator |
| `runSmokeTest(onProgress)` | `POST /api/smoke`, then `GET /api/smoke` every 3 s until done | canned result |
| `cancelGrid()` | `POST /api/grid/cancel` | simulator (pauses) |
| `retryRun(id)` | `POST /api/runs/{id}/retry` | no-op |

`grid/README.md` lists every endpoint.

### Shapes

```text
grid        {grid_id, created_at, image_tag, git_sha, repeats, max_parallel, max_attempts, judge_model, human_model,
             worlds:[{world_id, name, size_bytes, file_count, apps:[server]}],
             tasks:[{task_id, task_name, world_id, prompt, criteria_count, mcp_servers:[server]}],
             models:[{id, label, model, provider, context_window_tokens, max_concurrency, color}],
             totals:{tasks, models, repeats, runs}}
preflight   {ok, checks:[{id, label, status:"pass"|"fail"|"warn", detail}]}
            (the provider-key check only reports presence, never the value)
summary     {state:"running"|"paused"|"idle"|"done",
             counts:{queued, running, retrying, scored, gave_up},
             started_at, elapsed_s, eta_s, tokens:{prompt, completion}, attempts, retries,
             workers:[{port, run_id|null, state, health:"ok"|"degraded"|"down"}]}
runs        [{run_id, task_id, model_id, repeat, attempt, state, phase, score|null,
              agent_status|null, classification|null, started_at, finished_at|null,
              duration_s, tokens, steps, tool_calls}]
run         run row + {attempts:[{attempt, state, reason, started_at, finished_at}],
                       usage:{call_log:[{prompt_tokens, completion_tokens, reasoning_tokens}]},
                       final_answer,
                       grades:{final_score, grading_run_status,
                               criteria:[{verifier_id, criteria, score, passed, rationale}]}}
events      [event]
leaderboard {models:[{model_id, mean_score|null, ci95, pass_rate|null, runs, avg_tokens, avg_duration_s,
                      errors, gave_up, fails, tool_profile:{server: calls}}],
             matrix:[{task_id, model_id, mean_score|null, n}],
             pass_score, rules}
smoke       {state:"idle"|"running"|"done", ok|null, task_id, runs:[run row], error}
world files {world_id, files:[{path, size_bytes}]}
```

Enumerations:

- `state`: `queued | env_starting | populating | apps_starting | agent_running | snapshotting | grading | scored | retry | gave_up`
- `phase` (coarse grouping of `state`): `queued | setup | agent | grading | done`
- `agent_status`: `pending | running | completed | cancelled | failed | error` (as in `agents/runner/agents/models.py`)
- `classification`: `scored | rerun_agent | rerun_grading | fail_timeout | null`
- leaderboard (the API decides; the page shows its `rules`): only scored runs
  count; a failed agent or a wall-clock timeout counts against the model; runs
  that gave up on infra errors are left out of the score and counted in
  `gave_up`; `errors` counts extra attempts; pass means score ≥ `pass_score`.

### Event schema (`/api/runs/{id}/events` and `/api/stream`)

```text
{seq, ts, run_id, model_id, task_id, kind, server|null, tool|null, ref|null,
 args_preview|null, ok|null, duration_ms|null, tokens|null, text|null, state|null}
```

| `kind` | Meaning | Fields used |
|---|---|---|
| `run_state` | the run moved to a new `state` | `state`, `text` |
| `step` | one model call | `tokens`, `text` |
| `tool_call` | the agent called a tool | `server`, `tool`, `ref`, `args_preview` |
| `tool_result` | that call returned | `server`, `tool`, `ref` (same as the call), `ok`, `duration_ms`, `text` |
| `reasoning` | reasoning preview | `text` |
| `response` | assistant text | `text` |
| `toolbelt` | tools added to or removed from the toolbelt | `text` |
| `compaction` | context was compacted | `text`, `tokens` |
| `final_answer` | the agent submitted its answer | `text` |
| `grading_progress` | one criterion was graded | `ok`, `text` |
| `grade` | final score | `ok`, `text` |
| `infra` | sandbox, port or provider event (`run_id` may be null) | `ok`, `text` |

- `server` is an MCP server name from the gateway config: `filesystem_server`,
  `docs_server`, `sheets_server`, `slides_server`, `pdf_server`, `mail_server`,
  `calendar_server`, `chat_server`, `code_execution_server`, or the pseudo-server
  `toolbelt`.
- MCP tool names are `{server}_{tool}`, for example `filesystem_server_read_text_file`.
- The agent's meta-tools (`todo_write`, `toolbelt_list_tools`, `toolbelt_inspect_tool`,
  `toolbelt_add_tool`, `toolbelt_remove_tool`, `final_answer`) use `server: "toolbelt"`.
- `args_preview` is the call's JSON arguments, cut to about 160 characters. The map
  reads `path` or `file_path` from it to light up files.

## Files

```text
index.html            shell: top bar, <main id="view">
css/app.css           design tokens (dark default, light theme), layout, components
js/app.js             hash router, theme toggle, live indicator
js/api.js             data layer (mock fixtures or real API)
js/mock_stream.js     simulator for the demo
js/store.js           shared client state: grid, summary, runs, event ring buffer
js/util.js            DOM helpers, formatting, colors, icons, tooltip
js/charts.js          SVG charts: stacked bars, scatter, CI mark
js/views/*.js         one module per screen
mock/*.json           fixtures, one per endpoint response
```

## Design notes

- Model colors are the first six slots of a categorical palette checked for
  color-vision deficiency on the adjacent pairs, in both themes. Each model keeps
  its color everywhere. The dark theme uses darker steps of the same hues.
- Scores use a single-hue blue ramp. In the dark theme the ramp is flipped so low
  scores sink toward the background.
- Status colors (green, amber, red) are only used for ok/warning/error meaning and
  always come with an icon or label.
- Charts with more than three models (the scatters) label every point directly, so
  color is never the only cue.
- `prefers-reduced-motion` turns off pulses and particles; the map then flashes the
  edge instead of moving a packet.
