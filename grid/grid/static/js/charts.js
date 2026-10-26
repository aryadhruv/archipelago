// Hand-written SVG charts. Thin marks, hairline grid, hover tooltip on every mark.

import { s, h, showTip, moveTip, hideTip, tipBody, fmtTokens } from './util.js';

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}
function hover(node, content) {
  node.addEventListener('mouseenter', (e) => showTip(e, content()));
  node.addEventListener('mousemove', moveTip);
  node.addEventListener('mouseleave', hideTip);
}

/**
 * Stacked vertical bars. rows: [{label, values:{key:n}}], series: [{key,label,color}]
 */
export function stackedBars(rows, series, { height = 200, yFormat = fmtTokens, xTitle = '', yTitle = '' } = {}) {
  const W = 640, H = height, m = { l: 48, r: 8, t: 8, b: 30 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const totals = rows.map((r) => series.reduce((a, sr) => a + (r.values[sr.key] || 0), 0));
  const max = niceMax(Math.max(1, ...totals));
  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': yTitle || 'bar chart' });
  for (let i = 0; i <= 4; i++) {
    const y = m.t + ih - (ih * i) / 4;
    svg.append(s('line', { class: i === 0 ? 'axis' : 'grid-line', x1: m.l, x2: W - m.r, y1: y, y2: y }));
    svg.append(s('text', { class: 'tick', x: m.l - 6, y: y + 4, 'text-anchor': 'end' }, yFormat((max * i) / 4)));
  }
  const n = rows.length || 1;
  const band = iw / n;
  const bw = Math.max(3, Math.min(28, band - 4));
  rows.forEach((r, i) => {
    const x = m.l + band * i + (band - bw) / 2;
    let y0 = m.t + ih;
    const segs = series.filter((sr) => (r.values[sr.key] || 0) > 0);
    segs.forEach((sr, k) => {
      const v = r.values[sr.key] || 0;
      const hgt = (ih * v) / max;
      const top = k === segs.length - 1;
      const gap = k > 0 ? 2 : 0; // 2px surface gap between stacked fills
      const y = y0 - hgt;
      const rect = top
        ? s('path', { d: roundedTop(x, y, bw, Math.max(0, hgt - gap), Math.min(4, bw / 2)), fill: sr.color })
        : s('rect', { x, y, width: bw, height: Math.max(0, hgt - gap), fill: sr.color });
      svg.append(rect);
      y0 = y;
    });
    if (n <= 24 || i % Math.ceil(n / 24) === 0) {
      svg.append(s('text', { class: 'tick', x: x + bw / 2, y: H - m.b + 16, 'text-anchor': 'middle' }, r.label));
    }
    const hit = s('rect', { class: 'hit', x: m.l + band * i, y: m.t, width: band, height: ih });
    hover(hit, () => tipBody(`Step ${r.label}`, [
      ...series.map((sr) => [sr.label, fmtTokens(r.values[sr.key] || 0)]), ['Total', fmtTokens(totals[i])]]));
    svg.append(hit);
  });
  if (xTitle) svg.append(s('text', { class: 'axis-title', x: m.l + iw / 2, y: H - 2, 'text-anchor': 'middle' }, xTitle));
  return svg;
}

function roundedTop(x, y, w, hgt, r) {
  if (hgt <= r) return `M${x},${y + hgt}V${y}H${x + w}V${y + hgt}Z`;
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}

/**
 * Scatter with direct labels. points: [{x, y, label, color, tip:[[k,v]]}]
 */
export function scatter(points, { xTitle, yTitle, xFormat = (v) => v, yFormat = (v) => v.toFixed(2), yMax = 1 } = {}) {
  const W = 520, H = 300, m = { l: 44, r: 64, t: 12, b: 40 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const xs = points.map((p) => p.x);
  const lo = Math.min(...xs), hi = Math.max(...xs);
  const pad = Math.max((hi - lo) * 0.25, hi * 0.05, 1);
  const step = niceMax((hi - lo + 2 * pad) / 4);
  const xMin = Math.max(0, Math.floor((lo - pad) / step) * step);
  const xMax = xMin + step * Math.ceil((hi + pad - xMin) / step);
  const X = (v) => m.l + ((v - xMin) / (xMax - xMin)) * iw;
  const Y = (v) => m.t + ih - (v / yMax) * ih;
  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${yTitle} vs ${xTitle}` });
  for (let i = 0; i <= 4; i++) {
    const y = m.t + ih - (ih * i) / 4;
    svg.append(s('line', { class: i === 0 ? 'axis' : 'grid-line', x1: m.l, x2: m.l + iw, y1: y, y2: y }));
    svg.append(s('text', { class: 'tick', x: m.l - 6, y: y + 4, 'text-anchor': 'end' }, yFormat((yMax * i) / 4)));
  }
  for (let v = xMin; v <= xMax + 1e-9; v += step) {
    svg.append(s('text', { class: 'tick', x: X(v), y: m.t + ih + 16, 'text-anchor': 'middle' }, xFormat(v)));
  }
  svg.append(s('text', { class: 'axis-title', x: m.l + iw / 2, y: H - 4, 'text-anchor': 'middle' }, xTitle));
  svg.append(s('text', { class: 'axis-title', x: 12, y: m.t + ih / 2, transform: `rotate(-90 12 ${m.t + ih / 2})`, 'text-anchor': 'middle' }, yTitle));
  // labels: nudge to avoid overlaps (simple vertical relaxation)
  const placed = points.map((p) => ({ p, lx: X(p.x) + 9, ly: Y(p.y) + 4 }));
  placed.sort((a, b) => a.ly - b.ly);
  for (let it = 0; it < 20; it++) {
    for (let i = 1; i < placed.length; i++) {
      const a = placed[i - 1], b = placed[i];
      if (Math.abs(a.lx - b.lx) < 60 && b.ly - a.ly < 13) { const d = (13 - (b.ly - a.ly)) / 2; a.ly -= d; b.ly += d; }
    }
  }
  for (const { p, lx, ly } of placed) {
    const g = s('g');
    g.append(s('circle', { class: 'mark-ring', cx: X(p.x), cy: Y(p.y), r: 6, fill: p.color }));
    g.append(s('text', { class: 'dlabel', x: lx, y: ly }, p.label));
    const hit = s('circle', { class: 'hit', cx: X(p.x), cy: Y(p.y), r: 14 });
    hover(hit, () => tipBody(p.label, p.tip || [], p.color));
    g.append(hit);
    svg.append(g);
  }
  return svg;
}

/** Inline mean ± CI dot-and-whisker on a 0..1 scale. */
export function ciMark(mean, ci, color, w = 120) {
  const H = 14, X = (v) => 4 + Math.max(0, Math.min(1, v)) * (w - 8);
  const svg = s('svg', { width: w, height: H, viewBox: `0 0 ${w} ${H}`, 'aria-hidden': 'true' });
  svg.append(s('line', { x1: 4, x2: w - 4, y1: H / 2, y2: H / 2, stroke: 'var(--grid-line)', 'stroke-width': 1 }));
  if (ci !== null && ci !== undefined) {
    svg.append(s('line', { x1: X(mean - ci), x2: X(mean + ci), y1: H / 2, y2: H / 2, stroke: 'var(--text-2)', 'stroke-width': 2, 'stroke-linecap': 'round' }));
  }
  if (mean === null || mean === undefined) return svg; // no scored runs yet
  svg.append(s('circle', { cx: X(mean), cy: H / 2, r: 4.5, fill: color, stroke: 'var(--surface-1)', 'stroke-width': 2 }));
  return svg;
}

/** Legend row for a set of series. */
export function legend(series) {
  return h('div', { class: 'legend' }, series.map((sr) =>
    h('span', { class: 'lg' }, h('span', { class: 'swatch', style: { background: sr.color } }), sr.label)));
}
