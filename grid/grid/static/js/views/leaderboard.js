// Leaderboard: ranked table, task x model matrix, scatters, tool-usage profile, export.

import * as api from '../api.js';
import { store, taskById } from '../store.js';
import {
  h, icon, clear, fmtScore, fmtPct, fmtTokens, fmtDuration, fmtInt, modelColor, modelLabel, modelById,
  scoreColor, scoreInk, SERVERS, tip, tipBody, download, emptyState, modelSwatch, modelFill, modelAlt,
} from '../util.js';
import { scatter, ciMark } from '../charts.js';

export function render(root) {
  const body = h('div', null, h('div', { class: 'empty' }, h('span', { class: 'spinner' }), 'Loading leaderboard…'));
  let data = null;
  const exportCsv = h('button', { class: 'btn', type: 'button', onclick: () => data && download('leaderboard.csv', toCsv(data), 'text/csv') }, icon('download', 14), 'CSV');
  const exportJson = h('button', { class: 'btn', type: 'button', onclick: () => data && download('leaderboard.json', JSON.stringify(data, null, 2), 'application/json') }, icon('download', 14), 'JSON');
  // The API decides what counts; show its rules rather than our own copy.
  const rules = h('div', { class: 'sub' }, '');
  root.append(
    h('div', { class: 'page-head' },
      h('div', null, h('h1', null, 'Leaderboard'), rules),
      h('div', { class: 'actions' }, h('span', { class: 'panel-note' }, 'Export'), exportCsv, exportJson)),
    body);

  async function load() {
    try { data = await api.getLeaderboard(); } catch (err) { clear(body).append(emptyState('Could not load the leaderboard', String(err), 'warn')); return; }
    rules.textContent = data.rules || '';
    paint();
  }
  function paint() {
    if (!data.models.some((m) => m.runs > 0)) { clear(body).append(emptyState('No finished runs yet', 'Scores appear here once runs are graded.', 'grade')); return; }
    clear(body).append(
      h('section', { class: 'section' }, rankTable(data)),
      h('div', { class: 'grid-2 section' },
        h('section', { class: 'card' }, h('div', { class: 'panel-head' }, h('h2', null, 'Mean score by task and model'),
          h('span', { class: 'right panel-note' }, 'n = finished runs per cell')), matrix(data)),
        h('section', { class: 'card' }, h('div', { class: 'panel-head' }, h('h2', null, 'Tool usage profile'),
          h('span', { class: 'right panel-note' }, 'Share of each model\'s tool calls by server')), profile(data))),
      h('div', { class: 'grid-2 section' },
        h('section', { class: 'card' }, h('div', { class: 'panel-head' }, h('h2', null, 'Score vs average tokens')),
          scatter(data.models.filter((m) => m.runs).map((m) => point(m, m.avg_tokens)), { xTitle: 'Average tokens per run', yTitle: 'Mean score', xFormat: fmtTokens })),
        h('section', { class: 'card' }, h('div', { class: 'panel-head' }, h('h2', null, 'Score vs average duration')),
          scatter(data.models.filter((m) => m.runs).map((m) => point(m, m.avg_duration_s)), { xTitle: 'Average duration per run', yTitle: 'Mean score', xFormat: (v) => fmtDuration(v) }))));
  }

  load();
  const iv = setInterval(() => { if (store.summary && store.summary.state === 'running') load(); }, 3000);
  const onTheme = () => data && paint();
  window.addEventListener('themechange', onTheme);
  return () => { clearInterval(iv); window.removeEventListener('themechange', onTheme); };
}

function point(m, x) {
  return {
    x, y: m.mean_score, label: modelLabel(m.model_id), color: modelColor(m.model_id), alt: modelAlt(m.model_id),
    tip: [['Mean score', fmtScore(m.mean_score)], ['± 95% CI', m.ci95 === null ? '–' : fmtScore(m.ci95)], ['Avg tokens', fmtTokens(m.avg_tokens)],
      ['Avg duration', fmtDuration(m.avg_duration_s)], ['Runs', fmtInt(m.runs)]],
  };
}

