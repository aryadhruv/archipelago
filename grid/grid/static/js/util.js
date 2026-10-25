// Small shared helpers: DOM building, formatting, colors, icons, tooltip.

export const SVG_NS = 'http://www.w3.org/2000/svg';

/** Build an HTML element. h('div', {class: 'x', onclick}, child, 'text', [more]) */
export function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  setAttrs(node, attrs);
  append(node, children);
  return node;
}

/** Build an SVG element. */
export function s(tag, attrs, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  setAttrs(node, attrs);
  append(node, children);
  return node;
}

function setAttrs(node, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k === 'html') node.innerHTML = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
}

function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === undefined || c === null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// ---------- formatting ----------
const nf = new Intl.NumberFormat('en-US');
export const fmtInt = (n) => (n === null || n === undefined ? '–' : nf.format(Math.round(n)));
export function fmtTokens(n) {
  if (n === null || n === undefined) return '–';
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + 'k';
  return String(Math.round(n));
}
export function fmtDuration(sec) {
  if (sec === null || sec === undefined || isNaN(sec)) return '–';
  sec = Math.max(0, Math.round(sec));
  const hrs = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), r = sec % 60;
  if (hrs) return `${hrs}h ${String(m).padStart(2, '0')}m`;
  if (m) return `${m}m ${String(r).padStart(2, '0')}s`;
  return `${r}s`;
}
export const fmtScore = (x) => (x === null || x === undefined ? '–' : x.toFixed(2));
export const fmtPct = (x) => (x === null || x === undefined ? '–' : Math.round(x * 100) + '%');
export function fmtBytes(b) {
  if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB';
  if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB';
  if (b >= 1e3) return (b / 1e3).toFixed(0) + ' KB';
  return b + ' B';
}
export function fmtClock(ts) {
  const d = new Date(ts);
  return d.toLocaleTimeString('en-GB', { hour12: false, timeZone: 'UTC' });
}
export function fmtDateTime(ts) {
  if (!ts) return '–';
  const d = new Date(ts);
  return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
}

// ---------- theme + colors ----------
export const theme = () => document.documentElement.dataset.theme || 'dark';

// Categorical slots (validated with the dataviz validator, adjacent pairs, both modes).
const DARK_STEP = {
  '#2a78d6': '#3987e5', '#eb6834': '#d95926', '#1baf7a': '#199e70',
  '#eda100': '#c98500', '#e87ba4': '#d55181', '#008300': '#008300',
};
let MODELS = {};
export function registerModels(models) {
  MODELS = Object.fromEntries(models.map((m) => [m.id, m]));
}
export const modelById = (id) => MODELS[id];
export const modelLabel = (id) => (MODELS[id] ? MODELS[id].label : id);
export function modelColor(id) {
  const m = MODELS[id];
  if (!m) return 'var(--text-muted)';
  return theme() === 'dark' ? DARK_STEP[m.color.toLowerCase()] || m.color : m.color;
}

/** Label ink on top of a model-colored fill (dark ink on yellow, white elsewhere). */
export function modelInk(id) {
  const m = MODELS[id];
  return m && m.color.toLowerCase() === '#eda100' ? '#0b0b0b' : '#ffffff';
}

// Sequential blue ramp (100 -> 650). Light: low = pale, high = dark. Dark: anchor flips.
const RAMP = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5',
  '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281'];
function hex2rgb(x) { return [1, 3, 5].map((i) => parseInt(x.slice(i, i + 2), 16)); }
function mix(a, b, t) {
  const A = hex2rgb(a), B = hex2rgb(b);
  return '#' + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, '0')).join('');
}
export function scoreColor(score) {
  if (score === null || score === undefined) return 'transparent';
  const ramp = theme() === 'dark' ? [...RAMP].reverse() : RAMP;
  const x = Math.max(0, Math.min(1, score)) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(x));
  return mix(ramp[i], ramp[i + 1], x - i);
}
/** Ink that reads on top of scoreColor(score). */
export function scoreInk(score) {
  const dark = theme() === 'dark';
  const strong = dark ? score > 0.55 : score > 0.45;
  return dark ? (strong ? '#0b0b0b' : '#f0efec') : strong ? '#ffffff' : '#0b0b0b';
}
export const scoreRampStops = () => (theme() === 'dark' ? [...RAMP].reverse() : RAMP);

