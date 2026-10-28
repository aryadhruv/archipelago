# Human MCP server

One tool, `ask_human(question)`. It lets the agent ask the person who assigned
the task. An LLM plays that person (`mcp_servers/human_server/sim.py`):

- **What it knows:** the task prompt, a persona, and `knowledge.md`.
- **How it handles files:** it knows the names of its files in `files/` but
  never reads them. When asked, it shares one by copying it to
  `<HUMAN_FS_ROOT>/<HUMAN_UPLOAD_DIR>/`, and the reply names the path the agent
  sees.

```
/.apps_data/human/          HUMAN_DATA_ROOT (the agent can't reach /.apps_data)
  knowledge.md              optional
  files/...                 optional
```

| Variable | Default | |
|---|---|---|
| `HUMAN_MODEL` | required | LiteLLM model string; its provider key must be in the env |
| `HUMAN_EXTRA_ARGS` | `{}` | JSON, passed to `litellm.acompletion` |
| `HUMAN_PERSONA` | accommodating, 1–3 sentences | |
| `HUMAN_TASK_PROMPT` | | The prompt the agent was given |
| `HUMAN_MAX_QUESTIONS` | no cap | Past it, the human is unavailable |
| `HUMAN_DATA_ROOT` | `/.apps_data/human` | |
| `HUMAN_FS_ROOT` | `/filesystem` | The agent's filesystem root |
| `HUMAN_UPLOAD_DIR` | `from_user` | Where shared files land, under `HUMAN_FS_ROOT` |

The grid fills these in per run from `grid.json`'s `human` and
`world_descriptions.json` (see `grid/README.md`).

```bash
uv sync --locked && uv run pytest
```
