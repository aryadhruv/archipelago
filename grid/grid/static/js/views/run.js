// Run Detail: header, step timeline, token chart, final answer, grading, attempts.

import * as api from '../api.js';
import { store, on, taskById, worldById } from '../store.js';
import {
  h, icon, clear, fmtTokens, fmtDuration, fmtScore, fmtClock, fmtInt, modelColor, modelInk, modelLabel, serverLabel,
  shortTool, stateLabel, stateGroup, emptyState, scoreColor, scoreInk, tip, modelInitials,
} from '../util.js';
import { stackedBars, legend } from '../charts.js';

export function render(root, route) {
  const id = route.arg;
  const offs = [];
  const body = h('div', null, h('div', { class: 'empty' }, h('span', { class: 'spinner' }), 'Loading run…'));
  root.append(h('div', { style: { marginBottom: '12px' } }, h('a', { href: '#/live', class: 'btn btn-ghost' }, '← Mission Control')), body);

  let collapsed = new Set();
  let timer = null;
  async function load() {
    let run, events;
    try {
      [run, events] = await Promise.all([api.getRun(id), api.getRunEvents(id)]);
    } catch (err) {
      clear(body).append(emptyState('Could not load this run', String(err), 'warn'));
      return;
    }
    if (!run) { clear(body).append(emptyState(`No run called ${id}`, 'Check the link, or pick a run from Mission Control.', 'warn')); return; }
    paint(run, events || []);
  }

  function paint(run, events) {
    const task = taskById(run.task_id);
    const world = worldById(task.world_id);
    const grp = stateGroup(run.state);
    const pill = grp === 'scored' ? h('span', { class: 'pill good' }, icon('check', 12), 'Scored')
      : grp === 'gave_up' ? h('span', { class: 'pill bad' }, icon('x', 12), 'Gave up')
        : grp === 'retrying' ? h('span', { class: 'pill warn' }, icon('retry', 12), 'Retrying')
          : grp === 'running' ? h('span', { class: 'pill run' }, h('span', { class: 'spinner', style: { width: '10px', height: '10px' } }), stateLabel(run.state))
            : h('span', { class: 'pill' }, stateLabel(run.state));
    const fact = (k, v) => h('div', { class: 'fact' }, h('span', { class: 'k' }, k), h('span', { class: 'v' }, v));
    const replayBtn = h('a', { class: 'btn', href: `#/map?replay=${encodeURIComponent(run.run_id)}` }, icon('map', 14), 'Replay on map');
    if (!events.length) { replayBtn.classList.add('disabled'); replayBtn.removeAttribute('href'); replayBtn.setAttribute('aria-disabled', 'true'); }
    // A finished run can be run again with a fresh attempt budget.
    const retryBtn = (grp === 'scored' || grp === 'gave_up') && !api.MOCK
      ? h('button', { class: 'btn', type: 'button' }, icon('retry', 14), 'Run again')
      : null;
    if (retryBtn) {
      retryBtn.addEventListener('click', async () => {
        retryBtn.disabled = true;
        try { await api.retryRun(run.run_id); load(); } catch (err) { retryBtn.disabled = false; retryBtn.title = err.message; }
      });
    }

    const head = h('div', { class: 'run-head' },
      h('div', null,
        h('div', { class: 'run-title' },
          h('span', { class: 'model-chip', style: { background: modelColor(run.model_id), color: modelInk(run.model_id) } }, modelInitials(run.model_id)),
          h('h1', null, `${modelLabel(run.model_id)} · ${task.task_name}`), pill),
        h('div', { class: 'run-facts' },
          fact('Run', h('span', { class: 'mono' }, run.run_id)),
          fact('World', world.name),
          fact('Repeat', String(run.repeat)),
          fact('Attempt', `${run.attempt} of ${store.grid.max_attempts ?? 3}`),
          fact('Duration', fmtDuration(run.duration_s)),
          fact('Tokens', fmtTokens(run.tokens)),
          fact('Steps', fmtInt(run.steps)),
          fact('Tool calls', fmtInt(run.tool_calls)))),
      h('div', { style: { display: 'grid', gap: '12px', justifyItems: 'end' } },
        h('div', { class: 'score-big num' }, fmtScore(run.score), h('small', null, run.classification ? `classification: ${run.classification}` : 'not scored yet')),
        h('div', { style: { display: 'flex', gap: '8px' } }, retryBtn, replayBtn)));

    const note = run._synthetic
      ? h('div', { class: 'banner' }, icon('warn', 16), h('div', { class: 'grow muted' },
        'Mock mode: this run has no detailed fixture, so the trace below is what the simulator produced in this session. Runs r-0001, r-0022 and r-0011 have full fixtures.'))
      : null;

    // ---------- timeline ----------
    const groups = groupEvents(events);
    const tl = h('ol', { class: 'timeline' });
    if (!groups.length) tl.append(emptyState('No events yet', 'Events appear once the run starts.', 'clock'));
    for (const g of groups) {
      const key = g.key;
      const li = h('li', { class: 'tl-step' + (g.phase ? ' phase' : '') + (g.err ? ' err' : '') + (collapsed.has(key) ? ' collapsed' : '') });
      const headRow = h('div', { class: 'tl-head', role: 'button', tabindex: '0', 'aria-expanded': String(!collapsed.has(key)) },
        icon('play', 10, 'caret'), h('b', null, g.title), g.tokens ? h('span', { class: 'muted num' }, `${fmtTokens(g.tokens)} tokens`) : null,
        g.err ? h('span', { class: 'pill bad' }, icon('x', 12), `${g.err} error${g.err > 1 ? 's' : ''}`) : null,
        h('span', { class: 't' }, fmtClock(g.ts)));
      const toggle = () => {
        li.classList.toggle('collapsed');
        if (li.classList.contains('collapsed')) collapsed.add(key); else collapsed.delete(key);
        headRow.setAttribute('aria-expanded', String(!li.classList.contains('collapsed')));
      };
      headRow.addEventListener('click', toggle);
      headRow.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
      li.append(headRow, h('div', { class: 'tl-body' }, g.items.map(renderItem)));
      tl.append(li);
    }
    const expandBtn = h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => { collapsed = new Set(); paint(run, events); } }, 'Expand all');
    const collapseBtn = h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => { collapsed = new Set(groups.map((g) => g.key)); paint(run, events); } }, 'Collapse all');

    // ---------- token chart ----------
    const calls = (run.usage && run.usage.call_log) || [];
    const SERIES = [
      { key: 'prompt', label: 'Prompt', color: modelColorSlot(0) },
      { key: 'completion', label: 'Completion', color: modelColorSlot(1) },
      { key: 'reasoning', label: 'Reasoning', color: modelColorSlot(2) },
    ];
    const tokenCard = h('section', { class: 'card' },
      h('div', { class: 'panel-head' }, h('h2', null, 'Tokens per model call'),
        h('span', { class: 'right panel-note num' }, `${calls.length} calls · ${fmtTokens(calls.reduce((a, c) => a + c.prompt_tokens + c.completion_tokens + (c.reasoning_tokens || 0), 0))} total`)),
      calls.length ? [legend(SERIES), stackedBars(calls.map((c, i) => ({
        label: String(i + 1), values: { prompt: c.prompt_tokens, completion: c.completion_tokens, reasoning: c.reasoning_tokens || 0 },
      })), SERIES, { xTitle: 'Model call', height: 190 })] : emptyState('No usage recorded yet', null, 'clock'));

    // ---------- final answer ----------
    const answerCard = h('section', { class: 'card' }, h('div', { class: 'panel-head' }, h('h2', null, 'Final answer')),
      run.final_answer ? h('div', { class: 'answer' }, run.final_answer) : emptyState('No final answer', grp === 'gave_up' ? 'The agent never called final_answer.' : 'Not submitted yet.', 'doc'));

    // ---------- grading ----------
    const grades = run.grades;
    const crit = grades && grades.criteria ? grades.criteria : [];
    const passed = crit.filter((c) => c.passed).length;
    const gradeCard = h('section', { class: 'card' },
      h('div', { class: 'panel-head' }, h('h2', null, 'Grading'),
        grades ? h('span', { class: 'right panel-note' }, `${passed} of ${crit.length} criteria passed · ${grades.grading_run_status}`) : null),
      crit.length ? crit.map((c) => h('div', { class: 'crit' },
        h('span', { class: 'check-ic ' + (c.passed ? 'pass' : 'fail'), 'aria-label': c.passed ? 'passed' : 'failed' }, icon(c.passed ? 'check' : 'x', 14)),
        h('div', null, h('div', null, c.criteria), h('div', { class: 'vid' }, c.verifier_id)),
        h('span', { class: 'pill', style: { background: scoreColor(c.score), color: scoreInk(c.score) } }, fmtScore(c.score)),
        h('div', { class: 'why' }, c.rationale)))
        : emptyState('No grades', grp === 'gave_up' ? 'Grading was cancelled because the run gave up.' : 'Grading has not run yet.', 'grade'));

    // ---------- attempts ----------
    const attempts = run.attempts || [];
    const attemptCard = h('section', { class: 'card' }, h('div', { class: 'panel-head' }, h('h2', null, 'Attempts')),
      attempts.length ? h('table', null,
        h('thead', null, h('tr', null, h('th', null, '#'), h('th', null, 'Outcome'), h('th', null, 'Reason'), h('th', { class: 'num' }, 'Started'), h('th', { class: 'num' }, 'Took'))),
        h('tbody', null, attempts.map((a) => h('tr', null,
          h('td', { class: 'num' }, a.attempt),
          h('td', null, a.state === 'scored' ? h('span', { class: 'pill good' }, icon('check', 12), 'scored')
            : a.state === 'gave_up' ? h('span', { class: 'pill bad' }, icon('x', 12), 'gave up')
              : h('span', { class: 'pill warn' }, icon('retry', 12), a.state)),
          h('td', { class: 'text-2', style: { fontSize: '12px' } }, a.reason),
          h('td', { class: 'num muted' }, a.started_at ? fmtClock(a.started_at) : '–'),
          h('td', { class: 'num' }, a.started_at && a.finished_at ? fmtDuration((Date.parse(a.finished_at) - Date.parse(a.started_at)) / 1000) : '–')))))
        : emptyState('No attempts yet', null, 'clock'));

    clear(body).append(...[head, note].filter(Boolean),
      h('div', { class: 'run-cols' },
        h('section', { class: 'card' },
          h('div', { class: 'panel-head' }, h('h2', null, 'Timeline'), h('span', { class: 'panel-note' }, `${events.length} events`),
            h('div', { class: 'right' }, expandBtn, collapseBtn)), tl),
        h('div', { style: { display: 'grid', gap: '16px', minWidth: 0 } }, tokenCard, answerCard, gradeCard, attemptCard)));
  }

  load();
  offs.push(on('event', (e) => {
    if (e.run_id !== id) return;
    clearTimeout(timer);
    timer = setTimeout(load, 400);
  }));
  const onTheme = () => load();
  window.addEventListener('themechange', onTheme);
  return () => { offs.forEach((f) => f()); clearTimeout(timer); window.removeEventListener('themechange', onTheme); };
}

