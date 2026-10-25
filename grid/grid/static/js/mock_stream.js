// Scripted simulator for mock mode. It starts from the fixture snapshot
// (summary.json + runs.json), keeps about `max_parallel` runs going, emits
// events in the /api/stream schema and mutates summary/runs in memory so
// every screen looks live. Only api.js talks to this module.

const CATALOG = {
  filesystem_server: [['list_files', 'dir'], ['get_directory_tree', 'root'], ['read_text_file', 'txt'],
    ['search_files', 'pattern'], ['get_file_metadata', 'any']],
  docs_server: [['read_document_content', 'docx'], ['get_document_overview', 'docx'],
    ['create_document', 'newdocx'], ['add_content_text', 'newdocx'], ['edit_content_text', 'docx']],
  sheets_server: [['list_tabs_in_spreadsheet', 'xlsx'], ['read_tab', 'xlsx'], ['read_csv', 'csv'],
    ['edit_spreadsheet', 'xlsx'], ['create_chart', 'xlsx']],
  slides_server: [['read_slides', 'pptx'], ['create_deck', 'newpptx'], ['add_slide', 'newpptx'], ['insert_chart', 'newpptx']],
  pdf_server: [['read_pdf_pages', 'pdf'], ['search_pdf', 'pdf']],
  mail_server: [['list_mails', 'none'], ['search_mail', 'q'], ['read_mail', 'mail'], ['reply_mail', 'mail']],
  calendar_server: [['list_events', 'range'], ['read_event', 'event'], ['create_event', 'newevent']],
  chat_server: [['list_channels', 'none'], ['get_channel_history', 'channel'], ['get_thread_replies', 'thread'], ['post_message', 'channel']],
  code_execution_server: [['code_exec', 'code']],
};

const REASONING = [
  'Let me look at what is in the workspace before changing anything.',
  'I need tools for this file type. Checking the toolbelt.',
  'The instructions mention a convention; I will follow it exactly.',
  'That path did not exist. Listing the folder again.',
  'Two candidates match. Comparing their metadata first.',
  'I have what I need. Writing the output now.',
  'Verifying the output opens and has the expected content.',
];

const PHASE = {
  queued: 'queued', retry: 'queued', env_starting: 'setup', populating: 'setup', apps_starting: 'setup',
  agent_running: 'agent', snapshotting: 'grading', grading: 'grading', scored: 'done', gave_up: 'done',
};

const rand = (a, b) => a + Math.random() * (b - a);
const randi = (a, b) => Math.floor(rand(a, b + 1));
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const clamp01 = (x) => Math.max(0, Math.min(1, x));
function gauss() { return Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random()); }

export class MockStream {
  constructor({ grid, summary, runs, leaderboard, worldFiles }) {
    this.grid = grid;
    this.summary = structuredClone(summary);
    this.runs = structuredClone(runs);
    this.byId = new Map(this.runs.map((r) => [r.run_id, r]));
    this.leaderboardBase = leaderboard;
    this.worldFiles = worldFiles; // world_id -> [paths]
    this.tasks = new Map(grid.tasks.map((t) => [t.task_id, t]));
    this.worlds = new Map(grid.worlds.map((w) => [w.world_id, w]));
    this.listeners = new Set();
    this.seq = 10000;
    this.started = false;
    this.paused = false;
    this.vt = 0; // virtual ms since the simulator started
    this.timers = [];
    this.runEvents = new Map();
    this.liveToolCounts = {}; // model_id -> server -> calls
    this.slots = new Map(); // port -> run_id
    this.baseTs = Date.parse(summary.started_at) + summary.elapsed_s * 1000;

    // Skill estimates from the fixture leaderboard so live scores stay plausible.
    this.skill = Object.fromEntries(leaderboard.models.map((m) => [m.model_id, m.mean_score]));
    const overall = leaderboard.models.reduce((a, m) => a + m.mean_score, 0) / leaderboard.models.length;
    this.taskBias = {};
    for (const t of grid.tasks) {
      const cells = leaderboard.matrix.filter((c) => c.task_id === t.task_id && c.mean_score !== null);
      const avg = cells.length ? cells.reduce((a, c) => a + c.mean_score, 0) / cells.length : overall;
      this.taskBias[t.task_id] = avg - overall;
    }
  }

