// Mission Control: KPIs, task x model heatmap, activity ticker, worker pool.

import * as api from '../api.js';
import { store, on, taskById, worldById } from '../store.js';
import {
  h, icon, clear, fmtInt, fmtTokens, fmtDuration, fmtScore, fmtClock, modelColor, modelLabel, scoreColor,
  scoreRampStops, stateGroup, stateLabel, STATE_ICON, tip, tipBody, serverLabel, shortTool, emptyState,
} from '../util.js';

export function render(root) {
  const g = store.grid;
  const offs = [];

  // ---------- header + controls ----------
  const pauseBtn = h('button', { class: 'btn', type: 'button' });
  const banner = h('div', { class: 'banner', hidden: true },
    icon('play', 18), h('div', { class: 'grow' }, h('b', null, 'The grid is not running. '),
      h('span', { class: 'muted' }, 'The board shows the latest snapshot. Start the grid to stream live progress.')),
    h('button', { class: 'btn btn-primary', type: 'button', onclick: async () => { await api.startGrid(); } }, icon('play', 14), 'Start grid'));
  pauseBtn.addEventListener('click', async () => {
    if (store.summary.state === 'paused') await api.resumeGrid();
    else await api.pauseGrid();
  });

  const kpis = h('div', { class: 'kpis' });
  const heat = buildHeatmap(g);
  const ticker = h('ul', { class: 'ticker', 'aria-live': 'off' });
  const workers = h('div', { class: 'workers' });

  root.append(
    h('div', { class: 'page-head' },
      h('div', null, h('h1', null, 'Mission Control'), h('div', { class: 'sub' }, `${g.totals.tasks} tasks × ${g.totals.models} models × ${g.totals.repeats} repeats`)),
      h('div', { class: 'actions' }, pauseBtn)),
    banner, kpis,
    h('div', { class: 'mc' },
      h('div', { style: { display: 'grid', gap: '16px', minWidth: 0 } },
        h('section', { class: 'card' },
          h('div', { class: 'panel-head' }, h('h2', null, 'Task × model'),
            h('span', { class: 'right panel-note' }, 'Each cell is one run per repeat. Click a cell to open the run.')),
          heat.node, heat.legend),
        h('section', { class: 'card' },
          h('div', { class: 'panel-head' }, h('h2', null, 'Worker pool'), h('span', { class: 'right panel-note' }, 'One sandbox per port')),
          workers)),
      h('section', { class: 'card mc-side' },
        h('div', { class: 'panel-head', style: { marginBottom: 0 } }, h('h2', null, 'Activity'), h('span', { class: 'right panel-note' }, 'Latest events across runs')),
        ticker)));

  // ---------- painters ----------
  function paintSummary() {
    const sm = store.summary;
    if (!sm) return;
    banner.hidden = !(sm.state === 'idle');
    pauseBtn.hidden = sm.state === 'idle' || sm.state === 'done';
    clear(pauseBtn).append(icon(sm.state === 'paused' ? 'play' : 'pause', 14), sm.state === 'paused' ? 'Resume' : 'Pause');
    paintKpis(kpis, sm, g.totals.runs);
    paintWorkers(workers, sm);
  }
  function paintRuns() { heat.update(store.runs); }

  let tickQueued = false;
  function paintTicker() {
    if (tickQueued) return;
    tickQueued = true;
    requestAnimationFrame(() => {
      tickQueued = false;
      const evs = store.events.filter((e) => e.kind !== 'step' && e.kind !== 'tool_call').slice(-40).reverse();
      const lastSeq = Number(ticker.dataset.lastSeq || 0);
      clear(ticker);
      if (!evs.length) {
        ticker.append(h('li', { style: { display: 'block', borderBottom: 0 } }, emptyState('No activity yet', 'Events appear here as runs progress.', 'clock')));
        return;
      }
      for (const e of evs) ticker.append(tickerRow(e, e.seq > lastSeq && lastSeq > 0));
      ticker.dataset.lastSeq = String(evs[0].seq);
    });
  }

  paintSummary();
  paintRuns();
  paintTicker();
  offs.push(on('summary', paintSummary));
  offs.push(on('runs', paintRuns));
  offs.push(on('event', (e) => {
    if (e.kind === 'run_state') heat.update(store.runs);
    paintTicker();
  }));
  const onTheme = () => heat.update(store.runs, true);
  window.addEventListener('themechange', onTheme);
  return () => { offs.forEach((f) => f()); window.removeEventListener('themechange', onTheme); };
}