/** Token series take categorical slots 1..3 in fixed order (validated palette). */
function modelColorSlot(i) {
  const dark = document.documentElement.dataset.theme === 'dark';
  return (dark ? ['#3987e5', '#d95926', '#199e70'] : ['#2a78d6', '#eb6834', '#1baf7a'])[i];
}

function groupEvents(events) {
  const groups = [];
  let cur = null;
  const results = new Map(events.filter((e) => e.kind === 'tool_result').map((e) => [e.ref, e]));
  const open = (title, ts, phase, key) => { cur = { title, ts, phase, key, items: [], err: 0, tokens: 0 }; groups.push(cur); };
  for (const e of events) {
    if (e.kind === 'step') { open(e.text || 'Step', e.ts, false, 'step-' + e.seq); cur.tokens = e.tokens || 0; continue; }
    if (e.kind === 'run_state' && ['env_starting'].includes(e.state)) { open('Setup', e.ts, true, 'setup-' + e.seq); }
    if (e.kind === 'run_state' && ['snapshotting', 'grading'].includes(e.state) && (!cur || cur.title !== 'Grading')) { open('Grading', e.ts, true, 'grading-' + e.seq); }
    if (e.kind === 'run_state' && ['retry', 'gave_up'].includes(e.state) && cur && cur.phase === false) { open('Outcome', e.ts, true, 'outcome-' + e.seq); }
    if (!cur) open('Setup', e.ts, true, 'setup-0');
    if (e.kind === 'tool_result') continue; // merged into its call
    if (e.kind === 'tool_call') {
      const r = results.get(e.ref);
      if (r && !r.ok) cur.err += 1;
      cur.items.push({ ...e, result: r });
    } else cur.items.push(e);
  }
  return groups;
}

