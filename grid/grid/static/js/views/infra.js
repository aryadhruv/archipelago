// Infra / Logs: infra events, retries and classifications; worker/port list.

import { store, on, taskById } from '../store.js';
import {
  h, icon, clear, fmtClock, modelColor, modelLabel, stateLabel, emptyState, fmtDuration,
} from '../util.js';

export function render(root) {
  const g = store.grid;
  const offs = [];
  const f = { kind: 'all', model: 'all', q: '' };

  const kindSel = h('select', { 'aria-label': 'Filter by type' },
    [['all', 'All types'], ['infra', 'Infra events'], ['retry', 'Retries'], ['gave_up', 'Gave up']].map(([v, l]) => h('option', { value: v }, l)));
  const modelSel = h('select', { 'aria-label': 'Filter by model' },
    h('option', { value: 'all' }, 'All models'), h('option', { value: 'none' }, 'No model (grid level)'),
    g.models.map((m) => h('option', { value: m.id }, m.label)));
  const search = h('input', { type: 'search', placeholder: 'Search text, run id, port…', 'aria-label': 'Search' });
  const count = h('span', { class: 'right' });
  kindSel.addEventListener('change', () => { f.kind = kindSel.value; paint(); });
  modelSel.addEventListener('change', () => { f.model = modelSel.value; paint(); });
  search.addEventListener('input', () => { f.q = search.value.trim().toLowerCase(); paint(); });

  const tbody = h('tbody');
  const workers = h('tbody');
  root.append(
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Infra and logs'),
      h('div', { class: 'sub' }, 'Sandbox health, provider errors, retries and how each failure was classified.'))),
    h('div', { class: 'row', style: { alignItems: 'flex-start' } },
      h('section', { style: { flex: '1 1 0', minWidth: 0 } },
        h('div', { class: 'filters' }, kindSel, modelSel, search, count),
        h('div', { class: 'table-wrap infra-table' }, h('table', null,
          h('thead', null, h('tr', null, h('th', null, 'Time'), h('th', null, 'Type'), h('th', null, 'Run'), h('th', null, 'Model'),
            h('th', null, 'Detail'), h('th', null, 'Classification'))),
          tbody))),
      h('aside', { style: { width: '380px', flex: 'none' } },
        h('h2', { class: 'section-title' }, 'Workers'),
        h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, h('th', null, 'Port'), h('th', null, 'Health'), h('th', null, 'Run'), h('th', null, 'State'))),
          workers)))));

  function rows() {
    const out = [];
    // From run rows: anything that needed more than one attempt or gave up.
    for (const r of store.runs) {
      if (r.state === 'gave_up') out.push({ ts: r.finished_at || r.started_at, type: 'gave_up', run: r, detail: `Gave up after ${r.attempt} attempts`, cls: r.classification });
      else if (r.state === 'retry') out.push({ ts: r.started_at, type: 'retry', run: r, detail: `Queued for attempt ${r.attempt}`, cls: r.classification });
      else if (r.attempt > 1 && r.state === 'scored') out.push({ ts: r.started_at, type: 'retry', run: r, detail: `Scored on attempt ${r.attempt}`, cls: 'scored' });
    }
    // From the live stream.
    for (const e of store.events) {
      const run = e.run_id ? store.runIndex.get(e.run_id) : null;
      if (e.kind === 'infra') out.push({ ts: e.ts, type: 'infra', run, detail: e.text, ok: e.ok, cls: run && e.ok === false ? run.classification : null });
      else if (e.kind === 'run_state' && (e.state === 'retry' || e.state === 'gave_up')) out.push({ ts: e.ts, type: e.state, run, detail: e.text, cls: run ? run.classification : null });
    }
    return out.sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
  }

  function paint() {
    const all = rows();
    const list = all.filter((x) => {
      if (f.kind !== 'all' && x.type !== f.kind) return false;
      if (f.model === 'none' && x.run) return false;
      if (f.model !== 'all' && f.model !== 'none' && (!x.run || x.run.model_id !== f.model)) return false;
      if (f.q) {
        const hay = `${x.detail || ''} ${x.run ? x.run.run_id : ''} ${x.cls || ''} ${x.type}`.toLowerCase();
        if (!hay.includes(f.q)) return false;
      }
      return true;
    });
    count.textContent = `${list.length} of ${all.length} rows`;
    clear(tbody);
    if (!list.length) {
      tbody.append(h('tr', null, h('td', { colspan: 6 }, emptyState('Nothing matches these filters', 'Infra events, retries and give-ups appear here.', 'box'))));
    }
    for (const x of list.slice(0, 300)) {
      const typePill = x.type === 'gave_up' ? h('span', { class: 'pill bad' }, icon('x', 12), 'gave up')
        : x.type === 'retry' ? h('span', { class: 'pill warn' }, icon('retry', 12), 'retry')
          : x.ok === false ? h('span', { class: 'pill bad' }, icon('warn', 12), 'infra')
            : x.ok === true ? h('span', { class: 'pill good' }, icon('check', 12), 'infra') : h('span', { class: 'pill' }, 'infra');
      tbody.append(h('tr', { class: x.run ? 'clickable' : '', onclick: x.run ? () => { location.hash = `#/run/${x.run.run_id}`; } : undefined },
        h('td', { class: 'num muted' }, x.ts ? fmtClock(x.ts) : '–'),
        h('td', null, typePill),
        h('td', { class: 'mono' }, x.run ? x.run.run_id : '–'),
        h('td', null, x.run ? h('span', { class: 'chip' }, h('span', { class: 'swatch', style: { background: modelColor(x.run.model_id) } }), modelLabel(x.run.model_id)) : h('span', { class: 'muted' }, 'grid')),
        h('td', { class: 'text-2' }, x.detail || '', x.run ? h('div', { class: 'muted', style: { fontSize: '11px' } }, taskById(x.run.task_id).task_name) : null),
        h('td', null, x.cls ? h('span', { class: 'mono', style: { fontSize: '12px' } }, x.cls) : h('span', { class: 'muted' }, '–'))));
    }
  }

  function paintWorkers() {
    const sm = store.summary;
    clear(workers);
    if (!sm) return;
    for (const w of sm.workers) {
      const r = w.run_id ? store.runIndex.get(w.run_id) : null;
      workers.append(h('tr', { class: r ? 'clickable' : '', onclick: r ? () => { location.hash = `#/run/${r.run_id}`; } : undefined },
        h('td', { class: 'mono' }, w.port),
        h('td', null, h('span', { class: 'chip' }, h('span', { class: 'health ' + (w.health || 'ok') }), w.health || 'ok')),
        h('td', null, r ? h('span', { class: 'chip' }, h('span', { class: 'swatch', style: { background: modelColor(r.model_id) } }), h('span', { class: 'mono' }, r.run_id)) : h('span', { class: 'muted' }, 'idle')),
        h('td', { class: 'text-2' }, r ? `${stateLabel(r.state)}${r.duration_s ? ' · ' + fmtDuration(r.duration_s) : ''}` : '–')));
    }
  }

  paint();
  paintWorkers();
  let pending = false;
  offs.push(on('event', (e) => {
    if (pending || !(e.kind === 'infra' || e.kind === 'run_state')) return;
    pending = true;
    setTimeout(() => { pending = false; paint(); }, 500);
  }));
  offs.push(on('summary', paintWorkers));
  return () => offs.forEach((x) => x());
}