  // ---------- public ----------
  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  start() {
    if (this.started) { this.resume(); return; }
    this.started = true;
    this.summary.state = 'running';
    this.lastReal = performance.now();
    this.interval = setInterval(() => this.tick(), 50);
    // Resume runs that were mid-flight in the snapshot.
    for (const w of this.summary.workers) {
      if (!w.run_id) continue;
      const run = this.byId.get(w.run_id);
      if (run && PHASE[run.state] !== 'done' && PHASE[run.state] !== 'queued') {
        this.slots.set(w.port, run.run_id);
        this.resumeRun(run, w.port);
      }
    }
    this.infra(null, 'Simulator attached to grid ' + this.grid.grid_id);
    this.fill();
  }
  pause() {
    if (!this.started || this.paused) return;
    this.paused = true;
    this.summary.state = 'paused';
    this.infra(null, 'Grid paused: running attempts finish their current step, no new runs start');
  }
  resume() {
    if (!this.paused) return;
    this.paused = false;
    this.summary.state = 'running';
    this.lastReal = performance.now();
    this.infra(null, 'Grid resumed');
  }

  summarySnapshot() { this.refreshSummary(); return structuredClone(this.summary); }
  runsSnapshot() { return structuredClone(this.runs); }
  eventsFor(id) { return structuredClone(this.runEvents.get(id) || []); }

  leaderboard() {
    const base = this.leaderboardBase;
    const models = this.grid.models.map((m) => {
      const done = this.runs.filter((r) => r.model_id === m.id && (r.state === 'scored' || r.state === 'gave_up'));
      const sc = done.map((r) => (r.score === null ? 0 : r.score));
      const n = sc.length;
      const mean = n ? sc.reduce((a, b) => a + b, 0) / n : 0;
      const sd = n > 1 ? Math.sqrt(sc.reduce((a, x) => a + (x - mean) ** 2, 0) / (n - 1)) : 0;
      const prof = { ...((base.models.find((x) => x.model_id === m.id) || {}).tool_profile || {}) };
      for (const [srv, c] of Object.entries(this.liveToolCounts[m.id] || {})) prof[srv] = (prof[srv] || 0) + c;
      return {
        model_id: m.id, mean_score: +mean.toFixed(3), ci95: n > 1 ? +(1.96 * sd / Math.sqrt(n)).toFixed(3) : null,
        pass_rate: n ? +(sc.filter((x) => x >= 0.8).length / n).toFixed(3) : 0, runs: n,
        avg_tokens: n ? Math.round(done.reduce((a, r) => a + r.tokens, 0) / n) : 0,
        avg_duration_s: n ? Math.round(done.reduce((a, r) => a + r.duration_s, 0) / n) : 0,
        errors: this.runs.filter((r) => r.model_id === m.id).reduce((a, r) => a + Math.max(0, r.attempt - 1), 0),
        fails: done.filter((r) => r.state === 'gave_up').length, tool_profile: prof,
      };
    }).sort((a, b) => b.mean_score - a.mean_score);
    const matrix = [];
    for (const t of this.grid.tasks) {
      for (const m of this.grid.models) {
        const cell = this.runs.filter((r) => r.task_id === t.task_id && r.model_id === m.id && (r.state === 'scored' || r.state === 'gave_up'));
        matrix.push({ task_id: t.task_id, model_id: m.id, n: cell.length,
          mean_score: cell.length ? +(cell.reduce((a, r) => a + (r.score || 0), 0) / cell.length).toFixed(3) : null });
      }
    }
    return { models, matrix };
  }