// ---------- run states ----------
export const STATES = {
  queued: { label: 'Queued', group: 'queued' },
  env_starting: { label: 'Starting env', group: 'running' },
  populating: { label: 'Loading world', group: 'running' },
  apps_starting: { label: 'Starting apps', group: 'running' },
  agent_running: { label: 'Agent working', group: 'running' },
  snapshotting: { label: 'Snapshotting', group: 'running' },
  grading: { label: 'Grading', group: 'running' },
  scored: { label: 'Scored', group: 'scored' },
  retry: { label: 'Retrying', group: 'retrying' },
  gave_up: { label: 'Gave up', group: 'gave_up' },
};
export const stateGroup = (st) => (STATES[st] ? STATES[st].group : 'queued');
export const stateLabel = (st) => (STATES[st] ? STATES[st].label : st);

// ---------- MCP servers ----------
export const SERVERS = [
  { id: 'filesystem_server', label: 'Files', icon: 'folder' },
  { id: 'docs_server', label: 'Documents', icon: 'doc' },
  { id: 'sheets_server', label: 'Spreadsheets', icon: 'sheet' },
  { id: 'slides_server', label: 'Slides', icon: 'slides' },
  { id: 'pdf_server', label: 'PDFs', icon: 'pdf' },
  { id: 'mail_server', label: 'Mail', icon: 'mail' },
  { id: 'calendar_server', label: 'Calendar', icon: 'calendar' },
  { id: 'chat_server', label: 'Chat', icon: 'chat' },
  { id: 'code_execution_server', label: 'Code', icon: 'code' },
  { id: 'toolbelt', label: 'Toolbelt', icon: 'toolbelt' },
];
export const serverLabel = (id) => (SERVERS.find((x) => x.id === id) || { label: id }).label;
export const TOOLBELT_TOOLS = ['todo_write', 'toolbelt_list_tools', 'toolbelt_inspect_tool',
  'toolbelt_add_tool', 'toolbelt_remove_tool', 'final_answer'];
/** Short tool name without the `{server}_` prefix. */
export function shortTool(tool, server) {
  if (!tool) return '';
  if (server && server !== 'toolbelt' && tool.startsWith(server + '_')) return tool.slice(server.length + 1);
  return tool;
}

// 24x24 stroke icons (drawn, no icon font). Each entry is a list of path `d` strings.
export const ICONS = {
  folder: ['M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'],
  doc: ['M6 3h8l4 4v14H6z', 'M14 3v4h4', 'M9 12h6', 'M9 16h6'],
  sheet: ['M4 5h16v14H4z', 'M4 10h16', 'M4 14.5h16', 'M10 5v14'],
  slides: ['M3 5h18v11H3z', 'M12 16v4', 'M8 20h8', 'M7 12l3-3 2 2 4-4'],
  pdf: ['M6 3h8l4 4v14H6z', 'M14 3v4h4', 'M8.5 15.5h7', 'M8.5 12h4'],
  mail: ['M3 6h18v12H3z', 'M3 7l9 6 9-6'],
  calendar: ['M4 6h16v14H4z', 'M4 10h16', 'M8 3v5', 'M16 3v5'],
  chat: ['M4 5h16v10H9l-5 4z'],
  code: ['M9 8l-4 4 4 4', 'M15 8l4 4-4 4', 'M13 6l-2 12'],
  toolbelt: ['M14.5 6.5a4 4 0 0 0-5.3 5.3L4 17l3 3 5.2-5.2a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.3-.7-.7-2.3z'],
  agent: ['M7 8h10v9H7z', 'M12 4v4', 'M10 12h.01', 'M14 12h.01', 'M4 12v2', 'M20 12v2'],
  play: ['M8 5l11 7-11 7z'],
  pause: ['M8 5v14', 'M16 5v14'],
  check: ['M5 12l4 4 10-10'],
  x: ['M6 6l12 12', 'M18 6L6 18'],
  warn: ['M12 4l9 16H3z', 'M12 10v4', 'M12 17h.01'],
  retry: ['M4 12a8 8 0 0 1 14-5.3L20 9', 'M20 4v5h-5', 'M20 12a8 8 0 0 1-14 5.3L4 15', 'M4 20v-5h5'],
  clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M12 7v5l3 2'],
  box: ['M4 7l8-4 8 4v10l-8 4-8-4z', 'M4 7l8 4 8-4', 'M12 11v10'],
  download: ['M12 4v11', 'M7 10l5 5 5-5', 'M5 20h14'],
  sun: ['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z', 'M12 2v2', 'M12 20v2', 'M4.9 4.9l1.4 1.4', 'M17.7 17.7l1.4 1.4', 'M2 12h2', 'M20 12h2', 'M4.9 19.1l1.4-1.4', 'M17.7 6.3l1.4-1.4'],
  moon: ['M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z'],
  map: ['M9 4l-6 2v14l6-2 6 2 6-2V4l-6 2z', 'M9 4v14', 'M15 6v14'],
  grade: ['M5 4h14v16H5z', 'M8 9l2 2 4-4', 'M8 15h8'],
  snapshot: ['M4 8h3l2-3h6l2 3h3v11H4z', 'M12 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6z'],
  file: ['M6 3h8l4 4v14H6z', 'M14 3v4h4'],
};
/** Phase icon for a run state. */
export const STATE_ICON = {
  queued: 'clock', env_starting: 'box', populating: 'folder', apps_starting: 'box',
  agent_running: 'agent', snapshotting: 'snapshot', grading: 'grade', scored: 'check', retry: 'retry', gave_up: 'x',
};

