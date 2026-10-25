# Grid runner

Runs every task × model × repeat through the environment, agent and grading
runners, several at once, and records each run's state in SQLite. A stopped
grid resumes where it left off.

```bash
cd grid
uv sync --locked
uv run --locked python -m grid validate ../examples/grid/grid.json
uv run --locked python -m grid run ../examples/grid/grid.json
uv run --locked python -m grid status ../examples/grid/grid.json
uv run --locked python -m grid serve ../examples/grid/grid.json   # dashboard API on 127.0.0.1:7000
```

`agents/.env` and `grading/.env` need the provider keys for the models and the
judge, as for the examples. Each runner reads its own file. The grid itself
never reads keys. `validate` asks each runner whether its keys are present and
prints only the key names that are missing.

## Inputs

Tasks use the layout of the HuggingFace `mercor/apex-agents` dataset:

```
<inputs>/
  tasks_and_rubrics.json              [{task_id, task_name, world_id, prompt,
                                        rubric: [{verifier_id?, criteria}],
                                        task_input_files?}]
  world_descriptions.json             optional, [{world_id, ...}]
  world_files_zipped/<world_id>.zip   filesystem/ and optional .apps_data/
  task_files/<task_id>/               optional overlay: filesystem/, .apps_data/
```

Keep real inputs and outputs outside the repository. Point `inputs` and
`output_dir` in grid.json at them.

`models.json` lists the models being tested:

```json
[{"id": "model-a", "label": "Model A", "model": "openrouter/vendor/model-a",
  "context_window_tokens": 200000, "extra_args": {}, "max_concurrency": 2}]
```

- `id` names the run directories.
- `context_window_tokens` is required. The agent runs with
  `CONTEXT_WINDOW_FALLBACK_ENABLED=false`, so an unknown window fails instead of
  being assumed.
- `max_concurrency` (optional) caps the model's runs in flight, which keeps
  them under its rate limit.

`grid.json` (relative paths resolve against its folder):

| Key | Default | |
|---|---|---|
| `grid_id` | file name | Names the output folder |
| `inputs` | grid.json's folder | The inputs folder above |
| `models` | `models.json` | |
| `tasks` | `"all"` | Or a list of task ids |
| `repeats` | 1 | Runs per task × model |
| `max_parallel` | 2 | Environments at once, one port each |
| `max_attempts` | 3 | Per run, counted separately for the agent and for grading |
| `port_base` | 8100 | Ports are taken from here up, skipping busy ones |
| `judge` | required | `{"model": ..., "extra_args": ...}` |
| `agent_config` | `defaults/agent_config.json` | react_toolbelt_agent, 50 steps, 1 h wall clock |
| `system_prompt` | `defaults/system_prompt.md` | The prompt from the examples |
| `mcp_config` | `defaults/mcp_config.json` | All nine servers, each with its own venv Python over stdio |
| `eval_configs`, `scoring_config` | `defaults/` | `output_llm` criteria, plain average |
| `env_image` | built from the checked-out `environment/` and `mcp_servers/` | Pin an existing image |
| `output_dir` | `runs` | |
| `pass_score` | 1.0 | The score a run needs to count as a pass on the leaderboard |

Each rubric criterion becomes one `output_llm` verifier. The first one is the
primary objective.

## What a run does