  /** A run-detail object for runs without a fixture, built from what the simulator saw. */
  synthRun(id) {
    const run = this.byId.get(id);
    if (!run) return null;
    const evs = this.runEvents.get(id) || [];
    const callLog = evs.filter((e) => e.kind === 'step').map((e) => ({
      prompt_tokens: Math.round(e.tokens * 0.93), completion_tokens: Math.round(e.tokens * 0.07), reasoning_tokens: 0,
    }));
    const fin = evs.find((e) => e.kind === 'final_answer');
    const crit = evs.filter((e) => e.kind === 'grading_progress');
    return {
      ...structuredClone(run),
      attempts: [{ attempt: run.attempt, state: run.state, reason: run.classification || 'in progress',
        started_at: run.started_at, finished_at: run.finished_at }],
      usage: { call_log: callLog },
      final_answer: fin ? fin.text : null,
      grades: run.state === 'scored' ? {
        final_score: run.score, grading_run_status: 'completed',
        criteria: crit.map((e, i) => ({ verifier_id: `ver_llm_judge_${i + 1}`, criteria: `Criterion ${i + 1} (mock)`,
          score: e.ok ? 1 : 0, passed: !!e.ok, rationale: 'Simulated in mock mode.' })),
      } : null,
      _synthetic: true,
    };
  }

  // ---------- clock ----------
  now() { return new Date(this.baseTs + this.vt).toISOString().replace(/\.\d+Z$/, 'Z'); }
  after(ms, fn) { this.timers.push({ at: this.vt + ms, fn }); }
  tick() {
    const real = performance.now();
    const dt = real - this.lastReal;
    this.lastReal = real;
    if (this.paused) return;
    this.vt += Math.min(dt, 250);
    const due = this.timers.filter((t) => t.at <= this.vt).sort((a, b) => a.at - b.at);
    if (due.length) this.timers = this.timers.filter((t) => t.at > this.vt);
    for (const t of due) t.fn();
    // durations of in-flight runs advance in real time
    for (const port of this.slots.keys()) {
      const run = this.byId.get(this.slots.get(port));
      if (run && run.started_at) run.duration_s = Math.round((this.baseTs + this.vt - Date.parse(run.started_at)) / 1000);
    }
    if (Math.random() < 0.0015) this.randomInfra();
  }

  // ---------- events ----------
  emit(run, kind, extra = {}) {
    const e = {
      seq: ++this.seq, ts: this.now(), run_id: run ? run.run_id : null, model_id: run ? run.model_id : null,
      task_id: run ? run.task_id : null, kind, server: null, tool: null, ref: null, args_preview: null, ok: null,
      duration_ms: null, tokens: null, text: null, state: null, ...extra,
    };
    if (run) {
      let list = this.runEvents.get(run.run_id);
      if (!list) this.runEvents.set(run.run_id, (list = []));
      list.push(e);
      if (list.length > 300) list.shift();
    }
    for (const fn of this.listeners) {
      try { fn(e); } catch (err) { console.error(err); }
    }
    return e;
  }
  infra(run, text, ok = null) { return this.emit(run, 'infra', { text, ok }); }

  setState(run, state, text) {
    run.state = state;
    run.phase = PHASE[state];
    const w = this.summary.workers.find((x) => x.run_id === run.run_id);
    if (w) w.state = state;
    this.emit(run, 'run_state', { state, text: text || null });
  }

  randomInfra() {
    const w = pick(this.summary.workers);
    if (Math.random() < 0.5) {
      w.health = 'degraded';
      this.infra(null, `Port ${w.port}: health check slow (${rand(1.5, 3.5).toFixed(1)} s)`, false);
      this.after(randi(3000, 7000), () => { w.health = 'ok'; this.infra(null, `Port ${w.port}: healthy again`, true); });
    } else {
      const m = pick(this.grid.models);
      this.infra(null, `Provider ${m.provider} returned 429; backing off ${randi(2, 9)} s`, false);
    }
  }

