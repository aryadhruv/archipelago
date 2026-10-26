// Setup / Overview: what the grid will run, preflight checks, Start.

import * as api from '../api.js';
import { store, worldById } from '../store.js';
import {
  h, icon, fmtBytes, fmtInt, fmtTokens, modelColor, modelInk, serverLabel, tip, clear, fmtDateTime,
  fmtDuration, fmtScore, modelLabel, modelInitials, modelSwatch,
} from '../util.js';

export function render(root) {
  const g = store.grid;
  const t = g.totals;

  const startBtn = h('button', { class: 'btn btn-primary btn-lg', type: 'button' }, icon('play', 16), 'Start grid');
  const smokeBtn = h('button', { class: 'btn btn-lg', type: 'button' }, 'Smoke test');
  const smokeOut = h('div', { class: 'smoke-result' });
  const preflightBox = h('div', { class: 'card' }, h('div', { class: 'empty' }, h('span', { class: 'spinner' }), 'Running preflight…'));

  const state = store.summary ? store.summary.state : 'idle';
  if (state === 'running' || state === 'paused') {
    clear(startBtn).append(icon('map', 16), 'Open Mission Control');
  }
  startBtn.addEventListener('click', async () => {
    startBtn.disabled = true;
    try {
      await api.startGrid();
      location.hash = '#/live';
    } catch (err) {
      clear(smokeOut).append(h('span', { class: 'pill bad' }, icon('x', 12), 'Could not start'), ' ', err.message);
      startBtn.disabled = false;
    }
  });
  smokeBtn.addEventListener('click', async () => {
    smokeBtn.disabled = true;
    startBtn.disabled = true;
    const taskName = g.tasks[0] ? g.tasks[0].task_name : '';
    const progress = (s) => {
      const done = s.runs.filter((r) => r.phase === 'done').length;
      clear(smokeOut).append(h('span', { class: 'spinner' }),
        ` Smoke test: ${taskName} on every model (${done}/${s.runs.length || g.models.length} done)…`);
    };
    progress({ runs: [] });
    try {
      const r = await api.runSmokeTest(progress);
      // Passing means every model could call the tools and get graded, not a
      // particular score.
      clear(smokeOut).append(
        h('div', null, h('span', { class: 'pill ' + (r.ok ? 'good' : 'bad') }, icon(r.ok ? 'check' : 'x', 12),
          r.ok ? 'Every model finished' : 'Some models did not finish'), ` ${taskName}`),
        r.error ? h('div', null, r.error) : null,
        h('ul', { class: 'smoke-runs' }, r.runs.map((run) => h('li', null,
          modelSwatch(run.model_id),
          ` ${modelLabel(run.model_id)}: `,
          run.state === 'scored'
            ? `score ${fmtScore(run.score)}${run.duration_s ? ' in ' + fmtDuration(run.duration_s) : ''}`
            : h('span', { class: 'bad-text' }, `${run.state}${run.reason ? ' (' + run.reason.slice(0, 120) + ')' : ''}`)))));
    } catch (err) {
      clear(smokeOut).append(h('span', { class: 'pill bad' }, icon('x', 12), 'Smoke test failed'), ' ', err.message);
    }
    smokeBtn.disabled = false;
    startBtn.disabled = false;
  });

  const hero = h('section', { class: 'hero' },
    h('div', { class: 'hero-eq', 'aria-label': `${t.tasks} tasks times ${t.models} models times ${t.repeats} repeats equals ${t.runs} runs` },
      term(t.tasks, 'tasks'), h('span', { class: 'hero-op' }, '×'),
      term(t.models, 'models'), h('span', { class: 'hero-op' }, '×'),
      term(t.repeats, 'repeats'), h('span', { class: 'hero-op' }, '='),
      term(t.runs, 'runs')),
    h('div', { class: 'hero-meta' },
      h('div', null, h('span', { class: 'k' }, 'Image'), h('span', { class: 'mono' }, g.image_tag)),
      h('div', null, h('span', { class: 'k' }, 'Git SHA'), h('span', { class: 'mono' }, g.git_sha)),
      h('div', null, h('span', { class: 'k' }, 'Judge'), h('span', { class: 'mono' }, g.judge_model)),
      h('div', null, h('span', { class: 'k' }, 'Parallel'), `${g.max_parallel} sandboxes at a time`),
      h('div', null, h('span', { class: 'k' }, 'Created'), fmtDateTime(g.created_at))),
    h('div', { class: 'hero-actions' }, h('div', { style: { display: 'grid', gap: '8px', justifyItems: 'end' } },
      h('div', { style: { display: 'flex', gap: '8px' } }, smokeBtn, startBtn), smokeOut)));

  const worlds = h('div', { class: 'grid-auto' }, g.worlds.map((w) => h('div', { class: 'card' },
    h('h3', null, w.name),
    h('div', { class: 'meta' }, h('span', { class: 'num' }, fmtBytes(w.size_bytes)), h('span', { class: 'num' }, `${w.file_count} files`),
      h('span', { class: 'mono' }, w.world_id)),
    h('div', { class: 'tags', style: { marginTop: '12px' } }, w.apps.map((a) => h('span', { class: 'server-tag' }, serverLabel(a)))))));

  const tasks = h('div', { class: 'grid-auto' }, g.tasks.map((tk) => {
    const p = h('p', { class: 'prompt-preview clamped' }, tk.prompt);
    const more = h('button', { class: 'link-btn', type: 'button', 'aria-expanded': 'false' }, 'Show full prompt');
    more.addEventListener('click', () => {
      const open = p.classList.toggle('clamped');
      more.textContent = open ? 'Show full prompt' : 'Show less';
      more.setAttribute('aria-expanded', String(!open));
    });
    const w = worldById(tk.world_id);
    return h('div', { class: 'card' },
      h('h3', null, tk.task_name),
      h('div', { class: 'meta' }, h('span', null, w ? w.name : tk.world_id), h('span', { class: 'num' }, `${tk.criteria_count} criteria`)),
      p, more,
      h('div', { class: 'tags', style: { marginTop: '12px' } }, tk.mcp_servers.map((sv) => h('span', { class: 'server-tag' }, serverLabel(sv)))));
  }));

  const models = h('div', { class: 'grid-auto' }, g.models.map((m) => h('div', { class: 'card card-tight' },
    h('div', { class: 'model-row' },
      h('span', { class: 'model-chip', style: { background: modelColor(m.id), color: modelInk(m.id) } }, modelInitials(m.id)),
      h('div', null, h('div', { style: { fontWeight: 600 } }, m.label), h('div', { class: 'mono muted', style: { fontSize: '11px' } }, m.model))),
    h('dl', { class: 'kv', style: { marginTop: '12px', marginBottom: 0 } },
      h('dt', null, 'Provider'), h('dd', null, m.provider),
      h('dt', null, 'Context window'), h('dd', null, fmtTokens(m.context_window_tokens) + ' tokens'),
      h('dt', null, 'Max concurrency'), h('dd', null, fmtInt(m.max_concurrency))))));

  root.append(
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Grid setup'),
      h('div', { class: 'sub' }, 'Every task runs on every model, repeated, each in a fresh sandbox, then graded against its rubric.'))),
    hero,
    h('div', { class: 'row', style: { alignItems: 'flex-start' } },
      h('div', { style: { flex: '1 1 0', minWidth: 0 } },
        h('section', { class: 'section' }, h('h2', { class: 'section-title' }, 'Tasks', h('span', { class: 'count' }, g.tasks.length)), tasks),
        h('section', { class: 'section' }, h('h2', { class: 'section-title' }, 'Models', h('span', { class: 'count' }, g.models.length)), models),
        h('section', { class: 'section' }, h('h2', { class: 'section-title' }, 'Worlds', h('span', { class: 'count' }, g.worlds.length)), worlds)),
      h('aside', { style: { width: '380px', flex: 'none', position: 'sticky', top: '80px' } },
        h('h2', { class: 'section-title' }, 'Preflight'), preflightBox)),
  );

  api.getPreflight().then((pf) => {
    clear(preflightBox);
    const nFail = pf.checks.filter((c) => c.status === 'fail').length;
    const nWarn = pf.checks.filter((c) => c.status === 'warn').length;
    preflightBox.append(
      h('div', { class: 'panel-head' },
        h('span', { class: 'pill ' + (nFail ? 'bad' : nWarn ? 'warn' : 'good') }, icon(nFail ? 'x' : nWarn ? 'warn' : 'check', 12),
          nFail ? `${nFail} failing` : nWarn ? `Ready, ${nWarn} warning${nWarn > 1 ? 's' : ''}` : 'All checks pass'),
        h('span', { class: 'right panel-note' }, `${pf.checks.length} checks`)),
      h('ul', { class: 'checklist' }, pf.checks.map((c) => h('li', null,
        h('span', { class: 'check-ic ' + c.status, 'aria-label': c.status }, icon(c.status === 'pass' ? 'check' : c.status === 'warn' ? 'warn' : 'x', 14)),
        h('div', null, h('div', null, c.label), h('div', { class: 'check-detail' }, c.detail)),
        h('span', { class: 'muted', style: { fontSize: '11px', textTransform: 'uppercase', fontWeight: 600 } }, c.status)))));
    if (!pf.ok) { startBtn.disabled = true; tip(startBtn, 'Fix failing preflight checks first'); }
  }).catch((err) => {
    clear(preflightBox).append(h('div', { class: 'empty' }, icon('warn', 24), 'Preflight unavailable', h('div', { class: 'empty-body' }, String(err))));
  });
}

function term(n, label) {
  return h('div', { class: 'hero-term' }, h('b', { class: 'num' }, fmtInt(n)), h('span', null, label));
}
