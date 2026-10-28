# Simulated human example

A grid where the models can ask the person who gave them the task
(`ask_human`, see "Simulated human" in `grid/README.md`).

| Task | What it tests |
|---|---|
| `pick_venue` | Asking. The venue depends on the headcount, city and catering, which only the human knows. The meal order needs `guest_list.csv`, which only the human has, so they have to share it. Answer: Harbor Loft; 9 standard, 4 vegetarian, 2 vegan. |
| `cheapest_seat` | Not over-asking. `venues.csv` is enough. Answer: Pine Hall, about $73.33. |

The world's sources are in `world_src/offsite/`:
- `filesystem/` is what the agent sees.
- `.apps_data/human/` is the human's: `knowledge.md` and `files/guest_list.csv`.
  The agent can't reach it.

After editing the sources, rebuild the zip:

```bash
cd world_src/offsite && rm -f ../../world_files_zipped/offsite.zip \
  && zip -qrX ../../world_files_zipped/offsite.zip filesystem .apps_data
```

Run it:

```bash
cd ../../grid
uv run --locked python -m grid validate ../examples/hil/grid.json
uv run --locked python -m grid run ../examples/hil/grid.json
```

Every role uses a free OpenRouter model, and the tasks are text only (CSV and
Markdown, answered in the final answer), so any text model can run them.

| Role | Model |
|---|---|
| Tested | Laguna S 2.1, Nemotron 3 Ultra |
| Judge | Nemotron 3.5 Super VL |
| Human | Nemotron 3.5 Super VL |

The human shares the judge's model but not its role, and isn't one of the
models being tested.
- **Retries:** free models rate-limit, so the human gets
  `"extra_args": {"num_retries": 8}`. A human call that still fails reaches the
  agent as a tool error and counts against the model. If you see `429` in a
  run's `ask_human` results, rerun it.
- **Keys:** the human's provider key comes from `agents/.env`.
