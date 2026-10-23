# Upstream internals: what is missing or dormant in the public tree

Upstream publishes this repo from Mercor's internal `Mercor-io/studio` monorepo
(an "island" called `oss_archipelago`). The publish step filters out some
modules, and the code that remains still has hooks for internal services. This
note records both, so we can revive or replace pieces later. It reflects the
upstream sync at `2c84e52`.

## 1. Referenced but not shipped

These are named in code or config, but the module or package is not in the tree.
The code around them is written to tolerate their absence (soft imports,
`try/except ImportError`, or simply never registering the eval).

### Grading verifiers

| Name | Where it is referenced | Notes |
|---|---|---|
| `agentic_verifier` | `grading/runner/main.py` (soft-imports `agentic_verifier.live_apps`), `registry.py` (`_AV_*` constants, `_agentic_verifier_output_fields`, toolkits such as `app_state_audit`, `cad_review`), `grading/README.md`, `skip_rules.py` | A per-criterion agent judge with tools (`read_document`, `digest_file`, `query_database`, `read_database_changes`, `read_transcript`). Its `EvalIds` member and registry entry were also filtered out. The comment in `main.py` says: "island deliveries ship without the agentic verifier". |
| `vera_mh` | `grading/runner/main.py` (`vera_mh.repair_scope`), `skip_rules.py` | VERA "repair scope" expansion. |
| `cadbench_geometry` | `grading/pyproject.toml` (old comments) | CAD geometry IoU verifier. Its dependencies were removed with the CAD cleanup. |
| `cogmanip_judge` | `grading/pyproject.toml` | |
| `iheval_programmatic` | `grading/pyproject.toml` (basedpyright ignore for `vendor/`) | |
| `monitorbench` | `grading/pyproject.toml`, `skip_rules.py`, `agents/.../harness_descriptor_registry.py` | Its pinned deps (`pylatexenc`, `sympy`) are still in `grading/pyproject.toml`. |
| HarmBench copyright grader | `grading/pyproject.toml` (`datasketch`) | |
| `bua_verifier` | `grading/pyproject.toml` (`playwright`), `grading_entrypoint.py`, `utils/metrics.py`, `utils/tool_paths.py` | Drives a live headless browser. |
| `db_code_verifier` | `grading/runner/evals/db_code_verifier/README.md` | Present now, but its README says Studio's OSS `filters.py` excluded it from earlier public commits. Shows how the filter works. |

### Agent harnesses

| Name | Where it is referenced |
|---|---|
| `lighthouse_code_agent` | `agents/runner/agents/harness_descriptor_registry.py`, `agents/runner/save/main.py`, `agents/linting/concurrency_checks.py`, `grading/runner/evals/lighthouse_result/main.py` |
| MonitorBench harness | `agents/runner/agents/harness_descriptor_registry.py`, `agents/runner/utils/sandbox_metric_names.py` |

### MCP servers (app backends)

Grading verifiers exist for these apps, but their MCP servers are not in
`mcp_servers/`: BambooHR, Greenhouse, Workday, SAP, Tableau, Looker, Jenkins,
Eightfold, OpenEMR, QuickBooks, Xero, TaxJar and an `adp_*` server. The shared
MCP package also mentions `factset`, `zoho`, `powerbi` and a `studio_server`.
See backlog item 10 in `CLAUDE.md`.

### Private Python packages

| Package | Used by | Fallback |
|---|---|---|
| `mercor_cas_client` | `agents/runner/utils/cas_direct_emit.py`, `cas_ledger_emit.py`; `grading/runner/utils/cas_ledger_emit.py` | Soft import; cost-ledger emission is skipped. |
| `modal_helpers` | `grading/runner/evals/utils/s3.py` | Falls back to a default `aioboto3.Session()`. |
| `rls_pipeline_config` | Comment in `grading/runner/evals/registry.py` (`agentic_guidance.py`) | Not imported. |
| Studio `packages/islands/...` | Comments in `agents/.../stirrup_agent/main.py`, `agents/runner/utils/app_identity.py`, `environment/runner/grade.py`, `grading/runner/grading_entrypoint.py` | Some code is duplicated from there; the rest is only described. |