function paintKpis(root, sm, total) {
  const c = sm.counts;
  const tile = (label, value, dot) => h('div', { class: 'kpi' },
    h('div', { class: 'k' }, dot ? h('span', { class: 'dot', style: { background: dot } }) : null, label),
    h('div', { class: 'v num' }, fmtInt(value)));
  const done = c.scored + c.gave_up;
  const pct = (n) => (100 * n) / Math.max(1, total);
  const bar = h('div', { class: 'bar', role: 'img', 'aria-label': `${done} of ${total} runs finished` },
    h('i', { style: { width: pct(c.scored) + '%', background: 'var(--text-2)' } }),
    c.gave_up ? h('i', { style: { width: pct(c.gave_up) + '%', background: 'var(--critical)' } }) : null,
    c.retrying ? h('i', { style: { width: pct(c.retrying) + '%', background: 'var(--warning)' } }) : null,
    c.running ? h('i', { style: { width: pct(c.running) + '%', background: 'var(--text-muted)', opacity: 0.6 } }) : null);
  clear(root).append(
    tile('Scored', c.scored), tile('Running', c.running), tile('Queued', c.queued),
    tile('Retrying', c.retrying, c.retrying ? 'var(--warning)' : null), tile('Gave up', c.gave_up, c.gave_up ? 'var(--critical)' : null),
    h('div', { class: 'kpi kpi-progress' },
      h('div', { class: 'stats' }, h('span', null, h('b', null, `${Math.round(pct(done))}%`), ` done · ${fmtInt(done)} of ${fmtInt(total)} runs`)),
      bar,
      h('div', { class: 'stats' },
        h('span', null, 'Elapsed ', h('b', null, fmtDuration(sm.elapsed_s))),
        h('span', null, 'ETA ', h('b', null, sm.state === 'done' ? 'done' : fmtDuration(sm.eta_s))),
        h('span', null, 'Tokens ', h('b', null, fmtTokens(sm.tokens.prompt + sm.tokens.completion))),
        h('span', null, 'Retries ', h('b', null, fmtInt(sm.retries))))));
}

function paintWorkers(root, sm) {
  clear(root).append(...sm.workers.map((w) => {
    const r = w.run_id ? store.runIndex.get(w.run_id) : null;
    const node = h(r ? 'a' : 'div', { class: 'worker', href: r ? `#/run/${r.run_id}` : undefined },
      h('div', { class: 'port' }, h('span', { class: 'health ' + (w.health || 'ok'), 'aria-label': `health ${w.health}` }), `:${w.port}`),
      r ? h('div', { class: 'chip' }, h('span', { class: 'swatch', style: { background: modelColor(r.model_id) } }), modelLabel(r.model_id), h('span', { class: 'muted mono' }, r.run_id))
        : h('div', { class: 'muted' }, 'idle'),
      h('div', { class: 'st' }, r ? `${stateLabel(r.state)} · ${taskById(r.task_id).task_name}` : 'waiting for work'));
    tip(node, () => tipBody(`Port ${w.port}`, [['Health', w.health], ['Run', w.run_id || '–'], ['State', w.state || 'idle']]));
    return node;
  }));
}

function tickerRow(e, fresh) {
  const task = e.task_id ? taskById(e.task_id) : null;
  let what = e.kind.replace('_', ' ');
  let detail = e.text || '';
  let cls = '';
  if (e.kind === 'tool_result') {
    what = shortTool(e.tool, e.server);
    detail = `${serverLabel(e.server)} · ${e.ok ? 'ok' : 'error'} · ${e.duration_ms} ms${e.text ? ' · ' + e.text : ''}`;
    cls = e.ok ? '' : 'err';
  } else if (e.kind === 'run_state') {
    what = stateLabel(e.state);
  } else if (e.kind === 'grade') {
    what = 'graded';
  } else if (e.kind === 'infra') {
    cls = e.ok === false ? 'err' : '';
  }
  return h('li', { class: fresh ? 'fresh' : '' },
    h('span', { class: 't' }, fmtClock(e.ts)),
    h('span', { class: 'bar', style: { background: e.model_id ? modelColor(e.model_id) : 'var(--text-muted)' } }),
    h('div', { class: 'body' },
      h('div', { class: 'line1' },
        e.model_id ? h('b', null, modelLabel(e.model_id)) : h('b', null, 'Infra'),
        task ? h('span', { class: 'muted' }, task.task_name) : null,
        h('span', { class: cls || 'text-2' }, '· ' + what)),
      detail ? h('div', { class: 'line2 ' + cls }, detail) : null));
}

