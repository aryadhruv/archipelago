// In-memory client state shared by all views: the grid definition, the latest
// summary and run rows (polled), and a ring buffer of recent stream events.

import * as api from './api.js';
import { registerModels } from './util.js';

const EVENT_CAP = 600;
const listeners = { summary: new Set(), runs: new Set(), event: new Set() };

export const store = {
  grid: null,
  summary: null,
  runs: [],
  runIndex: new Map(),
  events: [],
  live: false,
};

export function on(topic, fn) {
  listeners[topic].add(fn);
  return () => listeners[topic].delete(fn);
}
function emit(topic, payload) {
  for (const fn of listeners[topic]) {
    try { fn(payload); } catch (err) { console.error(err); }
  }
}

export async function init() {
  store.grid = await api.getGrid();
  registerModels(store.grid.models);
  await refresh();
}

export async function refresh() {
  const [summary, runs] = await Promise.all([api.getSummary(), api.getRuns()]);
  store.summary = summary;
  store.runs = runs;
  store.runIndex = new Map(runs.map((r) => [r.run_id, r]));
  emit('summary', summary);
  emit('runs', runs);
}

let unsub = null, poll = null;
/** Start listening to the stream and polling summary/runs. Idempotent. */
export function goLive() {
  if (store.live) return;
  store.live = true;
  unsub = api.subscribe((e) => {
    store.events.push(e);
    if (store.events.length > EVENT_CAP) store.events.splice(0, store.events.length - EVENT_CAP);
    if (e.kind === 'run_state' && e.run_id) {
      const r = store.runIndex.get(e.run_id);
      if (r) r.state = e.state;
    }
    emit('event', e);
  });
  poll = setInterval(() => refresh().catch((err) => console.error(err)), 1000);
}

export function stopLive() {
  if (unsub) unsub();
  if (poll) clearInterval(poll);
  unsub = poll = null;
  store.live = false;
}

export const taskById = (id) => store.grid.tasks.find((t) => t.task_id === id);
export const worldById = (id) => store.grid.worlds.find((w) => w.world_id === id);