1. Start the environment on its own port (`docker compose -p archipelago-<port>`,
   `--no-build`, the grid's image).
2. Populate the world, then the task overlay. Snapshot that state as
   `initial.zip`: grading compares the final state against it.
3. `POST /apps` with the MCP config.
4. Run the agent, with `FILE_LOGGING` writing its events to `agent_events.jsonl`.
5. Snapshot the final state (`final.zip`), then stop the environment.
6. Grade.

Errors are told from fails like this:

| Result | Outcome |
|---|---|
| Agent `completed` or `failed` | Graded and counted |
| Agent `error` with a wall-clock fault | Counted as a fail, score 0 |
| Any other agent `error`, `cancelled`, infra failure (`/apps` 503, Docker) | Agent rerun, with backoff |
| Grading not `completed` with a score (a verifier raised or errored, e.g. a judge API or auth failure, after the judge's own 5 retries) | Grading rerun on the same trajectory and snapshots; no score is recorded |
| `max_attempts` used | `gave_up` |

The image is built once per grid and tagged after the git trees it's built
from (`archipelago-environment:tree-<hash>` of `environment/` and
`mcp_servers/`), so commits that don't touch them reuse it. Uncommitted changes
there give a `-dirty` tag, rebuilt on each `run`. `manifest.json` records the
image and the commit.

## Output

```
<output_dir>/<grid_id>/
  grid.db            runs, attempts, events (SQLite)
  manifest.json      image, commit, tasks, models, judge
  <task_id>/<model_id>/r<repeat>/a<attempt>/
    initial.zip, final.zip, trajectory.json, grades.json
    agent_events.jsonl, agent.stdout.log, grading.<n>.stdout.log, env.log (on infra failure)
    initial_messages.json, agent_config.json, verifiers.json, ...
<output_dir>/.cache/  packed worlds, reused across runs and grids
```

Ctrl-C stops the grid and tears down its environments. `run` again resumes,
and interrupted runs start a new attempt.

## Dashboard API

`serve` runs the API and the scheduler in one process, bound to 127.0.0.1.
Start the grid from the UI or with `--start`. Everything under `/api`:

| Method | Path | |
|---|---|---|
| GET | `/grid` | Config: worlds, tasks, models, totals, image |
| GET | `/preflight[?refresh=1]` | The `validate` checks (cached) |
| POST | `/grid/start`, `/grid/pause`, `/grid/resume`, `/grid/cancel` | Pause lets runs in flight finish; cancel stops them and requeues them |
| POST, GET | `/smoke` | Start, or check, one run per model on the first task, in its own `<grid_id>-smoke` grid |
| GET | `/summary` | Counts, tokens, ETA, workers |
| GET | `/runs[?task=&model=&state=]` | One row per run |
| GET | `/runs/{id}` | Detail: attempts, per-call usage, final answer, per-criterion grades |
| GET | `/runs/{id}/events[?after=seq]` | The run's events |
| POST | `/runs/{id}/retry` | Rerun a finished run with a fresh attempt budget |
| GET | `/runs/{id}/trajectory`, `/grades`, `/files/{path}` | Raw files of the latest attempt |
| GET | `/worlds/{id}/files` | The world's files, as paths the agent sees |
| GET | `/leaderboard` | Per model: mean score ± 95% CI, pass rate, tokens, time, errors, fails, tool use; task × model matrix |
| GET | `/export.csv`, `/export.json` | Results |
| GET | `/stream[?run=&kinds=&after=]` | Server-sent events, one JSON event per message |

The stream starts at the newest event unless `after` is given, and a
reconnect resumes from `Last-Event-ID`. The fixtures in `grid/static/mock/`
show every response's shape.

Events come from three places, normalized into one shape (`seq, ts, run_id,
model_id, task_id, kind, server, tool, ref, args_preview, ok, duration_ms,
tokens, text, state, path`):

- **The agent's log.** `FILE_LOGGING` writes `agent_events.jsonl`, which the
  grid tails while the agent runs. Kinds: `step` (one model call, with tokens
  and latency), `tool_call` and `tool_result` (server and tool split from the
  `{server}_{tool}` name, paired by `ref` and timed), `reasoning`, `response`,
  `toolbelt`, `compaction`, `final_answer`.
- **Grading's output.** The judge's start and result lines become
  `grading_progress`, and the final score becomes `grade`.
- **The grid itself.** `run_state` on every state change, `infra` for errors
  and retries, and `grid_state` for the grid as a whole.

Previews are clipped (300 characters for arguments and results, 2,000 for
text), and image results show as `[image]`. The full content stays in the
attempt's files.

### Leaderboard rules

Only scored runs count. A failed agent or a wall-clock timeout counts against
the model. Runs that gave up after `max_attempts` infra or grading errors are
left out of the score and reported as `gave_up`, and `errors` counts the extra
attempts, so a flaky provider is visible without lowering the score. A run
passes at `pass_score`.