// ---------- heatmap ----------
function buildHeatmap(g) {
  const cols = `minmax(180px, 220px) repeat(${g.models.length}, minmax(96px, 1fr))`;
  const node = h('div', { class: 'heat', style: { gridTemplateColumns: cols }, role: 'grid', 'aria-label': 'Task by model run status' });
  const segs = new Map(); // `${task}|${model}|${repeat}` -> element
  node.append(h('div'));
  for (const m of g.models) {
    const head = h('div', { class: 'heat-head' }, h('span', { class: 'swatch', style: { background: modelColor(m.id) } }), m.label);
    head.dataset.model = m.id;
    node.append(head);
  }
  for (const t of g.tasks) {
    const w = worldById(t.world_id);
    node.append(h('div', { class: 'heat-rowlabel' }, h('span', { title: t.task_name }, t.task_name), h('small', null, w ? w.name : '')));
    for (const m of g.models) {
      const cell = h('div', { class: 'heat-cell' });
      for (let r = 1; r <= g.repeats; r++) {
        const seg = h('div', { class: 'seg-cell queued', role: 'gridcell', tabindex: '0' });
        seg.dataset.key = `${t.task_id}|${m.id}|${r}`;
        segs.set(seg.dataset.key, seg);
        cell.append(seg);
      }
      node.append(cell);
    }
  }
  const runByKey = new Map();
  node.addEventListener('click', (e) => {
    const seg = e.target.closest('.seg-cell');
    const run = seg && runByKey.get(seg.dataset.key);
    if (run) location.hash = `#/run/${run.run_id}`;
  });
  node.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const seg = e.target.closest('.seg-cell');
    const run = seg && runByKey.get(seg.dataset.key);
    if (run) location.hash = `#/run/${run.run_id}`;
  });
  for (const seg of segs.values()) {
    tip(seg, () => {
      const r = runByKey.get(seg.dataset.key);
      if (!r) return 'Not scheduled';
      const t = taskById(r.task_id);
      return tipBody(`${modelLabel(r.model_id)} · ${t.task_name}`, [
        ['Run', r.run_id], ['Repeat', String(r.repeat)], ['State', stateLabel(r.state)],
        ['Score', fmtScore(r.score)], ['Attempt', String(r.attempt)],
        ['Duration', r.duration_s ? fmtDuration(r.duration_s) : '–'], ['Tokens', fmtTokens(r.tokens)],
      ], modelColor(r.model_id));
    });
  }

  function update(runs, force = false) {
    for (const r of runs) {
      const key = `${r.task_id}|${r.model_id}|${r.repeat}`;
      runByKey.set(key, r);
      const seg = segs.get(key);
      if (!seg) continue;
      const grp = stateGroup(r.state);
      const sig = `${r.state}|${r.score}|${force ? Math.random() : ''}`;
      if (seg.dataset.sig === sig) continue;
      seg.dataset.sig = sig;
      seg.className = 'seg-cell ' + grp;
      seg.style.background = '';
      clear(seg);
      if (grp === 'scored') {
        seg.style.background = scoreColor(r.score);
      } else if (grp === 'running') {
        seg.append(icon(STATE_ICON[r.state] || 'agent', 14));
      } else if (grp === 'retrying') {
        seg.append(icon('retry', 14));
      } else if (grp === 'gave_up') {
        seg.append(icon('x', 14));
      }
      seg.setAttribute('aria-label', `${r.run_id} ${stateLabel(r.state)}${r.score !== null ? ' score ' + fmtScore(r.score) : ''}`);
    }
    // header swatches follow the theme
    for (const head of node.querySelectorAll('.heat-head')) {
      const sw = head.querySelector('.swatch');
      if (sw) sw.style.background = modelColor(head.dataset.model);
    }
    legendRamp.style.background = `linear-gradient(90deg, ${scoreRampStops().join(',')})`;
  }

  const legendRamp = h('span', { class: 'ramp' });
  const legend = h('div', { class: 'heat-legend' },
    h('span', { class: 'lg' }, 'Score 0', legendRamp, '1'),
    h('span', { class: 'lg' }, h('span', { class: 'box', style: { background: 'var(--surface-3)', border: '1px solid var(--text-2)' } }, icon('agent', 10)), 'Running (icon shows phase)'),
    h('span', { class: 'lg' }, h('span', { class: 'box', style: { background: 'var(--warning-wash)', border: '1px dashed var(--warning)' } }, icon('retry', 10)), 'Retrying'),
    h('span', { class: 'lg' }, h('span', { class: 'box', style: { background: 'var(--critical-wash)', border: '1px solid var(--critical)', color: 'var(--critical-text)' } }, icon('x', 10)), 'Gave up'),
    h('span', { class: 'lg' }, h('span', { class: 'box', style: { background: 'var(--surface-2)', border: '1px solid var(--border)' } }), 'Queued'));
  return { node, legend, update };
}