  // ---------- scheduling ----------
  queue() {
    const q = this.runs.filter((r) => r.state === 'queued' || r.state === 'retry');
    return [...q.filter((r) => r.state === 'retry'), ...q.filter((r) => r.state === 'queued')];
  }
  fill() {
    if (this.paused) return;
    for (const w of this.summary.workers) {
      if (this.slots.has(w.port)) continue;
      const next = this.queue()[0];
      if (!next) {
        w.run_id = null; w.state = 'idle';
        continue;
      }
      this.slots.set(w.port, next.run_id);
      w.run_id = next.run_id;
      this.startRun(next, w.port);
    }
    if (!this.slots.size && !this.queue().length && this.summary.state !== 'done') {
      this.summary.state = 'done';
      this.infra(null, 'Grid finished: every run is scored or gave up', true);
    }
  }
  release(port) {
    this.slots.delete(port);
    const w = this.summary.workers.find((x) => x.port === port);
    if (w) { w.run_id = null; w.state = 'idle'; }
    this.after(randi(300, 900), () => this.fill());
  }

  startRun(run, port) {
    if (run.state === 'retry') run.attempt = Math.max(run.attempt, 1);
    run.started_at = this.now();
    run.finished_at = null;
    run.duration_s = 0;
    run.score = null;
    run.classification = null;
    run.agent_status = null;
    const w = this.worlds.get(this.tasks.get(run.task_id).world_id);
    this.setState(run, 'env_starting', `Container on port ${port}` + (run.attempt > 1 ? ` (attempt ${run.attempt})` : ''));
    this.after(randi(700, 1400), () => {
      this.setState(run, 'populating', `Loading ${w.name} (${w.file_count} files)`);
      this.after(randi(800, 1600), () => {
        this.setState(run, 'apps_starting', `Starting ${w.apps.length} MCP servers`);
        this.after(randi(1200, 2400), () => this.startAgent(run, port));
      });
    });
  }

  resumeRun(run, port) {
    if (['env_starting', 'populating', 'apps_starting'].includes(run.state)) {
      this.after(randi(500, 1500), () => this.startAgent(run, port));
    } else if (run.state === 'agent_running') {
      this.agentLoop(run, port, { step: run.steps || 0, maxSteps: (run.steps || 0) + randi(3, 7), ref: 1000 });
    } else {
      this.grade(run, port);
    }
  }

  startAgent(run, port) {
    run.agent_status = 'running';
    this.setState(run, 'agent_running', 'Agent started with meta-tools only');
    const skill = this.skill[run.model_id] || 0.6;
    this.agentLoop(run, port, { step: 0, maxSteps: randi(5, 9) + Math.round((1 - skill) * 6), ref: 0 });
  }

  agentLoop(run, port, ctx) {
    if (ctx.step >= ctx.maxSteps) return this.finishAgent(run, port, ctx);
    ctx.step += 1;
    const tokens = 2500 + ctx.step * 2100 + randi(0, 1500);
    run.steps = ctx.step;
    run.tokens += tokens;
    this.summary.tokens.prompt += Math.round(tokens * 0.93);
    this.summary.tokens.completion += Math.round(tokens * 0.07);
    this.emit(run, 'step', { text: `Step ${ctx.step}`, tokens });

    // Infrequent provider failure -> retry or give up.
    if (ctx.step > 2 && Math.random() < 0.012) return this.failRun(run, port);

    const think = randi(500, 1600);
    this.after(think, () => {
      if (Math.random() < 0.35 || ctx.step === 1) this.emit(run, 'reasoning', { text: pick(REASONING) });
      if (Math.random() < 0.03) this.emit(run, 'compaction', { text: `Context compacted from ${randi(28, 60)}k to ${randi(8, 14)}k tokens` });
      const calls = [];
      if (ctx.step === 1) calls.push(['toolbelt', 'toolbelt_list_tools', {}]);
      else if (ctx.step === 2) {
        const task = this.tasks.get(run.task_id);
        const names = task.mcp_servers.flatMap((sv) => CATALOG[sv].slice(0, 2).map(([t]) => `${sv}_${t}`));
        calls.push(['toolbelt', 'toolbelt_add_tool', { tool_names: names }]);
        this.emit(run, 'toolbelt', { server: 'toolbelt', text: `Added ${names.length} tools: ${names.join(', ')}` });
      } else {
        calls.push(this.pickCall(run));
        if (Math.random() < 0.2) calls.push(this.pickCall(run));
      }
      let pending = calls.length;
      for (const [server, tool, args] of calls) {
        this.toolCall(run, ctx, server, tool, args, () => {
          pending -= 1;
          if (pending === 0) this.after(randi(300, 900), () => this.agentLoop(run, port, ctx));
        });
      }
    });
  }

