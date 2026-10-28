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
uv run --locked python -m grid serve ../examples/grid/grid.json   # dashboard API on 127.0.0.1:7070 (not 7000: macOS AirPlay listens there)
```

`agents/.env` and `grading/.env` need the provider keys for the models and the
judge, as for the examples. Each runner reads its own file. The grid itself
reads keys only for the simulated human (`human`, below), from `agents/.env`. `validate` asks each runner whether its keys are present and
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
| `agent_grace_sec` | 600 | Seconds past the agent's own wall clock before the grid kills it (scored as a timeout) |
| `grading_timeout_sec` | 3600 | Seconds before the grid kills a grading attempt and reruns grading |
| `human` | off | `{"model": ..., "extra_args": ..., "max_questions": null}`: a simulated human the models can ask (below) |

Each rubric criterion becomes one `output_llm` verifier. The first one is the
primary objective.

### Simulated human

With `human` set, every run gets one more MCP server, `human_server`, with one
tool: `ask_human(question)`. The model being tested uses it to ask the person
who assigned the task. An LLM (`human.model`, the same one for every model in
the grid) plays that person. It knows:

- the task prompt
- its persona: `world_descriptions.json` → `{"world_id": ..., "human":
  {"persona": "..."}}`. Without one, it's accommodating and answers in one to
  three sentences.
- `.apps_data/human/knowledge.md` in the world zip or task overlay: what it
  knows from memory
- the names of the files in `.apps_data/human/files/`. It doesn't read them.
  Like a person, it answers from memory, points to a path in the agent's files,
  or shares one of its own files. A shared file is copied to `/from_user/<name>`
  in the agent's filesystem, and the reply ends with `Shared: /from_user/<name>`.

The model being tested can't read `.apps_data/human/`: the code sandbox blocks
`/.apps_data`, and the filesystem server only sees `/filesystem`. Shared files
are new files in the final state, so rubrics that check outputs should expect
them under `/from_user/`.

- The human keeps the conversation for the run, so follow-ups make sense.
- Past `max_questions`, it replies that it isn't available.
- Its model calls use the provider keys from `agents/.env` (and the
  environment). The grid passes them into that one server's environment for
  the run. They're never written to disk, and the code sandbox's env scrub
  keeps them from model-run code.
- `validate` checks that the key is there. The human's token use is logged to
  the environment's stderr only, not counted on the leaderboard.

## What a run does

1. Start the environment container on its own port, through the Docker Engine
   API (the `docker` Python package): the grid's image, named
   `archipelago-grid-<pid>-<port>`, bound to 127.0.0.1 only. Only the image
   build goes through the CLI (`docker compose build`), since it needs
   BuildKit.
2. Populate the world, then the task overlay. Snapshot that state as
   `initial.zip`: grading compares the final state against it.
3. `POST /apps` with the MCP config (with `human`, the human server's env filled in for this task).
4. Run the agent, with `FILE_LOGGING` writing its events to `agent_events.jsonl`.
5. Snapshot the final state (`final.zip`), then stop the environment.
6. Grade.

Errors are told from fails like this:

| Result | Outcome |
|---|---|
| Agent `completed` or `failed` | Graded and counted |
| Agent `error` with a wall-clock fault | Counted as a fail, score 0 |
| Any other agent `error`, `cancelled`, infra failure (`/apps` 503, Docker, a dropped sandbox connection, a full disk) or an unexpected worker error | Agent rerun, with backoff |
| Grading not `completed` with a score (a verifier raised or errored, e.g. a judge API or auth failure, after the judge's own 5 retries) | Grading rerun on the same trajectory and snapshots; no score is recorded |
| Grading killed at `grading_timeout_sec` | Grading rerun |
| `max_attempts` used | `gave_up` |

The image is built once per grid and tagged after the git trees it's built
from (`archipelago-environment:tree-<hash>` of `environment/` and
`mcp_servers/`), so commits that don't touch them reuse it. Uncommitted changes
there give a `-dirty` tag, rebuilt on each `run`; the image a rebuild replaces
is removed. `manifest.json` records the image and the commit.

### Container cleanup

Every container a grid starts is removed, however the grid ends:
- **A run that finishes, fails or is stopped** removes its own container, even
  when Stop or Ctrl+C lands mid-teardown.
- **A grid that stops** then removes any of its containers that are left.
- **A grid process that dies** (a crash, `kill -9`, a closed terminal) is
  covered by a watchdog. It's a small detached Python process started with the
  first environment. It removes that process's containers within about 5 seconds of
  the process exiting.
- **On start**, a grid removes containers left by grid processes that are no
  longer running. `python -m grid clean` does the same on demand.

Containers carry the pid of the process that started them (the
`archipelago.grid.owner` label), so a grid never removes another live grid's
containers.

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

## Dashboard

`serve` runs the dashboard, its API and the scheduler in one process, bound to
127.0.0.1. Open `http://127.0.0.1:7070/` and start the grid from Setup (or
pass `--start`). The screens: Setup (the grid, preflight, smoke test), Mission
Control (progress, the task × model board, activity, workers, pause and stop),
Live Map (tool calls as they happen; focus on one run with its world's files;
replay a finished run), run detail, Leaderboard and Infra. The UI is plain
HTML, CSS and JavaScript in `grid/static/`; see its README.

On a remote machine, forward the port (`ssh -L 7070:127.0.0.1:7070 host`)
rather than binding to a public address: the API has no authentication.

### API

Everything under `/api`:

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
| POST | `/runs/{id}/retry` | Rerun a finished run with a fresh attempt budget (starts the grid if it's idle) |
| GET | `/runs/{id}/trajectory`, `/grades`, `/files/{path}` | Raw files of the latest attempt |
| GET | `/worlds/{id}/files` | The world's files, as paths the agent sees |
| GET | `/leaderboard` | Per model: mean score ± 95% CI, pass rate, tokens, time, errors, fails, tool use; task × model matrix |
| GET | `/export.csv`, `/export.json` | Results |
| GET | `/stream[?run=&kinds=&after=]` | Server-sent events, one JSON event per message |

A new stream starts with the latest 200 events unless `after` is given, and
a reconnect resumes from `Last-Event-ID`. The fixtures in `grid/static/mock/`
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

How every dashboard number is calculated and aggregated, with its caveats:
[`docs/grid-analytics.md`](../docs/grid-analytics.md).
