// Live Map: model agents on the left, MCP servers on the right. Each tool call
// sends a packet agent -> server; the result returns green (ok) or red (error).
// Modes: live (all or one model), focus (one live run + world file tree),
// replay (a finished run's events at 1x / 4x / 16x).

import * as api from '../api.js';
import { store, on, taskById, worldById } from '../store.js';
import {
  h, s, icon, clear, ICONS, SERVERS, modelColor, modelInk, modelLabel, serverLabel, shortTool, showTip, moveTip, hideTip,
  tipBody, pathFromArgs, reducedMotion, fmtInt, fmtClock, stateLabel, stateGroup,
} from '../util.js';

const W = 1200, H = 680;
const AX = 210, SX = 960; // agent column x, server column x
const MAX_PARTICLES = 90;
const OUT_MS = 950, BACK_MS = 850;

export function render(root, route) {
  const g = store.grid;
  const q = route.query;
  const offs = [];
  const st = {
    mode: q.get('replay') ? 'replay' : q.get('run') ? 'focus' : 'live',
    model: q.get('model') || 'all',
    focusRun: q.get('run') || null,
    replayRun: q.get('replay') || null,
    speed: 4,
    playing: true,
  };

  // ---------- layout ----------
  const models = g.models;
  const agentPos = new Map(models.map((m, i) => [m.id, { x: AX, y: 120 + (i * (H - 200)) / Math.max(1, models.length - 1) }]));
  const serverPos = new Map(SERVERS.map((sv, i) => {
    const t = i / (SERVERS.length - 1);
    return [sv.id, { x: SX - Math.sin(t * Math.PI) * 46, y: 64 + t * (H - 118) }];
  }));

  const svg = s('svg', { class: 'map', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Live map of tool calls from model agents to MCP servers' });
  const defs = s('defs');
  const glow = s('filter', { id: 'glow', x: '-50%', y: '-50%', width: '200%', height: '200%' });
  glow.append(s('feGaussianBlur', { stdDeviation: '3.2', result: 'b' }));
  const merge = s('feMerge');
  merge.append(s('feMergeNode', { in: 'b' }), s('feMergeNode', { in: 'SourceGraphic' }));
  glow.append(merge);
  defs.append(glow);
  svg.append(defs);
  svg.append(s('text', { class: 'col-title', x: AX, y: 36, 'text-anchor': 'middle' }, 'Agents'));
  svg.append(s('text', { class: 'col-title', x: SX, y: 36, 'text-anchor': 'middle' }, 'MCP servers'));

  const gEdges = s('g'), gHits = s('g'), gParticles = s('g', { filter: 'url(#glow)' }), gNodes = s('g');
  svg.append(gEdges, gHits, gParticles, gNodes);

  // edges
  const edges = new Map(); // `${model}|${server}` -> {path, len, count, last}
  for (const m of models) {
    const a = agentPos.get(m.id);
    for (const sv of SERVERS) {
      const b = serverPos.get(sv.id);
      const x1 = a.x + 32, x2 = b.x - 26;
      const d = `M${x1},${a.y} C${x1 + 260},${a.y} ${x2 - 260},${b.y} ${x2},${b.y}`;
      const path = s('path', { class: 'edge', d });
      const hit = s('path', { class: 'edge-hit', d });
      gEdges.append(path);
      gHits.append(hit);
      const e = { path, hit, len: 0, count: 0, last: null, model: m.id, server: sv.id };
      edges.set(`${m.id}|${sv.id}`, e);
      hit.addEventListener('mouseenter', (ev) => showTip(ev, edgeTip(e)));
      hit.addEventListener('mousemove', moveTip);
      hit.addEventListener('mouseleave', hideTip);
    }
  }

  // server hubs
  const hubs = new Map();
  for (const sv of SERVERS) {
    const p = serverPos.get(sv.id);
    const grp = s('g', { transform: `translate(${p.x},${p.y})`, style: 'cursor:help' });
    const ping = s('circle', { class: 'hub-ring', r: 24 });
    const busy = s('circle', { class: 'busy-ring', r: 27, stroke: 'var(--text-2)', 'stroke-opacity': 0 });
    const bg = s('circle', { class: 'hub-bg', r: 22 });
    const ic = s('g', { class: 'hub-icon', transform: 'translate(-11,-11) scale(0.92)' });
    for (const d of ICONS[sv.icon]) ic.append(s('path', { d }));
    const label = s('text', { class: 'node-label', x: 36, y: -2 }, sv.label);
    const sub = s('text', { class: 'node-sub', x: 36, y: 14 }, '0 calls/min');
    grp.append(ping, busy, bg, ic, label, sub);
    gNodes.append(grp);
    const hub = { grp, ping, busy, sub, times: [], total: 0, inflight: 0, tools: new Map(), pingAt: -1e9, errAt: -1e9 };
    hubs.set(sv.id, hub);
    grp.addEventListener('mouseenter', (ev) => showTip(ev, hubTip(sv, hub)));
    grp.addEventListener('mousemove', moveTip);
    grp.addEventListener('mouseleave', hideTip);
  }

  // agent nodes
  const agents = new Map();
  for (const m of models) {
    const p = agentPos.get(m.id);
    const grp = s('g', { transform: `translate(${p.x},${p.y})`, style: 'cursor:pointer' });
    const ring = s('circle', { class: 'busy-ring', r: 33, stroke: modelColor(m.id), 'stroke-opacity': 0 });
    const bg = s('circle', { class: 'agent-bg', r: 26, fill: modelColor(m.id) });
    const ic = s('g', { class: 'agent-icon', transform: 'translate(-12,-12)' });
    for (const d of ICONS.agent) ic.append(s('path', { d }));
    if (modelInk(m.id) !== '#ffffff') ic.setAttribute('style', 'stroke:' + modelInk(m.id));
    const label = s('text', { class: 'node-label', x: -42, y: -2, 'text-anchor': 'end' }, m.label);
    const sub = s('text', { class: 'node-sub', x: -42, y: 14, 'text-anchor': 'end' }, '');
    grp.append(ring, bg, ic, label, sub);
    gNodes.append(grp);
    const ag = { grp, ring, bg, sub, calls: 0 };
    agents.set(m.id, ag);
    grp.addEventListener('click', () => setModel(st.model === m.id ? 'all' : m.id));
    grp.addEventListener('mouseenter', (ev) => showTip(ev, agentTip(m, ag)));
    grp.addEventListener('mousemove', moveTip);
    grp.addEventListener('mouseleave', hideTip);
  }

  // ---------- controls ----------
  const modeSeg = h('div', { class: 'seg', role: 'tablist' });
  const modelSel = h('select', { 'aria-label': 'Filter by model' },
    h('option', { value: 'all' }, 'All models'), models.map((m) => h('option', { value: m.id }, m.label)));
  const runSel = h('select', { 'aria-label': 'Run' });
  const speedSeg = h('div', { class: 'seg', 'aria-label': 'Replay speed' });
  const playBtn = h('button', { class: 'btn btn-ghost', type: 'button' });
  const track = h('div', { class: 'track' }, h('i'));
  const replayInfo = h('span', { class: 'muted num' });
  const replayBar = h('div', { class: 'replay-bar', hidden: true }, playBtn, speedSeg, track, replayInfo);
  const banner = h('div', { class: 'banner', hidden: true }, icon('play', 18),
    h('div', { class: 'grow' }, h('b', null, 'The grid is not running. '),
      h('span', { class: 'muted' }, 'Start it to see live traffic, or switch to Replay to play back a finished run.')),
    h('button', { class: 'btn btn-primary', type: 'button', onclick: () => api.startGrid() }, icon('play', 14), 'Start grid'));

  for (const [k, label] of [['live', 'Live'], ['focus', 'Focus a run'], ['replay', 'Replay']]) {
    const b = h('button', { type: 'button', role: 'tab' }, label);
    b.dataset.mode = k;
    b.addEventListener('click', () => setMode(k));
    modeSeg.append(b);
  }
  for (const sp of [1, 4, 16]) {
    const b = h('button', { type: 'button' }, `${sp}×`);
    b.dataset.speed = String(sp);
    b.addEventListener('click', () => { st.speed = sp; paintControls(); });
    speedSeg.append(b);
  }
  playBtn.addEventListener('click', () => {
    if (replay && replay.done) startReplay(st.replayRun);
    else { st.playing = !st.playing; paintControls(); }
  });
  modelSel.addEventListener('change', () => setModel(modelSel.value));
  runSel.addEventListener('change', () => {
    if (st.mode === 'focus') { st.focusRun = runSel.value || null; resetFocus(); }
    if (st.mode === 'replay') { st.replayRun = runSel.value || null; startReplay(st.replayRun); }
    syncHash();
  });

  const legend = h('div', { class: 'map-legend' },
    h('span', { class: 'lg' }, h('span', { class: 'dotk', style: { background: 'var(--text-2)' } }), 'Call (packet in the model\'s color)'),
    h('span', { class: 'lg' }, h('span', { class: 'dotk', style: { background: 'var(--packet-ok)' } }), icon('check', 12), 'Result ok'),
    h('span', { class: 'lg' }, h('span', { class: 'dotk', style: { background: 'var(--packet-err)' } }), icon('x', 12), 'Result error'),
    h('span', { class: 'lg muted' }, 'Edge width = total calls · ring = calls in flight · hover anything for details'));

  // side panel (focus/replay)
  const panelTitle = h('div', { class: 'panel-head' }, h('h2', null, 'World files'));
  const panelSub = h('div', { class: 'panel-note', style: { marginBottom: '8px' } });
  const tree = h('div', { class: 'filetree' });
  const feed = h('ul', { class: 'focus-feed' });
  const panel = h('aside', { class: 'card', hidden: true }, panelTitle, panelSub, tree, feed);

  const mapWrap = h('div', { class: 'map-wrap' },
    h('section', { class: 'card map-card' },
      h('div', { class: 'map-toolbar' }, modeSeg, modelSel, runSel,
        h('div', { class: 'right' }, h('span', { class: 'panel-note', id: 'map-rate' }, ''))),
      replayBar, svg, legend),
    panel);

  root.append(
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Live Map'),
      h('div', { class: 'sub' }, 'Every tool call an agent makes, as it happens.'))),
    banner, mapWrap);

  // ---------- state ----------
  const particles = [];
  const pending = new Map(); // ref key -> {server, model}
  let replay = null;
  const fileHits = new Map(); // path -> {hits, err, at}
  let worldFiles = [];
  let focusWorld = null;
  const motion = !reducedMotion();
  const rateEl = mapWrap.querySelector('#map-rate');

  // Seed edge widths from cumulative tool usage so the map shows history immediately.
  api.getLeaderboard().then((lb) => {
    for (const row of lb.models) {
      for (const [srv, n] of Object.entries(row.tool_profile || {})) {
        const e = edges.get(`${row.model_id}|${srv}`);
        if (e) e.count += n;
      }
    }
    paintEdges();
  });

  function accept(e) {
    if (!e.model_id) return false;
    if (st.mode === 'live') return st.model === 'all' || e.model_id === st.model;
    if (st.mode === 'focus') return e.run_id === st.focusRun;
    return false;
  }

  function handle(e, fromReplay = false) {
    if (!fromReplay && !accept(e)) return;
    if (e.kind === 'tool_call' && e.server) {
      const key = `${e.run_id}|${e.ref}`;
      pending.set(key, { server: e.server, model: e.model_id });
      const edge = edges.get(`${e.model_id}|${e.server}`);
      if (edge) {
        edge.count += 1;
        edge.last = e;
        spawn(edge, 'out', modelColor(e.model_id));
      }
      const hub = hubs.get(e.server);
      if (hub) {
        hub.times.push(performance.now());
        hub.total += 1;
        hub.inflight += 1;
        hub.pingAt = performance.now();
        const t = shortTool(e.tool, e.server);
        hub.tools.set(t, (hub.tools.get(t) || 0) + 1);
      }
      const ag = agents.get(e.model_id);
      if (ag) ag.calls += 1;
      const p = e.path || pathFromArgs(e.args_preview);
      if (p && (st.mode !== 'live')) touchFile(p, false);
      paintEdgeWidth(edge);
    } else if (e.kind === 'tool_result' && e.server) {
      const key = `${e.run_id}|${e.ref}`;
      pending.delete(key);
      const edge = edges.get(`${e.model_id}|${e.server}`);
      if (edge) spawn(edge, 'back', e.ok ? 'var(--packet-ok)' : 'var(--packet-err)');
      const hub = hubs.get(e.server);
      if (hub) {
        hub.inflight = Math.max(0, hub.inflight - 1);
        if (!e.ok) hub.errAt = performance.now();
      }
      if (!e.ok && st.mode !== 'live' && edge && edge.last) {
        const p = edge.last.path || pathFromArgs(edge.last.args_preview);
        if (p) touchFile(p, true);
      }
    }
    if (st.mode !== 'live' && (e.kind === 'tool_call' || e.kind === 'tool_result' || e.kind === 'run_state' || e.kind === 'grade' || e.kind === 'final_answer')) {
      pushFeed(e);
    }
  }

  function spawn(edge, dir, color) {
    if (!motion) {
      edge.path.style.stroke = dir === 'out' ? modelColor(edge.model) : color;
      edge.path.style.strokeOpacity = 0.9;
      setTimeout(() => { edge.path.style.stroke = ''; edge.path.style.strokeOpacity = ''; paintEdgeWidth(edge); }, 500);
      return;
    }
    if (particles.length >= MAX_PARTICLES) {
      const old = particles.shift();
      old.node.remove();
    }
    if (!edge.len) edge.len = edge.path.getTotalLength();
    const node = s('circle', { r: dir === 'out' ? 4.5 : 4, fill: color });
    gParticles.append(node);
    const speedScale = st.mode === 'replay' ? Math.max(0.35, 1 / Math.sqrt(st.speed)) : 1;
    particles.push({ node, edge, dir, t0: performance.now(), dur: (dir === 'out' ? OUT_MS : BACK_MS) * speedScale });
  }

  // ---------- painting ----------
  function maxCount() { let m = 1; for (const e of edges.values()) m = Math.max(m, e.count); return m; }
  function paintEdgeWidth(edge, mc = maxCount()) {
    if (!edge) return;
    const n = Math.sqrt(edge.count / mc);
    edge.path.style.strokeWidth = (0.6 + n * 5).toFixed(2);
    edge.path.style.stroke = edge.count ? modelColor(edge.model) : '';
    edge.path.style.strokeOpacity = edge.count ? (0.05 + n * 0.30).toFixed(2) : '';
  }
  function paintEdges() {
    const mc = maxCount();
    for (const e of edges.values()) paintEdgeWidth(e, mc);
    paintDim();
  }
  function paintDim() {
    const focusModel = st.mode === 'live' ? (st.model === 'all' ? null : st.model)
      : st.mode === 'focus' ? (store.runIndex.get(st.focusRun) || {}).model_id
        : replay ? replay.model : null;
    for (const e of edges.values()) {
      const on = !focusModel || e.model === focusModel;
      e.path.classList.toggle('dim', !on);
      e.hit.style.display = on ? '' : 'none';
    }
    for (const [id, ag] of agents) ag.grp.classList.toggle('dim', !!focusModel && id !== focusModel);
  }
  function paintNodes(now) {
    let totalRate = 0;
    for (const [id, hub] of hubs) {
      while (hub.times.length && now - hub.times[0] > 60000) hub.times.shift();
      const rate = hub.times.length;
      totalRate += rate;
      hub.sub.textContent = `${fmtInt(rate)} calls/min`;
      // ping: expanding ring after each call
      const pt = (now - hub.pingAt) / 700;
      if (pt >= 0 && pt < 1 && motion) {
        hub.ping.setAttribute('r', (22 + pt * 16).toFixed(1));
        hub.ping.style.strokeOpacity = String((1 - pt) * 0.7);
        hub.ping.style.stroke = now - hub.errAt < 700 ? 'var(--packet-err)' : 'var(--text-2)';
      } else hub.ping.style.strokeOpacity = '0';
      hub.busy.style.strokeOpacity = hub.inflight ? String(Math.min(0.9, 0.3 + hub.inflight * 0.2)) : '0';
      hub.busy.style.strokeWidth = String(Math.min(5, 1.5 + hub.inflight));
    }
    for (const [id, ag] of agents) {
      const running = store.runs.filter((r) => r.model_id === id && stateGroup(r.state) === 'running').length;
      ag.sub.textContent = `${running} running · ${fmtInt(ag.calls)} calls`;
      ag.ring.style.strokeOpacity = running && st.mode === 'live' ? '0.45' : '0';
    }
    if (rateEl) rateEl.textContent = st.mode === 'replay' ? '' : `${fmtInt(totalRate)} calls in the last minute`;
  }

  let raf = 0, lastFrame = performance.now(), nodeTick = 0;
  function frame(now) {
    const dt = now - lastFrame;
    lastFrame = now;
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      const t = (now - p.t0) / p.dur;
      if (t >= 1) { p.node.remove(); particles.splice(i, 1); continue; }
      const ease = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      const at = p.dir === 'out' ? ease : 1 - ease;
      const pt = p.edge.path.getPointAtLength(at * p.edge.len);
      p.node.setAttribute('cx', pt.x.toFixed(1));
      p.node.setAttribute('cy', pt.y.toFixed(1));
    }
    if (replay && st.playing && !replay.done) stepReplay(dt);
    if (now - nodeTick > 120) { nodeTick = now; paintNodes(now); }
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  // ---------- focus panel ----------
  async function loadWorldFor(runId, events) {
    const r = store.runIndex.get(runId) || (events && events[0] ? { task_id: events[0].task_id, model_id: events[0].model_id } : null);
    if (!r) { panel.hidden = true; mapWrap.classList.remove('focus'); return; }
    const task = taskById(r.task_id);
    const world = worldById(task.world_id);
    focusWorld = world;
    panel.hidden = false;
    mapWrap.classList.add('focus');
    panelSub.textContent = `${runId} · ${modelLabel(r.model_id)} · ${task.task_name} · ${world.name}`;
    try {
      const wf = await api.getWorldFiles(world.world_id);
      worldFiles = wf.files.map((f) => f.path);
    } catch { worldFiles = []; }
    paintTree();
  }
  function touchFile(p, err) {
    const f = fileHits.get(p) || { hits: 0, err: false, at: 0 };
    if (!err) f.hits += 1;
    f.err = err || f.err;
    f.at = performance.now();
    fileHits.set(p, f);
    if (!worldFiles.includes(p)) worldFiles.push(p);
    paintTree(p);
  }
  function paintTree(flashPath) {
    const rootNode = { children: new Map(), path: '' };
    for (const p of [...worldFiles].sort()) {
      const parts = p.split('/').filter(Boolean);
      let cur = rootNode;
      parts.forEach((part, i) => {
        if (!cur.children.has(part)) cur.children.set(part, { children: new Map(), path: '/' + parts.slice(0, i + 1).join('/'), file: i === parts.length - 1 });
        cur = cur.children.get(part);
      });
    }
    const build = (n) => h('ul', null, [...n.children.entries()].map(([name, c]) => {
      const f = fileHits.get(c.path);
      const row = h('div', { class: 'ft-row' + (c.file ? '' : ' dir') + (f ? ' touched' : '') + (f && f.err ? ' err' : '') + (flashPath === c.path ? ' flash' : '') },
        icon(c.file ? 'file' : 'folder', 14), name, f && f.hits ? h('span', { class: 'hits' }, `${f.hits}×`) : null);
      if (flashPath === c.path) setTimeout(() => row.classList.remove('flash'), 700);
      return h('li', null, row, c.file ? null : build(c));
    }));
    clear(tree).append(worldFiles.length ? build(rootNode) : h('div', { class: 'muted' }, 'No file list for this world.'));
  }
  function pushFeed(e) {
    let txt;
    if (e.kind === 'tool_call') txt = `→ ${shortTool(e.tool, e.server)} ${e.args_preview || ''}`;
    else if (e.kind === 'tool_result') txt = `${e.ok ? '✓' : '✕'} ${shortTool(e.tool, e.server)} · ${e.duration_ms} ms`;
    else if (e.kind === 'run_state') txt = `● ${stateLabel(e.state)}`;
    else txt = `● ${e.text || e.kind}`;
    feed.prepend(h('li', { class: e.ok === false ? 'err' : '', title: txt, style: e.ok === false ? { color: 'var(--critical-text)' } : null },
      h('span', { class: 'muted num' }, fmtClock(e.ts) + '  '), txt));
    while (feed.children.length > 14) feed.lastChild.remove();
  }
  function resetFocus() {
    fileHits.clear();
    clear(feed);
    clearParticles();
    for (const hub of hubs.values()) hub.inflight = 0;
    if (st.focusRun) loadWorldFor(st.focusRun).then(() => seedFocus(st.focusRun));
    else { panel.hidden = true; mapWrap.classList.remove('focus'); }
    paintDim();
  }
  /** Mark what a run already touched before focus mode opened on it. */
  async function seedFocus(id) {
    let events;
    try { events = await api.getRunEvents(id); } catch { return; }
    if (st.focusRun !== id) return;
    const calls = new Map();
    for (const e of events) {
      if (e.kind === 'tool_call') {
        calls.set(e.ref, e);
        const p = e.path || pathFromArgs(e.args_preview);
        if (p) { const f = fileHits.get(p) || { hits: 0, err: false, at: 0 }; f.hits += 1; fileHits.set(p, f); }
      } else if (e.kind === 'tool_result' && !e.ok && calls.has(e.ref)) {
        const c = calls.get(e.ref);
        const p = c.path || pathFromArgs(c.args_preview);
        if (p && fileHits.has(p)) fileHits.get(p).err = true;
      }
    }
    for (const p of fileHits.keys()) if (!worldFiles.includes(p)) worldFiles.push(p);
    paintTree();
    for (const e of events.filter((x) => ['tool_call', 'tool_result', 'run_state', 'grade', 'final_answer'].includes(x.kind)).slice(-14)) pushFeed(e);
  }
  function clearParticles() {
    for (const p of particles) p.node.remove();
    particles.length = 0;
  }

  // ---------- replay ----------
  async function startReplay(id) {
    clearParticles();
    fileHits.clear();
    clear(feed);
    for (const hub of hubs.values()) { hub.inflight = 0; hub.times = []; }
    replay = null;
    if (!id) { paintControls(); return; }
    const events = await api.getRunEvents(id);
    if (!events.length) { paintControls(); return; }
    const t0 = Date.parse(events[0].ts);
    replay = { id, events, t0, span: Date.parse(events[events.length - 1].ts) - t0, clock: 0, idx: 0, done: false, model: events[0].model_id };
    st.playing = true;
    await loadWorldFor(id, events);
    paintDim();
    paintControls();
  }
  function stepReplay(dt) {
    replay.clock += dt * st.speed;
    while (replay.idx < replay.events.length && Date.parse(replay.events[replay.idx].ts) - replay.t0 <= replay.clock) {
      handle(replay.events[replay.idx], true);
      replay.idx += 1;
    }
    if (replay.idx >= replay.events.length) { replay.done = true; paintControls(); }
    track.firstChild.style.width = `${Math.min(100, (100 * replay.clock) / Math.max(1, replay.span))}%`;
    replayInfo.textContent = `${Math.min(replay.idx, replay.events.length)} / ${replay.events.length} events · ${fmtSecs(Math.min(replay.clock, replay.span))} of ${fmtSecs(replay.span)}`;
  }
  const fmtSecs = (ms) => `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, '0')}`;

  // ---------- controls painting ----------
  async function fillRunSelect() {
    clear(runSel);
    if (st.mode === 'focus') {
      const live = store.runs.filter((r) => stateGroup(r.state) === 'running');
      runSel.append(h('option', { value: '' }, live.length ? 'Choose a running run…' : 'No runs in progress'));
      for (const r of live) runSel.append(h('option', { value: r.run_id }, `${r.run_id} · ${modelLabel(r.model_id)} · ${taskById(r.task_id).task_name}`));
      if (st.focusRun && !live.find((r) => r.run_id === st.focusRun)) {
        const r = store.runIndex.get(st.focusRun);
        if (r) runSel.append(h('option', { value: r.run_id }, `${r.run_id} · ${modelLabel(r.model_id)} (${stateLabel(r.state)})`));
      }
      runSel.value = st.focusRun || '';
    } else if (st.mode === 'replay') {
      const ids = new Set(await api.detailedRunIds());
      for (const r of store.runs) if (r.state === 'scored' && ids.size < 40 && (await hasEvents(r.run_id))) ids.add(r.run_id);
      runSel.append(h('option', { value: '' }, 'Choose a finished run…'));
      for (const id of ids) {
        const r = store.runIndex.get(id);
        runSel.append(h('option', { value: id }, r ? `${id} · ${modelLabel(r.model_id)} · ${taskById(r.task_id).task_name}` : id));
      }
      runSel.value = st.replayRun || '';
    }
  }
  const eventsKnown = new Set();
  async function hasEvents(id) {
    if (eventsKnown.has(id)) return true;
    if (!store.events.some((e) => e.run_id === id && e.kind === 'run_state' && e.state === 'env_starting')) return false;
    eventsKnown.add(id);
    return true;
  }

  function paintControls() {
    for (const b of modeSeg.children) { b.classList.toggle('on', b.dataset.mode === st.mode); b.setAttribute('aria-selected', String(b.dataset.mode === st.mode)); }
    for (const b of speedSeg.children) b.classList.toggle('on', Number(b.dataset.speed) === st.speed);
    modelSel.hidden = st.mode !== 'live';
    modelSel.value = st.model;
    runSel.hidden = st.mode === 'live';
    replayBar.hidden = st.mode !== 'replay' || !replay;
    clear(playBtn).append(icon(replay && replay.done ? 'retry' : st.playing ? 'pause' : 'play', 14),
      replay && replay.done ? 'Replay again' : st.playing ? 'Pause' : 'Play');
    banner.hidden = !(store.summary && store.summary.state === 'idle' && st.mode !== 'replay');
  }

  function setMode(mode) {
    st.mode = mode;
    clearParticles();
    pending.clear();
    if (mode === 'live') {
      replay = null;
      panel.hidden = true;
      mapWrap.classList.remove('focus');
      for (const hub of hubs.values()) hub.inflight = 0;
    } else if (mode === 'focus') {
      replay = null;
      if (!st.focusRun) {
        const r = store.runs.find((x) => x.state === 'agent_running');
        st.focusRun = r ? r.run_id : null;
      }
      resetFocus();
    } else if (mode === 'replay') {
      if (!st.replayRun) st.replayRun = 'r-0001';
      startReplay(st.replayRun);
    }
    fillRunSelect();
    paintControls();
    paintDim();
    syncHash();
  }
  function setModel(id) {
    if (st.mode !== 'live') setMode('live');
    st.model = id;
    paintControls();
    paintDim();
    syncHash();
  }
  function syncHash() {
    const p = new URLSearchParams();
    if (st.mode === 'live' && st.model !== 'all') p.set('model', st.model);
    if (st.mode === 'focus' && st.focusRun) p.set('run', st.focusRun);
    if (st.mode === 'replay' && st.replayRun) p.set('replay', st.replayRun);
    const hash = '#/map' + (p.toString() ? '?' + p : '');
    if (location.hash !== hash) history.replaceState(null, '', hash);
  }

  // ---------- tooltips ----------
  function edgeTip(e) {
    const last = e.last;
    return h('div', null, tipBody(`${modelLabel(e.model)} → ${serverLabel(e.server)}`, [
      ['Total calls', fmtInt(e.count)],
      ['Last tool', last ? shortTool(last.tool, last.server) : '–'],
    ], modelColor(e.model)), last && last.args_preview ? h('div', { class: 'tt-code' }, last.args_preview) : null);
  }
  function hubTip(sv, hub) {
    const top = [...hub.tools.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
    return tipBody(sv.label, [
      ['Server', sv.id], ['Calls/min', fmtInt(hub.times.length)], ['In flight', fmtInt(hub.inflight)], ['Calls seen', fmtInt(hub.total)],
      ...top.map(([t, n]) => [t, fmtInt(n)]),
    ]);
  }
  function agentTip(m, ag) {
    const running = store.runs.filter((r) => r.model_id === m.id && stateGroup(r.state) === 'running');
    return tipBody(m.label, [['Model', m.model], ['Running', String(running.length)], ['Calls seen', fmtInt(ag.calls)], ['Click', 'filter to this model']], modelColor(m.id));
  }

  // ---------- wiring ----------
  offs.push(on('event', (e) => {
    if (st.mode === 'replay') return;
    handle(e);
  }));
  offs.push(on('summary', () => {
    paintControls();
    if (st.mode === 'focus' && runSel.options.length <= 1) fillRunSelect();
  }));
  const onTheme = () => {
    for (const [id, ag] of agents) { ag.bg.setAttribute('fill', modelColor(id)); ag.ring.setAttribute('stroke', modelColor(id)); }
    paintEdges();
  };
  window.addEventListener('themechange', onTheme);

  setMode(st.mode);
  paintEdges();

  return () => {
    cancelAnimationFrame(raf);
    offs.forEach((f) => f());
    window.removeEventListener('themechange', onTheme);
    hideTip();
  };
}