  pickCall(run) {
    const task = this.tasks.get(run.task_id);
    const world = this.worlds.get(task.world_id);
    const servers = [...task.mcp_servers];
    if (world.apps.includes('filesystem_server') && !servers.includes('filesystem_server')) servers.push('filesystem_server');
    const server = Math.random() < 0.08 ? 'toolbelt' : pick(servers);
    if (server === 'toolbelt') return ['toolbelt', pick(['todo_write', 'toolbelt_inspect_tool']), { tool_name: `${pick(task.mcp_servers)}_${pick(CATALOG[pick(task.mcp_servers)])[0]}` }];
    const [tool, kind] = pick(CATALOG[server]);
    return [server, `${server}_${tool}`, this.argsFor(kind, task, world)];
  }

  argsFor(kind, task, world) {
    const files = this.worldFiles[world.world_id] || [];
    const byExt = (exts) => {
      const c = files.filter((f) => exts.some((e) => f.endsWith(e)));
      return c.length ? pick(c) : pick(files) || '/';
    };
    const out = world.world_id.includes('survey') ? '/output' : world.world_id.includes('inbox') ? '/docs' : '/projects';
    const stem = task.task_id.split('_')[0];
    switch (kind) {
      case 'dir': return { path: pick([...new Set(files.map((f) => f.slice(0, f.lastIndexOf('/')) || '/'))]) };
      case 'root': return { path: '/', max_depth: 3 };
      case 'txt': return { path: byExt(['.txt', '.md', '.csv']) };
      case 'pattern': return { path: '/', pattern: pick(['*.docx', '*notes*', '*.pdf', '*v2*']) };
      case 'any': return { path: pick(files) };
      case 'docx': return { file_path: byExt(['.docx']) };
      case 'newdocx': return { file_path: `${out}/${stem}_output.docx` };
      case 'xlsx': return { file_path: byExt(['.xlsx']), tab_index: 0 };
      case 'csv': return { file_path: byExt(['.csv']) };
      case 'pptx': return { file_path: byExt(['.pptx']) };
      case 'newpptx': return { file_path: `${out}/${stem}_deck.pptx` };
      case 'pdf': return { file_path: byExt(['.pdf']), pages: '1-4' };
      case 'q': return { query: pick(['sync', 'schedule', 'next week', 'availability']), limit: 10 };
      case 'mail': return { mail_id: `<msg-${randi(1000, 9999)}@example.test>` };
      case 'range': return { start: '2026-11-02', end: '2026-11-06' };
      case 'event': return { event_id: `evt_${randi(100, 999)}` };
      case 'newevent': return { summary: 'Project sync', start: '2026-11-04T15:00', duration_min: 45 };
      case 'channel': return { channel_id: pick(['general', 'launch']) };
      case 'thread': return { channel_id: 'general', thread_ts: '1729851200.000100' };
      case 'code': return { code: "import pandas as pd\ndf = pd.read_csv('/data/survey_responses.csv')" };
      default: return {};
    }
  }

  toolCall(run, ctx, server, tool, args, done) {
    ctx.ref += 1;
    const ref = `call_${ctx.ref}`;
    let preview = JSON.stringify(args);
    if (preview.length > 160) preview = preview.slice(0, 157) + '...';
    run.tool_calls += 1;
    const m = (this.liveToolCounts[run.model_id] ||= {});
    m[server] = (m[server] || 0) + 1;
    this.emit(run, 'tool_call', { server, tool, ref, args_preview: preview });
    const ms = randi(200, 3000);
    const ok = Math.random() > 0.07;
    this.after(ms, () => {
      this.emit(run, 'tool_result', { server, tool, ref, ok, duration_ms: ms,
        text: ok ? resultText(tool) : pick(['Error: path does not exist', 'Error: sheet index out of range', 'Error: invalid arguments']) });
      done();
    });
  }