/** Inline SVG icon element (HTML context). */
export function icon(name, size = 16, cls = '') {
  const svg = s('svg', { viewBox: '0 0 24 24', width: size, height: size, class: 'icon ' + cls, 'aria-hidden': 'true' });
  for (const d of ICONS[name] || []) svg.append(s('path', { d }));
  return svg;
}

// ---------- tooltip (singleton) ----------
let tipEl = null;
export function showTip(evt, content) {
  if (!tipEl) {
    tipEl = h('div', { class: 'tooltip', role: 'tooltip' });
    document.body.append(tipEl);
  }
  clear(tipEl);
  if (typeof content === 'string') tipEl.textContent = content;
  else tipEl.append(content);
  tipEl.style.display = 'block';
  moveTip(evt);
}
export function moveTip(evt) {
  if (!tipEl || tipEl.style.display !== 'block') return;
  const pad = 14, r = tipEl.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
  tipEl.style.left = x + 'px';
  tipEl.style.top = y + 'px';
}
export function hideTip() { if (tipEl) tipEl.style.display = 'none'; }
/** Attach a hover tooltip; `content` may be a function returning string|Node. */
export function tip(node, content) {
  node.addEventListener('mouseenter', (e) => showTip(e, typeof content === 'function' ? content() : content));
  node.addEventListener('mousemove', moveTip);
  node.addEventListener('mouseleave', hideTip);
  return node;
}
/** Tooltip body: title + key/value rows. */
export function tipBody(title, rows, swatch) {
  return h('div', null,
    h('div', { class: 'tt-title' }, swatch ? h('span', { class: 'swatch', style: { background: swatch } }) : null, title),
    rows.map(([k, v]) => h('div', { class: 'tt-row' }, h('span', { class: 'tt-k' }, k), h('span', { class: 'tt-v' }, v))));
}

// ---------- misc ----------
export const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

export function download(name, text, mime) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = h('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Pull a file path out of an args_preview JSON string (may be truncated). */
export function pathFromArgs(preview) {
  if (!preview) return null;
  const m = /"(?:file_path|path)"\s*:\s*"([^"]+)"/.exec(preview);
  return m ? m[1] : null;
}

export function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
export function storageSet(key, val) {
  try { localStorage.setItem(key, val); } catch { /* storage unavailable */ }
}

export function emptyState(title, body, iconName = 'clock') {
  return h('div', { class: 'empty' }, icon(iconName, 28), h('div', { class: 'empty-title' }, title),
    body ? h('div', { class: 'empty-body' }, body) : null);
}
