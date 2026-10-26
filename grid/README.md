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
| `env_image` | built from the current commit | Pin an existing image |
| `output_dir` | `runs` | |

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

The image is built once per grid and tagged with the commit
(`archipelago-environment:<sha>`, or `<sha>-dirty` with uncommitted changes
under `environment/` or `mcp_servers/`). `manifest.json` records it.

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
