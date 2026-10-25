// Data layer. Every screen reads data through these functions only.
//
// Mock mode (the default for now; also `?mock=1`) reads the fixtures in
// ./mock/ and drives the live stream from mock_stream.js. With `?mock=0`
// the same functions call the real backend (fetch + EventSource). The
// fixtures are the API contract: one file per endpoint, see README.md.

import { MockStream } from './mock_stream.js';

const params = new URLSearchParams(location.search);
export const MOCK = params.get('mock') !== '0';
const API = params.get('api') || ''; // optional base URL for the real backend

// ---------- transport ----------
async function getJSON(path) {
  const res = await fetch(API + path, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}
async function postJSON(path, body) {
  const res = await fetch(API + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json().catch(() => ({}));
}

const fixtures = new Map();
function fixture(name) {
  if (!fixtures.has(name)) {
    fixtures.set(name, fetch('mock/' + name).then((r) => {
      if (!r.ok) throw Object.assign(new Error(`mock/${name}: HTTP ${r.status}`), { status: r.status });
      return r.json();
    }));
  }
  return fixtures.get(name).then((x) => structuredClone(x));
}

let simPromise = null;
function sim() {
  if (!simPromise) {
    simPromise = (async () => {
      const [grid, summary, runs, leaderboard] = await Promise.all([
        fixture('grid.json'), fixture('summary.json'), fixture('runs.json'), fixture('leaderboard.json'),
      ]);
      const files = await Promise.all(grid.worlds.map((w) => fixture(`world_${w.world_id}.json`)));
      const worldFiles = Object.fromEntries(files.map((f) => [f.world_id, f.files.map((x) => x.path)]));
      return new MockStream({ grid, summary, runs, leaderboard, worldFiles });
    })();
  }
  return simPromise;
}

// ---------- endpoints ----------
/** GET /api/grid */
export const getGrid = () => (MOCK ? fixture('grid.json') : getJSON('/api/grid'));

/** GET /api/preflight */
export const getPreflight = () => (MOCK ? fixture('preflight.json') : getJSON('/api/preflight'));

/** GET /api/summary */
export async function getSummary() {
  if (!MOCK) return getJSON('/api/summary');
  const s = await sim();
  const snap = s.summarySnapshot();
  if (!s.started) snap.state = 'idle'; // nothing streams until Start (mock only)
  return snap;
}

/** GET /api/runs */
export async function getRuns() {
  if (!MOCK) return getJSON('/api/runs');
  return (await sim()).runsSnapshot();
}

/** GET /api/runs/{id} */
export async function getRun(id) {
  if (!MOCK) return getJSON(`/api/runs/${encodeURIComponent(id)}`);
  const s = await sim();
  // Fixture detail wins while the simulator has not re-run this run.
  if (!s.runEvents.has(id)) {
    try { return await fixture(`run_${id}.json`); } catch { /* no fixture for this run */ }
  }
  return s.synthRun(id);
}

/** GET /api/runs/{id}/events */
export async function getRunEvents(id) {
  if (!MOCK) return getJSON(`/api/runs/${encodeURIComponent(id)}/events`);
  const s = await sim();
  if (!s.runEvents.has(id)) {
    try { return await fixture(`events_${id}.json`); } catch { /* no fixture */ }
  }
  return s.eventsFor(id);
}

/** GET /api/leaderboard */
export async function getLeaderboard() {
  if (!MOCK) return getJSON('/api/leaderboard');
  const s = await sim();
  return s.started ? s.leaderboard() : fixture('leaderboard.json');
}

/** GET /api/worlds/{id}/files (used by the map's focus mode) */
export function getWorldFiles(worldId) {
  if (!MOCK) return getJSON(`/api/worlds/${encodeURIComponent(worldId)}/files`);
  return fixture(`world_${worldId}.json`);
}

/**
 * GET /api/stream (SSE). Calls onEvent(event) for every event.
 * Returns an unsubscribe function.
 */
export function subscribe(onEvent) {
  if (!MOCK) {
    const es = new EventSource(API + '/api/stream');
    es.onmessage = (m) => {
      try { onEvent(JSON.parse(m.data)); } catch (err) { console.error('bad event', err); }
    };
    return () => es.close();
  }
  let off = null, cancelled = false;
  sim().then((s) => { if (!cancelled) off = s.on(onEvent); });
  return () => { cancelled = true; if (off) off(); };
}

/** Run IDs that have a full fixture trace (mock only; used for replay pickers). */
export async function detailedRunIds() {
  if (!MOCK) return [];
  return ['r-0001', 'r-0022', 'r-0011'];
}

// ---------- actions ----------
/** POST /api/grid/start */
export async function startGrid() {
  if (!MOCK) return postJSON('/api/grid/start');
  (await sim()).start();
  return { ok: true };
}
/** POST /api/grid/pause */
export async function pauseGrid() {
  if (!MOCK) return postJSON('/api/grid/pause');
  (await sim()).pause();
  return { ok: true };
}
/** POST /api/grid/resume */
export async function resumeGrid() {
  if (!MOCK) return postJSON('/api/grid/resume');
  (await sim()).resume();
  return { ok: true };
}
/** POST /api/smoke: run one task with one model end to end. */
export async function runSmokeTest() {
  if (!MOCK) return postJSON('/api/smoke');
  await new Promise((r) => setTimeout(r, 2600));
  return { ok: true, run_id: 'smoke-0001', task_id: 'organize_project_folder', model_id: 'model_a', score: 1.0, duration_s: 41 };
}
/** Is the stream running? (mock: has Start been pressed) */
export async function isStreaming() {
  if (!MOCK) return true;
  return (await sim()).started;
}