### Scripts

- The code server's install task (`mise.toml [tasks.install]`, configured through `arco.toml`), which builds the sandbox package mirror at `/usr/local/lib/code_execution`. `environment/Dockerfile` now does that itself.
- `scripts/sync_eval_definitions.sh` (copies the eval registry into Studio's
  server registry; mentioned in `registry.py` and `grading/README.md`).

## 2. Shipped but dormant locally

These services are wired in, but nothing runs while `ENV=local` and their
settings are unset. Setting names come from each runner's
`runner/utils/settings.py`.

| Service | Runners | Settings / env vars | Code |
|---|---|---|---|
| RL Studio API | agents, grading | `RL_STUDIO_API`, `RL_STUDIO_API_KEY` | `runner/utils/studio_http.py` |
| Result webhooks | agents, grading | `SAVE_WEBHOOK_URL`, `SAVE_WEBHOOK_API_KEY`, `SCORE_WEBHOOK_URL` | `agents/runner/save/webhook.py` |
| Redis log streams | agents, grading | `REDIS_LOGGING`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_USER`, `REDIS_PASSWORD`, `REDIS_STREAM_PREFIX` | `runner/utils/redis.py`, `runner/utils/logging/redis_logger.py` |
| Datadog logs and metrics | agents, grading, environment | `DATADOG_LOGGING`, `DATADOG_API_KEY`, `DATADOG_APP_KEY` | `logging/datadog_logger.py`, `utils/metrics.py` |
| S3 logs and snapshots | agents, environment, grading | `S3_LOGGING`, `S3_SNAPSHOTS_BUCKET`, `S3_SNAPSHOTS_PREFIX`, `S3_DEFAULT_REGION` | `agents/runner/utils/logging/s3_log_*.py`, `environment/runner/utils/s3*.py`, `grading/runner/utils/s3_transfer.py` |
| LLM gateway | agents, grading | `LLM_GATEWAY_API_BASE`, `LLM_GATEWAY_API_KEY` | `runner/utils/llm.py` |
| Cost ledger (CAS) | agents, grading | needs `mercor_cas_client` | `runner/utils/cas_ledger_emit.py`, `budget_meter.py`, `budget_hydration.py` |
| Request signing | agents, environment | `API_SIGNING_PRIVATE_KEY`, `API_SIGNING_PUBLIC_KEY` | `runner/utils/signing.py` |
| Mercor document cache | grading | `MERCOR_DOCUMENT_API`, `MERCOR_DOCUMENT_API_KEY` | `grading/runner/utils/file_extraction/methods/mercor_document_cache.py` |
| Reducto (third-party parsing) | grading | `REDUCTO_API_KEY` | `grading/runner/utils/file_extraction/methods/reducto*` |
| Vertex prompt cache | grading | `GRADING_VERTEX_CACHE_PROJECT`, `GRADING_VERTEX_CACHE_LOCATION`, `GRADING_PROMPT_CACHE_*` | `grading/runner/utils/llm.py` |
| Modal (sandboxes, Lighthouse) | agents, grading | `LIGHTHOUSE_MODAL_APP_NAME` (agents), `MODAL_*` | `grading/runner/evals/lighthouse_result/`, `grading/runner/evals/utils/s3.py` (via `modal_helpers`) |
| Inline grader sidecar | environment | dispatch from Studio | `environment/runner/grader_sidecar_server.py`, `grade.py`, `grade_jobs.py`, `SECURITY-inline-grading.md` |
| Harbor world source | agents | | `agents/runner/harbor_world_source.py`, `harbor_answer_isolation.py` |

## 3. Removed in this fork

These were in the public tree and we removed them. Restore one with
`git checkout <commit>^ -- <path>`, or take it from `upstream/main`.

| What | Commit | Why |
|---|---|---|
| `mcp_servers/fmp`, `mcp_servers/edgar_sec` and their Dockerfile and example config entries | `64f7b91` | Need external market-data APIs or keys; not local. |
| CAD verifiers: `kicad`, `freecad`, `cad_llm_judge`, `agentic_verifier/cad.py` and their dependencies | `2983527` | Need CAD toolchains; outside this fork's scope. |