function rankTable(data) {
  return h('div', { class: 'table-wrap' }, h('table', null,
    h('thead', null, h('tr', null,
      h('th', null, '#'), h('th', null, 'Model'), h('th', null, 'Mean score ± 95% CI'), h('th', { class: 'num' }, 'Pass rate'),
      h('th', { class: 'num' }, 'Runs'), h('th', { class: 'num' }, 'Avg tokens'), h('th', { class: 'num' }, 'Avg time'),
      h('th', { class: 'num', title: 'Extra attempts after infra or grading errors' }, 'Errors'),
      h('th', { class: 'num', title: 'Runs that gave up after max attempts; not scored' }, 'Gave up'),
      h('th', { class: 'num', title: 'Agent failed or timed out; scored against the model' }, 'Fails'))),
    h('tbody', null, data.models.map((m, i) => {
      const md = modelById(m.model_id);
      const ci = h('span', { class: 'ci' }, ciMark(m.mean_score, m.ci95, modelColor(m.model_id), 140, modelAlt(m.model_id)),
        h('b', { class: 'num' }, fmtScore(m.mean_score)), h('span', { class: 'muted num' }, m.ci95 === null ? '' : `± ${fmtScore(m.ci95)}`));
      tip(ci, () => tipBody(md.label, [['Mean', fmtScore(m.mean_score)], ['95% CI', m.ci95 === null ? '–' : `${fmtScore(m.mean_score - m.ci95)} – ${fmtScore(m.mean_score + m.ci95)}`], ['Runs', String(m.runs)]], modelFill(m.model_id)));
      return h('tr', null,
        h('td', { class: 'rank' }, i + 1),
        h('td', null, h('div', { class: 'chip', style: { color: 'var(--text)', fontWeight: 600 } }, modelSwatch(m.model_id), md.label),
          h('div', { class: 'mono muted', style: { fontSize: '11px' } }, md.model)),
        h('td', null, ci),
        h('td', { class: 'num' }, fmtPct(m.pass_rate)),
        h('td', { class: 'num' }, fmtInt(m.runs)),
        h('td', { class: 'num' }, fmtTokens(m.avg_tokens)),
        h('td', { class: 'num' }, fmtDuration(m.avg_duration_s)),
        h('td', { class: 'num' }, m.errors ? h('span', null, icon('retry', 12), ' ', fmtInt(m.errors)) : '0'),
        h('td', { class: 'num' }, fmtInt(m.gave_up || 0)),
        h('td', { class: 'num', style: m.fails ? { color: 'var(--critical-text)' } : null }, m.fails ? h('span', null, icon('x', 12), ' ', fmtInt(m.fails)) : '0'));
    }))));
}

function matrix(data) {
  const g = store.grid;
  const node = h('div', { class: 'matrix', style: { gridTemplateColumns: `minmax(190px, 2.2fr) repeat(${g.models.length}, 1fr)` } });
  node.append(h('div'));
  for (const m of g.models) node.append(h('div', { class: 'mh' }, modelSwatch(m.id), m.label.replace('Model ', '')));
  for (const t of g.tasks) {
    node.append(h('div', { class: 'ml', title: t.task_name }, t.task_name));
    for (const m of g.models) {
      const c = data.matrix.find((x) => x.task_id === t.task_id && x.model_id === m.id);
      const v = c ? c.mean_score : null;
      const cell = h('div', { class: 'mc-cell' + (v === null ? ' na' : ''), style: v === null ? null : { background: scoreColor(v), color: scoreInk(v) } }, v === null ? '–' : fmtScore(v));
      tip(cell, () => tipBody(`${m.label} · ${taskById(t.task_id).task_name}`, [['Mean score', fmtScore(v)], ['Finished runs', String(c ? c.n : 0)]], modelFill(m.id)));
      node.append(cell);
    }
  }
  return node;
}

function profile(data) {
  const g = store.grid;
  const node = h('div', { class: 'profile' });
  let maxShare = 0.01;
  for (const row of data.models) {
    const tot = Object.values(row.tool_profile || {}).reduce((a, b) => a + b, 0) || 1;
    for (const n of Object.values(row.tool_profile || {})) maxShare = Math.max(maxShare, n / tot);
  }
  node.append(h('div'));
  for (const sv of SERVERS) node.append(h('div', { class: 'ph' }, icon(sv.icon, 16), sv.label));
  for (const m of g.models) {
    const row = data.models.find((x) => x.model_id === m.id);
    const prof = (row && row.tool_profile) || {};
    const total = Object.values(prof).reduce((a, b) => a + b, 0) || 1;
    node.append(h('div', { class: 'pl' }, modelSwatch(m.id), m.label));
    for (const sv of SERVERS) {
      const n = prof[sv.id] || 0;
      const share = n / total;
      const cell = h('div', null,
        h('div', { class: 'pbar' }, h('i', { style: { height: `${n ? Math.max(4, (share / maxShare) * 100) : 0}%`, background: modelFill(m.id), maxHeight: '100%' } })),
        h('div', { class: 'pval' }, n ? fmtPct(share) : '·'));
      tip(cell, () => tipBody(`${m.label} → ${sv.label}`, [['Calls', fmtInt(n)], ['Share', fmtPct(share)]], modelFill(m.id)));
      node.append(cell);
    }
  }
  return node;
}

function toCsv(data) {
  const rows = [['rank', 'model_id', 'label', 'model', 'mean_score', 'ci95', 'pass_rate', 'runs', 'avg_tokens', 'avg_duration_s', 'errors', 'gave_up', 'fails']];
  data.models.forEach((m, i) => {
    const md = modelById(m.model_id) || {};
    rows.push([i + 1, m.model_id, md.label, md.model, m.mean_score ?? '', m.ci95 ?? '', m.pass_rate ?? '', m.runs, m.avg_tokens ?? '', m.avg_duration_s ?? '', m.errors, m.gave_up ?? 0, m.fails]);
  });
  rows.push([]);
  rows.push(['task_id', 'model_id', 'mean_score', 'n']);
  for (const c of data.matrix) rows.push([c.task_id, c.model_id, c.mean_score ?? '', c.n]);
  return rows.map((r) => r.map((v) => (/[",\n]/.test(String(v ?? '')) ? `"${String(v).replace(/"/g, '""')}"` : v ?? '')).join(',')).join('\n') + '\n';
}
