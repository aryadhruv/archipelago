# Examples

Quick commands for each example. Run them from the repository root. All of
them need Docker running.

## Setup (once)

```bash
# Provider keys: each runner reads its own .env
echo 'OPENROUTER_API_KEY=sk-or-...' >> agents/.env
echo 'OPENROUTER_API_KEY=sk-or-...' >> grading/.env

(cd agents && uv sync --locked)
(cd grading && uv sync --locked)
(cd grid && uv sync --locked)
```

There are three types of grid example. Each uses free OpenRouter models in
every role (tested models and judge, plus the human for `hil`), chosen so that
every model can do the task:

| Type | Grid file | Task | Tested models |
|---|---|---|---|
| **Text** | [`grid/grid_text.json`](grid/) | Find the gorilla among text notes | Laguna S 2.1, Nemotron 3 Ultra (text only) |
| **Images** | [`grid/grid_images.json`](grid/) | Find the gorilla among images | Dots 3 Note, Nemotron 3.5 Super VL (read images) |
| **Human-in-the-loop** | [`hil/grid.json`](hil/) | Plan an offsite by asking the user (`ask_human`, a simulated human), plus a task that needs no questions | Laguna S 2.1, Nemotron 3 Ultra |

The judge grades the final answer, which is text: Nemotron 3.5 Super VL for
the text and HIL grids, Nemotron 3 Ultra for the images grid (where Super VL is
one of the models being tested).

Two single-task examples run one model end to end (populate → MCP servers →
agent → snapshot → grading), without the grid:
- [`simple_task`](simple_task/): the text task, on free models.
- [`hugging_face_task`](hugging_face_task/): one task from the HF
  `mercor/apex-agents` dataset with all MCP servers, on upstream's Vertex
  config.

## One task

```bash
cd examples/simple_task && bash run.sh
```

Writes `trajectory.json`, `final_snapshot.zip` and `grades.json` next to the
script. Expect a score of 1.0.

```bash
cd examples/hugging_face_task && bash run.sh       # the default task
cd examples/hugging_face_task && bash run.sh 42    # by index, or pass a task id
```

This one downloads the task from Hugging Face and uses Vertex models
(`orchestrator_config.json`, `grading_settings.json`). Point those at an
OpenRouter model to use `OPENROUTER_API_KEY` instead.

## A grid

Serve it, open the dashboard, and press **Start grid**:

```bash
cd grid
uv run --locked python -m grid serve ../examples/grid/grid_text.json     # text;   open http://127.0.0.1:7070/
uv run --locked python -m grid serve ../examples/grid/grid_images.json   # images
uv run --locked python -m grid serve ../examples/hil/grid.json           # human-in-the-loop
```

The dashboard does everything else:
- **Setup:** runs the preflight checks (inputs, Docker, keys), with a smoke test and **Start grid**.
- **Mission Control:** progress, the task × model board, pause and stop.
- **Live Map:** tool calls as they happen.
- **Run Detail:** each step of a run. `ask_human` calls show as "Asked the user".
- **Leaderboard** and **Infra**.

Starting a grid also removes containers left by earlier grids. A stopped grid
resumes where it left off.

- **Start on launch:** add `--start`.
- **Second dashboard:** add `--port 7071`. Each example grid has its own
  `port_base` (text 8100, human-in-the-loop 8200, images 8300), so they can
  run at the same time.
- **Results:** under `examples/<example>/runs/<grid_id>/`.
- **First start:** builds the environment image, which takes about 10 minutes.

Without a browser, the CLI does the same: `validate`, `run`, `status` and
`clean` (see `grid/README.md`).

## Demo

The same dashboard on canned data, with a scripted simulator. It needs no
Docker and makes no model calls. Start any `serve` above and open:

```
http://127.0.0.1:7070/demo/
```

Press **Start grid** to set the simulated runs going. Run `r-0001` (Mission
Control → its segment, or `#/run/r-0001`) shows a full run, including two
questions to the simulated human. `#/map?replay=r-0001` replays it on the
Live Map.

## More

- `grid/README.md`: grid inputs, settings, outputs and the simulated human.
- `grid/grid/static/README.md`: the dashboard and the demo.
- Each example's own README.
