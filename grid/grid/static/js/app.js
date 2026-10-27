// App shell: hash router, top bar (grid name, live indicator, theme toggle).

import * as api from './api.js';
import { store, init, goLive, on } from './store.js';
import { icon, clear, h, storageSet, emptyState } from './util.js';
import * as setup from './views/setup.js';
import * as live from './views/live.js';
import * as map from './views/map.js';
import * as run from './views/run.js';
import * as leaderboard from './views/leaderboard.js';
import * as infra from './views/infra.js';

const ROUTES = { setup, live, map, run, leaderboard, infra };
const view = document.getElementById('view');
let cleanup = null;
let currentKey = '';

/** Parse "#/run/r-0001?x=1" -> {name:'run', arg:'r-0001', query:URLSearchParams} */
export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, qs] = raw.split('?');
  const [name, ...rest] = path.split('/');
  return { name: name || 'setup', arg: rest.join('/') ? decodeURIComponent(rest.join('/')) : null, query: new URLSearchParams(qs || '') };
}

export function navigate(hash) {
  if (location.hash === hash) route(true);
  else location.hash = hash;
}

function route(force = false) {
  const r = parseHash();
  const key = location.hash;
  if (!force && key === currentKey) return;
  currentKey = key;
  const mod = ROUTES[r.name];
  for (const a of document.querySelectorAll('#tabs a')) {
    a.classList.toggle('active', a.dataset.route === r.name || (r.name === 'run' && a.dataset.route === 'live'));
  }
  if (cleanup) { try { cleanup(); } catch (err) { console.error(err); } }
  cleanup = null;
  clear(view);
  view.scrollTop = 0;
  window.scrollTo(0, 0);
  if (!mod) {
    view.append(emptyState('Page not found', h('a', { href: '#/setup' }, 'Back to setup'), 'map'));
    return;
  }
  try {
    const out = mod.render(view, r);
    if (typeof out === 'function') cleanup = out;
  } catch (err) {
    console.error(err);
    view.append(emptyState('Something went wrong rendering this screen', String(err), 'warn'));
  }
}

// ---------- theme ----------
const toggle = document.getElementById('theme-toggle');
function paintToggle() {
  clear(toggle).append(icon(document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon', 18));
}
toggle.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  storageSet('grid-theme', next);
  paintToggle();
  window.dispatchEvent(new CustomEvent('themechange'));
});
paintToggle();

// ---------- live indicator ----------
const ind = document.getElementById('live-ind');
const LABEL = { running: 'Live', paused: 'Paused', idle: 'Not started', stopped: 'Stopped', done: 'Finished' };
function paintIndicator(summary) {
  if (!summary) return;
  // An idle grid that has started runs before was stopped (or failed).
  const state = summary.state === 'idle' && summary.started_at ? 'stopped' : summary.state;
  ind.dataset.state = state;
  ind.querySelector('.live-label').textContent = LABEL[state] || state;
  ind.title = summary.error ? `Grid stopped: ${summary.error}` : `Grid state: ${state}`;
}

// ---------- boot ----------
(async function boot() {
  if (api.MOCK) document.getElementById('mock-badge').hidden = false;
  try {
    await init();
  } catch (err) {
    console.error(err);
    view.append(emptyState('Could not load the grid', `${err.message}. Is \`grid serve\` running? Demo data is at /demo/.`, 'warn'));
    return;
  }
  document.getElementById('grid-id').textContent = store.grid.grid_id;
  document.title = `Archipelago · ${store.grid.grid_id}`;
  paintIndicator(store.summary);
  on('summary', paintIndicator);
  goLive();
  if (!location.hash) history.replaceState(null, '', '#/setup');
  window.addEventListener('hashchange', () => route());
  route(true);
})();