function renderItem(e) {
  switch (e.kind) {
    case 'reasoning': return h('div', { class: 'tl-reason' }, `“${e.text}”`);
    case 'tool_call': {
      const r = e.result;
      return h('div', { class: 'tl-call' },
        h('div', { class: 'top' },
          h('span', { class: 'server-tag' }, serverLabel(e.server)), h('span', { class: 'tool' }, shortTool(e.tool, e.server)),
          r ? h('span', { class: 'pill ' + (r.ok ? 'good' : 'bad') }, icon(r.ok ? 'check' : 'x', 12), r.ok ? 'ok' : 'error')
            : h('span', { class: 'pill run' }, h('span', { class: 'spinner', style: { width: '10px', height: '10px' } }), 'waiting'),
          r ? h('span', { class: 'ms' }, `${fmtInt(r.duration_ms)} ms`) : null),
        e.args_preview && e.args_preview !== '{}' ? h('div', { class: 'args' }, e.args_preview) : null,
        r && r.text ? h('div', { class: 'res', style: r.ok ? null : { color: 'var(--critical-text)' } }, '→ ' + r.text) : null);
    }
    case 'run_state': return h('div', { class: 'tl-note' }, h('b', null, stateLabel(e.state)), e.text ? h('span', { class: 'muted' }, ' · ' + e.text) : null);
    case 'grading_progress': return h('div', { class: 'tl-note' }, icon(e.ok ? 'check' : 'x', 12), ' ', e.text);
    case 'grade': return h('div', { class: 'tl-note' }, h('b', null, e.text));
    case 'final_answer': return h('div', { class: 'tl-note' }, h('b', null, 'Final answer submitted'));
    case 'infra': return h('div', { class: 'tl-note', style: { color: e.ok === false ? 'var(--critical-text)' : undefined } }, icon('warn', 12), ' ', e.text);
    case 'toolbelt': return h('div', { class: 'tl-note' }, icon('toolbelt', 12), ' ', e.text);
    case 'compaction': return h('div', { class: 'tl-note muted' }, icon('box', 12), ' ', e.text);
    case 'response': return h('div', { class: 'tl-note' }, e.text);
    default: return h('div', { class: 'tl-note muted' }, `${e.kind}: ${e.text || ''}`);
  }
}
