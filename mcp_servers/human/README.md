# Human MCP server

One tool, `ask_human(question)`. It lets the agent ask the person who assigned
the task. An LLM plays that person (`mcp_servers/human_server/sim.py`):

- **What it knows:** the task prompt, a persona, and `knowledge.md`.
- **How it handles files:** it knows the names of its files in `files/` but
  never reads them. When asked, it shares one by uploading it to
  `/<HUMAN_UPLOAD_DIR>/` in the agent's filesystem through the environment's
  `/data/populate`, and the reply names the path the agent sees.
- **How it answers:** through a `reply(answer, share)` tool. Only the paths in
  `share` are uploaded; the answer's wording never triggers a share. A wrong
  path gets it another try (3 model calls at most), and a plain-text answer
  is taken as is, with nothing shared. A file it still can't share is named
  in the reply, telling the agent to proceed without it.
- **When it fails:** past the question cap, or when its model call fails
  after retries, the agent is told the human is unavailable and to proceed.
  A failed call doesn't count toward the cap.

It runs outside the agent's sandbox (the grid starts it on the host, see
`grid/grid/human.py`), so its provider key and its folder never enter it.

```
<HUMAN_DATA_ROOT>/          on the host
  knowledge.md              optional
  files/...                 optional
```

| Variable | Default | |
|---|---|---|
| `HUMAN_MODEL` | required | LiteLLM model string |
| `HUMAN_ENV_FILE` | | A .env with its provider key, read with dotenv; variables already set win |
| `HUMAN_EXTRA_ARGS` | `{}` | A JSON object passed to `litellm.acompletion`; it can't set `model`, `messages` or `tools` |
| `HUMAN_PERSONA` | accommodating, 1–3 sentences | |
| `HUMAN_TASK_PROMPT` | | The prompt the agent was given |
| `HUMAN_MAX_QUESTIONS` | no cap | A whole number ≥ 0; past it, the human is unavailable |
| `HUMAN_DATA_ROOT` | `human` | The human's folder |
| `HUMAN_AGENT_FILES` | | A file listing the agent's paths, one per line |
| `HUMAN_ENV_URL` | `http://localhost:8080` | The agent's environment, for uploads |
| `HUMAN_UPLOAD_DIR` | `from_user` | Where shared files land in the agent's filesystem |
| `HUMAN_PARENT_PID` | | Exit when this process is no longer the parent |
| `MCP_HOST`, `MCP_PORT`, `MCP_PATH` | `127.0.0.1`, `5000`, `/mcp` | HTTP transport |

The grid fills these in per run from `grid.json`'s `human` and
`world_descriptions.json` (see `grid/README.md`).

The settings are checked at startup. If one is invalid (no model, bad JSON,
a reserved key in `HUMAN_EXTRA_ARGS`, a cap that isn't a whole number), the
server exits with the reason in its log instead of failing every question.
The grid checks the same when it loads `grid.json`.

```bash
uv sync --locked && uv run pytest
```
