# Grid analytics: how each number is calculated

This document follows every number the grid dashboard and its exports show
back to the data it comes from, and says how that data is aggregated. Use it
to read results correctly. When we change a method later, update the matching
section here too.

The code lives in `grid/grid/views.py` (`summary_view`, `leaderboard_view`,
`run_view`, `export_csv`). The UI shows these values as the API returns them.
Only the few calculations listed under [In the browser](#in-the-browser) are
done in the page.

## 1. From criteria to a run's score

A **run** is one task × one model × one repeat. Each run is graded once,
against the task's rubric.

1. **Criterion score.** Each rubric criterion becomes one `output_llm`
   verifier (`verifiers_for` in `grid/grid/inputs.py`). The LLM judge
   (`judge.model` in grid.json) reads the final answer and the files the run
   changed, then scores the criterion:
   - **1.0** if the criterion is met.
   - **0.0** if it isn't.
   - **-1.0** only when the verifier has a negative criterion and the run
     violated it. The grid's rubric mapping doesn't set negative criteria,
     so today scores are 0 or 1.
2. **Run score.** The `template` scoring method (`grid/grid/defaults/
   scoring_config.json`) takes the **plain average of the criterion
   scores**. A task with 4 criteria and 3 met scores 0.75. If any verifier
   errors, grading is rerun and no score is recorded
   (`grading/runner/scoring_methods/template/main.py`).
3. **Pass.** A run passes when `score >= pass_score` (grid.json, default
   **1.0**, meaning every criterion is met).

### Which runs get a score

`grid/grid/classify.py` decides, and the grid's README has the full table:

| Agent result | Counts as |
|---|---|
| `completed` or `failed` | Graded; the score counts |
| Killed at the wall clock (`fault.budget = wall_clock`) | Scored **0**, a fail |
| Other `error`, `cancelled`, sandbox or host failure | Not scored; the agent reruns |
| Grading didn't finish with a score, or was killed at `grading_timeout_sec` | Not scored; grading reruns on the same output |
| `max_attempts` used up on either of the two lines above | `gave_up`: **left out of every score** |

A run's final state is `scored` (it has a score) or `gave_up` (it has none).
Every aggregate below uses **only `scored` runs**, unless it says otherwise.

## 2. Leaderboard (`GET /api/leaderboard`)

There is one row per model. *S* is the set of that model's `scored` runs,
across all tasks and repeats.

| Column | Calculation | Notes |
|---|---|---|
| **Mean score** | mean of run scores over *S* | Every run weighs the same, so a task with more repeats weighs more. All tasks have the same number of repeats, so in practice this equals the mean of per-task means, as long as no runs are missing. |
| **± 95% CI** | `1.96 × stdev(scores) / √n`, with n = \|S\| | A normal approximation over **runs**. Repeats of one task are treated as independent samples, so more repeats make the interval narrower even though the set of tasks hasn't changed. Read it as "how stable the scores are across runs", not as uncertainty over tasks. Shown as `–` when n < 2. |
| **Pass rate** | share of runs in *S* with `score >= pass_score` | |
| **Runs** | \|S\| | |
| **Avg tokens** | mean of `tokens` over *S* | `tokens` = prompt + completion for the run's last agent attempt (section 5). |
| **Avg time** | mean of `duration_s` over *S* | The agent's own elapsed time. For a killed or crashed agent, the clock time of the attempt. |
| **Errors** | sum over **all** the model's runs of `(agent attempts − 1) + (grading attempts − 1)` | Extra attempts after infra or grading errors. A flaky provider shows up here, not in the score. Manual retries and resumed (interrupted) attempts also count. |
| **Gave up** | number of the model's runs in state `gave_up` | Not in the score. |
| **Fails** | runs in *S* where the agent `failed` or hit the wall clock | Already counted in the mean, as their score (0 for timeouts). |

**Sort order.** By mean score, highest first. Models with no scored runs go
last.

**Comparing models: what to keep in mind**

- **Coverage can differ.** Gave-up runs drop out. If model X gave up on its
  two hardest tasks, X's mean is taken over easier tasks than model Y's. Compare
  `Runs` (and `Gave up`) across models before comparing means.
- **Mid-grid numbers are partial.** While a grid runs, each model's mean
  covers whichever tasks have finished so far.

### Task × model matrix

One cell per (task, model): the **mean score over that pair's scored
repeats**, plus *n*, the number of scored repeats. An empty cell means no
scored run yet.

### Scatters

One point per model that has at least one scored run:
- **Score vs. tokens:** x = Avg tokens, y = Mean score.
- **Score vs. time:** x = Avg time, y = Mean score.

### Tool-usage profile

For each model and each MCP server (plus `toolbelt` for the meta-tools), the
number of `tool_call` events, counted over **every attempt of every run**:
failed and superseded attempts, gave-up runs, and runs still in progress
all count. The UI shows each server's **share** of the model's calls. It
describes how a model works, not how well it scored.

The server is read from the tool name's `{server}_` prefix, which the
gateway adds.

## 3. Mission Control (`GET /api/summary`)

| Number | Calculation |
|---|---|
| **Scored / Running / Queued / Retrying / Gave up** | Runs per state. **Running** covers every active phase (sandbox starting, populating, apps starting, agent, snapshot, grading). **Retrying** = waiting out the backoff before another attempt. |
| **% done** | (scored + gave up) / total runs |
| **Elapsed** | now − the earliest run start. When nothing is pending or active, it stops at the last run's finish. |
| **ETA** | mean duration of the finished `scored` runs (wall time from first start to finish, including retries and grading) × runs left (queued + running + retrying) / `max_parallel`. A rough figure: it ignores per-model concurrency caps, and it assumes the runs left take as long as the ones that finished. |
| **Tokens** | sum of prompt and completion tokens over all runs, counting each run's latest agent attempt only. Tokens spent by earlier, failed attempts are **not** included, so this undercounts spend when there were retries. |
| **Attempts / Retries** | the sum of agent and grading attempts over all runs, and the attempts beyond each run's first |

## 4. Run Detail (`GET /api/runs/{id}`)

The run's latest attempt directory supplies:
- the timeline (its events);
- tokens per model call (`trajectory.usage.call_log`);
- the final answer;
- the per-criterion grades.

A criterion is shown as **passed** when its score is `>= pass_score`. The
attempt list shows every agent and grading attempt, each with the reason it
was rerun.

## 5. Where tokens and durations come from

Live, while the agent runs:
- The grid tails the agent's event log. Each model call's `llm_usage` line
  adds its prompt and completion tokens to the run's running totals.
- Each `tool_call` adds one to the tool-call count.

When the agent exits, these values replace the running totals:
- the trajectory's `usage.total_tokens`, `prompt_tokens` and
  `completion_tokens`;
- `steps`, the number of model calls;
- `time_elapsed`.

If the trajectory has no `usage` (the agent was killed at the wall clock or
crashed), the running totals stay. The duration then becomes the clock time
of the agent process.

A new agent attempt resets the totals, so the numbers always describe the
**latest attempt**.

## 6. Exports

- **`/api/export.csv` and `/api/export.json`:** one row per run with its raw
  fields (state, score, classification, attempts, tokens, duration, reason).
  The API doesn't aggregate them; use these to recompute anything differently.
- **The leaderboard page's CSV:** built in the browser from the leaderboard
  response, one row per model with the columns above.

## In the browser

These few values are computed in the page, from API data:
- **Heatmap colour:** a cell's score on a sequential ramp (0 → 1).
- **% done bar:** the segments come from the summary counts.
- **Live Map:**
  - **calls/min:** `tool_call` events seen in the last 60 seconds of the
    stream, per server hub;
  - **edge thickness:** the cumulative calls seen since the page opened.

  Both reset on reload. They show activity, not results.

## Changing a method

| To change | Edit |
|---|---|
| How criteria combine into a run score | `scoring_config` in grid.json (for example a weighted method from `grading/runner/scoring_methods/`) |
| The pass threshold | `pass_score` in grid.json |
| Leaderboard aggregation (mean, CI, coverage, what counts as a fail) | `leaderboard_view` in `grid/grid/views.py`; also update `rules` there and this document |
| Which outcomes are scored, rerun or excluded | `grid/grid/classify.py` and the grid README's table |