  finishAgent(run, port, ctx) {
    const text = 'Task complete. The output was written and checked against the instructions.';
    this.emit(run, 'response', { text: 'I have finished and verified the output.' });
    this.toolCall(run, ctx, 'toolbelt', 'final_answer', { answer: text }, () => {
      this.emit(run, 'final_answer', { text });
      run.agent_status = 'completed';
      this.setState(run, 'snapshotting', 'Saving world snapshot');
      this.after(randi(700, 1300), () => this.grade(run, port));
    });
  }

  grade(run, port) {
    const task = this.tasks.get(run.task_id);
    const n = task.criteria_count;
    const mu = (this.skill[run.model_id] || 0.6) + (this.taskBias[run.task_id] || 0);
    const passedCount = Math.round(clamp01(mu + gauss() * 0.13) * n);
    const passes = Array.from({ length: n }, (_, i) => i < passedCount).sort(() => Math.random() - 0.5);
    this.setState(run, 'grading', `${n} criteria`);
    let k = 0;
    const next = () => {
      if (k < n) {
        const ok = passes[k];
        k += 1;
        this.emit(run, 'grading_progress', { text: `Criterion ${k}/${n}: ${ok ? 'pass' : 'fail'}`, ok });
        this.after(randi(250, 600), next);
      } else {
        const score = +(passedCount / n).toFixed(3);
        run.score = score;
        run.classification = 'scored';
        run.finished_at = this.now();
        this.emit(run, 'grade', { text: `Final score ${score.toFixed(2)}`, ok: score >= 0.5 });
        this.setState(run, 'scored', `Score ${score.toFixed(2)}`);
        this.release(port);
      }
    };
    this.after(randi(300, 700), next);
  }

  failRun(run, port) {
    const timeout = Math.random() < 0.5;
    run.agent_status = 'error';
    this.summary.retries += 1;
    if (run.attempt >= 3) {
      run.classification = 'fail_timeout';
      run.finished_at = this.now();
      this.infra(run, `Attempt ${run.attempt} failed again; giving up`, false);
      this.setState(run, 'gave_up', 'Classified fail_timeout after 3 attempts');
    } else {
      run.classification = timeout ? 'fail_timeout' : 'rerun_agent';
      this.infra(run, timeout ? 'Provider call timed out after 180 s' : `Provider returned ${pick([502, 503])} during step ${run.steps}`, false);
      run.attempt += 1;
      this.summary.attempts += 1;
      this.setState(run, 'retry', `Requeued as attempt ${run.attempt} (${run.classification})`);
    }
    this.release(port);
  }

  refreshSummary() {
    const c = { queued: 0, running: 0, retrying: 0, scored: 0, gave_up: 0 };
    for (const r of this.runs) {
      const g = PHASE[r.state];
      if (r.state === 'retry') c.retrying += 1;
      else if (r.state === 'scored') c.scored += 1;
      else if (r.state === 'gave_up') c.gave_up += 1;
      else if (g === 'queued') c.queued += 1;
      else c.running += 1;
    }
    this.summary.counts = c;
    this.summary.elapsed_s = Math.round((this.baseTs + this.vt - Date.parse(this.summary.started_at)) / 1000);
    const done = c.scored + c.gave_up;
    const left = c.queued + c.retrying + c.running;
    // The fixture clock is real minutes; the simulator runs compressed, so ETA uses the recent pace.
    this.summary.eta_s = left === 0 ? 0 : Math.round((this.summary.elapsed_s / Math.max(1, done)) * left);
  }
}

function resultText(tool) {
  if (/list|tree|channels/.test(tool)) return `${randi(4, 40)} entries`;
  if (/read|get_|search/.test(tool)) return `Read ${randi(800, 24000).toLocaleString('en-US')} chars`;
  if (tool === 'todo_write') return '3 todos saved';
  if (tool === 'final_answer') return 'Answer recorded';
  if (tool.endsWith('code_exec')) return 'exit 0 · 14 lines of output';
  if (tool.startsWith('toolbelt')) return 'ok';
  return 'Saved';
}
