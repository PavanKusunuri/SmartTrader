/* Smart Trader dashboard — read-only client for the paper-trading ledger.
 *
 * PAPER TRADING — NO REAL ORDERS. This client only issues GET requests. It has
 * no code path that submits, modifies or cancels an order.
 *
 * Conventions enforced throughout:
 *  - A value the backend did not record renders as "not recorded" / "no price",
 *    never as 0 and never as a guess. Zero and unknown are different facts.
 *  - Gains/losses never rely on colour alone: an explicit sign and an arrow
 *    glyph carry the same meaning for colour-blind readers and in print.
 *  - Every table is paginated, filtered and sorted server-side; the browser
 *    never receives a whole table.
 *  - A slow response can never overwrite a newer one (see `guard`).
 */
'use strict';

/* ------------------------------------------------------------- formatting */

const INR = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2, maximumFractionDigits: 2,
});
const INR0 = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 0, maximumFractionDigits: 0,
});
const NUM = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

const MISSING = '<span class="text-slate-400 dark:text-slate-500" title="No value recorded for this field">not recorded</span>';

function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function money(value, { compact = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  return `<span class="nums">${esc((compact ? INR0 : INR).format(value))}</span>`;
}

/** Signed money with an arrow, so direction is never colour-only. */
function pnl(value, { compact = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  const positive = value > 0;
  const negative = value < 0;
  const cls = positive
    ? 'text-emerald-700 dark:text-emerald-400'
    : negative ? 'text-rose-700 dark:text-rose-400' : 'text-slate-600 dark:text-slate-300';
  const arrow = positive ? '▲' : negative ? '▼' : '■';
  const sign = positive ? '+' : '';
  const formatted = (compact ? INR0 : INR).format(value);
  return `<span class="nums font-medium ${cls}"><span aria-hidden="true">${arrow}</span> ${esc(sign + formatted)}</span>`;
}

function pct(value, { digits = 2, signed = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  const sign = signed && value > 0 ? '+' : '';
  return `<span class="nums">${esc(sign + (value * 100).toFixed(digits))}%</span>`;
}

function pctPnl(value, { digits = 2 } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  const positive = value > 0;
  const negative = value < 0;
  const cls = positive
    ? 'text-emerald-700 dark:text-emerald-400'
    : negative ? 'text-rose-700 dark:text-rose-400' : 'text-slate-600 dark:text-slate-300';
  const arrow = positive ? '▲' : negative ? '▼' : '■';
  const sign = positive ? '+' : '';
  return `<span class="nums font-medium ${cls}"><span aria-hidden="true">${arrow}</span> ${esc(sign + (value * 100).toFixed(digits))}%</span>`;
}

function num(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  return `<span class="nums">${esc(NUM.format(Number(value.toFixed ? value.toFixed(digits) : value)))}</span>`;
}

const hasOffset = (s) => /([+-]\d{2}:?\d{2}|Z)$/.test(String(s).trim());

/** Formats a timestamp in Asia/Kolkata. Strings without a timezone offset are
 *  machine-local write stamps, shown as-is and marked, not silently relabelled
 *  as IST. */
function ts(value, { dateOnly = false } = {}) {
  if (!value) return MISSING;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return `<span class="nums">${esc(value)}</span>`;
  const opts = dateOnly
    ? { year: 'numeric', month: 'short', day: '2-digit' }
    : { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false };
  if (!hasOffset(value)) {
    const local = date.toLocaleString('en-IN', opts);
    return `<span class="nums" title="Recorded without a timezone (local machine clock)">${esc(local)}<span class="ml-1 text-[10px] text-slate-400">local</span></span>`;
  }
  const text = date.toLocaleString('en-IN', { ...opts, timeZone: 'Asia/Kolkata' });
  return `<span class="nums" title="${esc(value)}">${esc(text)}<span class="ml-1 text-[10px] text-slate-400">IST</span></span>`;
}

function dateOnly(value) { return ts(value, { dateOnly: true }); }

/* ------------------------------------------------------------- components */

const BADGE_TONES = {
  green: 'bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/20',
  red: 'bg-rose-100 text-rose-800 ring-rose-600/20 dark:bg-rose-950 dark:text-rose-300 dark:ring-rose-400/20',
  amber: 'bg-amber-100 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/20',
  blue: 'bg-sky-100 text-sky-800 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-300 dark:ring-sky-400/20',
  slate: 'bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-400/20',
  violet: 'bg-violet-100 text-violet-800 ring-violet-600/20 dark:bg-violet-950 dark:text-violet-300 dark:ring-violet-400/20',
};

function badge(text, tone = 'slate', title = '') {
  if (text === null || text === undefined || text === '') return MISSING;
  return `<span class="inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${BADGE_TONES[tone] || BADGE_TONES.slate}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
}

const STATUS_TONE = {
  FILLED: 'green', CLOSED: 'green', HEALTHY: 'green', OK: 'green', OPEN: 'green',
  REJECTED: 'red', UNHEALTHY: 'red', INVALID: 'red',
  CANCELLED: 'amber', EXPIRED: 'amber', DEGRADED: 'amber', STALE: 'amber',
  SUSPECT_GAP: 'amber', PARTIALLY_FILLED: 'amber', NOT_QUEUED: 'amber',
  SUBMITTED: 'blue', BUY: 'green', SELL: 'red', LONG: 'blue',
  NO_TRADE: 'slate', CLOSED_SESSION: 'slate', UNKNOWN: 'slate', NOT_IMPLEMENTED: 'violet',
};
const statusBadge = (value, title = '') => badge(value, STATUS_TONE[String(value).toUpperCase()] || 'slate', title);

/* Data-kind labels carry real consequences, so each gets its own tone and an
 * explanation of what the data is NOT. Synthetic data is amber because acting
 * on it would be a mistake; bhavcopy is blue because it is real but
 * unadjusted, which is a different kind of caution. */
const DATA_KIND_META = {
  SAMPLE_SYNTHETIC: ['amber', 'Generated test data. Describes no real company and must not inform any decision.'],
  NSE_BHAVCOPY_EOD: ['blue', 'Real NSE exchange-published end-of-day data from a file you downloaded by hand. Unadjusted for corporate actions, and end-of-day only — not a quote.'],
  HISTORICAL_CSV: ['slate', 'A CSV you supplied. This app cannot verify whether it is real market data.'],
};

function dataKindBadge(kind) {
  if (!kind) return MISSING;
  const [tone, why] = DATA_KIND_META[kind] || ['slate', ''];
  return badge(kind, tone, why);
}

function card(label, valueHtml, { hint = '', wide = false } = {}) {
  return `<div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900 ${wide ? 'sm:col-span-2' : ''}">
    <div class="text-[11px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">${esc(label)}</div>
    <div class="mt-1 text-lg font-semibold">${valueHtml}</div>
    ${hint ? `<div class="mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">${esc(hint)}</div>` : ''}
  </div>`;
}

function panel(title, bodyHtml, { actions = '', note = '' } = {}) {
  return `<section class="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
    <div class="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-2.5 dark:border-slate-800">
      <h2 class="text-sm font-semibold">${esc(title)}</h2>
      <div class="ml-auto flex flex-wrap items-center gap-2">${actions}</div>
    </div>
    ${note ? `<p class="border-b border-slate-200 bg-slate-50 px-4 py-2 text-[11px] leading-relaxed text-slate-600 dark:border-slate-800 dark:bg-slate-950/50 dark:text-slate-400">${esc(note)}</p>` : ''}
    <div>${bodyHtml}</div>
  </section>`;
}

function notice(text, tone = 'amber') {
  const tones = {
    amber: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200',
    red: 'border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-800 dark:bg-rose-950/60 dark:text-rose-200',
    blue: 'border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-800 dark:bg-sky-950/60 dark:text-sky-200',
    slate: 'border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300',
  };
  return `<div role="note" class="rounded-lg border px-3 py-2 text-xs leading-relaxed ${tones[tone] || tones.amber}">${esc(text)}</div>`;
}

const loading = (what = 'data') =>
  `<div class="flex items-center gap-2 px-4 py-10 text-sm text-slate-500 dark:text-slate-400"><span class="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600"></span> Loading ${esc(what)}…</div>`;

const emptyState = (title, detail = '') =>
  `<div class="px-4 py-12 text-center">
     <div class="text-sm font-medium text-slate-700 dark:text-slate-200">${esc(title)}</div>
     ${detail ? `<div class="mx-auto mt-1 max-w-xl text-xs leading-relaxed text-slate-500 dark:text-slate-400">${esc(detail)}</div>` : ''}
   </div>`;

const errorState = (message) =>
  `<div class="px-4 py-10 text-center">
     <div class="text-sm font-medium text-rose-700 dark:text-rose-400">Could not load this view</div>
     <div class="mx-auto mt-1 max-w-xl text-xs leading-relaxed text-slate-600 dark:text-slate-400">${esc(message)}</div>
   </div>`;

function kv(rows) {
  const body = rows.map(([label, value]) => `
    <div class="grid grid-cols-5 gap-2 border-b border-slate-100 px-4 py-1.5 last:border-0 dark:border-slate-800">
      <dt class="col-span-2 text-xs text-slate-500 dark:text-slate-400">${esc(label)}</dt>
      <dd class="col-span-3 break-words text-xs">${value === null || value === undefined || value === '' ? MISSING : value}</dd>
    </div>`).join('');
  return `<dl>${body}</dl>`;
}

/** Server-side-paginated table. `cols` entries: {key,label,render,align,sort}. */
function table(cols, rows, { sortable = [], sort = '', order = 'desc', onSortAttr = '', rowAttr = null } = {}) {
  if (!rows.length) return '';
  const head = cols.map((c) => {
    const canSort = c.sort && sortable.includes(c.sort);
    const active = sort === c.sort;
    const arrow = active ? (order === 'asc' ? ' ▲' : ' ▼') : '';
    const align = c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left';
    const inner = canSort
      ? `<button class="inline-flex items-center gap-0.5 font-semibold hover:text-indigo-700 dark:hover:text-indigo-400" ${onSortAttr}="${esc(c.sort)}" aria-label="Sort by ${esc(c.label)}">${esc(c.label)}<span aria-hidden="true">${arrow}</span></button>`
      : `<span class="font-semibold">${esc(c.label)}</span>`;
    return `<th scope="col" class="whitespace-nowrap px-3 py-2 ${align}">${inner}</th>`;
  }).join('');

  const body = rows.map((row) => {
    const attrs = rowAttr ? rowAttr(row) : '';
    const cells = cols.map((c) => {
      const align = c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left';
      return `<td class="whitespace-nowrap px-3 py-2 ${align} ${c.cls || ''}">${c.render(row)}</td>`;
    }).join('');
    return `<tr class="border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50" ${attrs}>${cells}</tr>`;
  }).join('');

  return `<div class="scroll-x"><table class="w-full min-w-max text-xs">
    <thead class="bg-slate-50 text-slate-600 dark:bg-slate-950/50 dark:text-slate-400"><tr>${head}</tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}

function pager(meta, attr) {
  const { page, total_pages: pages, total, page_size: size } = meta;
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(page * size, total);
  return `<div class="flex flex-wrap items-center gap-2 border-t border-slate-200 px-4 py-2 text-xs dark:border-slate-800">
    <span class="text-slate-500 dark:text-slate-400">Showing <span class="nums">${from}–${to}</span> of <span class="nums">${total}</span></span>
    <div class="ml-auto flex items-center gap-1">
      <button ${attr}="1" ${page <= 1 ? 'disabled' : ''} class="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-700">First</button>
      <button ${attr}="${page - 1}" ${page <= 1 ? 'disabled' : ''} class="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-700">Prev</button>
      <span class="px-1 text-slate-500 dark:text-slate-400">Page <span class="nums">${page}</span> / <span class="nums">${pages || 1}</span></span>
      <button ${attr}="${page + 1}" ${page >= pages ? 'disabled' : ''} class="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-700">Next</button>
      <button ${attr}="${pages || 1}" ${page >= pages ? 'disabled' : ''} class="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-700">Last</button>
    </div>
  </div>`;
}

const inputCls = 'rounded border border-slate-300 bg-white px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900';

function select(attr, label, options, current) {
  const opts = ['<option value="">All</option>'].concat(
    options.filter((o) => o !== null && o !== undefined && o !== '')
      .map((o) => `<option value="${esc(o)}" ${String(current) === String(o) ? 'selected' : ''}>${esc(o)}</option>`)
  ).join('');
  return `<label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">${esc(label)}
    <select ${attr} class="${inputCls}">${opts}</select></label>`;
}

const linkBtn = (href, text) =>
  `<a href="${esc(href)}" class="rounded border border-slate-300 px-2 py-1 text-xs font-medium hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800" download>${esc(text)}</a>`;

/* ----------------------------------------------------------------- charts */

/* Charts are hand-drawn inline SVG: no chart library, so the dashboard has no
 * third-party runtime dependency and works offline. Each chart plots recorded
 * values only — no smoothing, no interpolation, no trend line. */

function lineChart(points, { height = 180, label = 'equity', baseline = null } = {}) {
  const values = points.map((p) => p.value).filter((v) => v !== null && v !== undefined);
  if (values.length < 2) {
    return emptyState('Not enough recorded points to draw a chart',
      'A curve needs at least two equity snapshots. Run a backtest with --db to record them.');
  }
  const W = 1000, H = height, PAD_L = 8, PAD_R = 8, PAD_T = 10, PAD_B = 18;
  let min = Math.min(...values), max = Math.max(...values);
  if (baseline !== null) { min = Math.min(min, baseline); max = Math.max(max, baseline); }
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const x = (i) => PAD_L + (i * (W - PAD_L - PAD_R)) / (points.length - 1);
  const y = (v) => PAD_T + (1 - (v - min) / span) * (H - PAD_T - PAD_B);

  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const area = `${path} L${x(points.length - 1).toFixed(1)},${y(min).toFixed(1)} L${x(0).toFixed(1)},${y(min).toFixed(1)} Z`;
  const baseY = baseline !== null ? y(baseline) : null;
  const last = points[points.length - 1];
  const rising = baseline !== null ? last.value >= baseline : last.value >= points[0].value;
  const stroke = rising ? '#059669' : '#e11d48';
  const fill = rising ? 'rgba(5,150,105,0.12)' : 'rgba(225,29,72,0.12)';

  return `<figure class="px-4 py-3">
    <figcaption class="sr-only">${esc(label)} from ${esc(points[0].label || '')} to ${esc(last.label || '')}</figcaption>
    <svg viewBox="0 0 ${W} ${H}" class="h-auto w-full" role="img" aria-label="${esc(label)} chart">
      <path d="${area}" fill="${fill}"/>
      ${baseY !== null ? `<line x1="${PAD_L}" y1="${baseY.toFixed(1)}" x2="${W - PAD_R}" y2="${baseY.toFixed(1)}" stroke="currentColor" stroke-opacity="0.35" stroke-dasharray="4 4" class="text-slate-500"/>` : ''}
      <path d="${path}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linejoin="round"/>
      <circle cx="${x(points.length - 1).toFixed(1)}" cy="${y(last.value).toFixed(1)}" r="3" fill="${stroke}"/>
    </svg>
    <div class="mt-1 flex flex-wrap justify-between gap-2 text-[11px] text-slate-500 dark:text-slate-400">
      <span>${esc(points[0].label || '')}</span>
      <span>min ${esc(INR0.format(min))} · max ${esc(INR0.format(max))}${baseline !== null ? ` · start ${esc(INR0.format(baseline))}` : ''}</span>
      <span>${esc(last.label || '')}</span>
    </div>
  </figure>`;
}

function drawdownChart(points, { height = 120 } = {}) {
  if (points.length < 2) return '';
  let peak = points[0].value;
  const dd = points.map((p) => {
    peak = Math.max(peak, p.value);
    return { label: p.label, value: peak > 0 ? -((peak - p.value) / peak) : 0 };
  });
  const W = 1000, H = height, PAD = 10;
  const worst = Math.min(...dd.map((d) => d.value), -0.0001);
  const x = (i) => PAD + (i * (W - 2 * PAD)) / (dd.length - 1);
  const y = (v) => PAD + (v / worst) * (H - 2 * PAD);
  const path = dd.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d.value).toFixed(1)}`).join(' ');
  const area = `${path} L${x(dd.length - 1).toFixed(1)},${PAD} L${x(0).toFixed(1)},${PAD} Z`;
  return `<figure class="px-4 py-3">
    <figcaption class="mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">Drawdown from running peak (worst ${(worst * -100).toFixed(2)}%)</figcaption>
    <svg viewBox="0 0 ${W} ${H}" class="h-auto w-full" role="img" aria-label="Drawdown chart, worst ${(worst * -100).toFixed(2)} percent">
      <path d="${area}" fill="rgba(225,29,72,0.15)"/>
      <path d="${path}" fill="none" stroke="#e11d48" stroke-width="1.5"/>
    </svg>
  </figure>`;
}

function candleChart(bars, { height = 260 } = {}) {
  if (!bars.length) return emptyState('No bars to chart');
  const W = 1000, H = height, PAD_T = 10, PAD_B = 22, PAD_X = 10;
  const highs = bars.map((b) => b.high), lows = bars.map((b) => b.low);
  let min = Math.min(...lows), max = Math.max(...highs);
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const slot = (W - 2 * PAD_X) / bars.length;
  const bodyW = Math.max(1, Math.min(10, slot * 0.6));
  const y = (v) => PAD_T + (1 - (v - min) / span) * (H - PAD_T - PAD_B);

  const marks = bars.map((b, i) => {
    const cx = PAD_X + slot * (i + 0.5);
    const up = b.close >= b.open;
    const colour = up ? '#059669' : '#e11d48';
    const top = y(Math.max(b.open, b.close));
    const bottom = y(Math.min(b.open, b.close));
    const h = Math.max(1, bottom - top);
    return `<g>
      <line x1="${cx.toFixed(1)}" y1="${y(b.high).toFixed(1)}" x2="${cx.toFixed(1)}" y2="${y(b.low).toFixed(1)}" stroke="${colour}" stroke-width="1"/>
      <rect x="${(cx - bodyW / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${h.toFixed(1)}" fill="${up ? colour : colour}" fill-opacity="${up ? 0.85 : 1}"/>
    </g>`;
  }).join('');

  return `<figure class="px-4 py-3">
    <svg viewBox="0 0 ${W} ${H}" class="h-auto w-full" role="img" aria-label="Candlestick chart of ${bars.length} bars">
      ${marks}
    </svg>
    <div class="mt-1 flex flex-wrap justify-between gap-2 text-[11px] text-slate-500 dark:text-slate-400">
      <span>${esc(String(bars[0].timestamp).slice(0, 10))}</span>
      <span>low ${esc(NUM.format(min))} · high ${esc(NUM.format(max))} · ${bars.length} bars</span>
      <span>${esc(String(bars[bars.length - 1].timestamp).slice(0, 10))}</span>
    </div>
  </figure>`;
}

/* ------------------------------------------------------------ data access */

const seqByKey = new Map();

/** Fetches JSON, guarding against an older in-flight response landing after a
 *  newer one for the same logical view. */
async function getJSON(url, key = url) {
  const seq = (seqByKey.get(key) || 0) + 1;
  seqByKey.set(key, seq);
  let response;
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (networkError) {
    if (seqByKey.get(key) !== seq) return { stale: true };
    throw new Error('The dashboard server is unreachable. Is it still running?');
  }
  if (seqByKey.get(key) !== seq) return { stale: true };

  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (seqByKey.get(key) !== seq) return { stale: true };

  if (!response.ok) {
    const detail = payload && (payload.detail || payload.error);
    throw new Error(detail ? String(detail) : `Request failed (HTTP ${response.status}).`);
  }
  return payload;
}

const qs = (params) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) search.set(k, v);
  });
  const text = search.toString();
  return text ? `?${text}` : '';
};

/* ------------------------------------------------------------------ pages */

const el = (name) => document.querySelector(`[data-page="${name}"]`);

/* ---- Overview ---- */

const overview = {
  async render() {
    const root = el('overview');
    root.innerHTML = loading('portfolio overview');
    let data;
    try { data = await getJSON('/api/overview', 'overview'); } catch (e) {
      root.innerHTML = errorState(e.message); return;
    }
    if (data.stale) return;

    if (!data.has_data) {
      root.innerHTML = panel('Portfolio overview', emptyState('No recorded runs yet', data.message));
      return;
    }

    const run = data.run;
    const curve = (data.equity_curve || []).map((p) => ({
      value: p.equity, label: String(p.timestamp || '').slice(0, 10),
    }));
    const netReturn = data.starting_capital
      ? (data.realized_net_pnl) / data.starting_capital : null;

    const unrealized = data.open_position_count === 0
      ? pnl(0)
      : data.unrealized_pnl_incomplete
        ? `${pnl(data.unrealized_pnl)} <span class="block text-[11px] font-normal text-amber-700 dark:text-amber-400">partial: no price for ${esc(data.missing_price_symbols.join(', '))}</span>`
        : pnl(data.unrealized_pnl);

    root.innerHTML = `
      <div class="space-y-4">
        ${notice(`Figures below describe one recorded backtest run (${run.strategy_name}, ${run.start_date || '?'} to ${run.end_date || '?'}, interval ${run.interval || '?'}). There is no live portfolio in this version.`, 'blue')}

        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
          ${card('Starting capital', money(data.starting_capital, { compact: true }))}
          ${card('Available cash', money(data.cash), { hint: 'Last recorded equity snapshot' })}
          ${card('Portfolio value', money(data.portfolio_value), { hint: 'Cash + marked open positions' })}
          ${card('Invested (at cost)', money(data.invested))}
          ${card('Realized net P&L', pnl(data.realized_net_pnl), { hint: 'Closed trades, after simulated costs' })}
          ${card('Unrealized P&L', unrealized, { hint: 'Open positions, marked to last bar' })}
          ${card('Gross P&L', pnl(data.gross_pnl), { hint: 'Before simulated costs' })}
          ${card('Simulated costs', money(data.total_costs), { hint: 'Unverified rate assumptions' })}
          ${card('Net return', netReturn === null ? MISSING : pctPnl(netReturn), { hint: 'Realized net P&L ÷ starting capital' })}
          ${card('Max drawdown', data.max_drawdown_pct === null ? MISSING : pct(data.max_drawdown_pct), { hint: 'Peak-to-trough of equity curve' })}
          ${card('Open positions', num(data.open_position_count, 0))}
          ${card('Trades closed', num(data.trade_count, 0))}
          ${card('Win rate', data.win_rate === null ? MISSING : `${pct(data.win_rate, { digits: 1 })} <span class="text-xs font-normal text-slate-500">(${data.win_count}W / ${data.loss_count}L)</span>`, { hint: data.trade_count < 30 ? 'Sample far too small to be meaningful' : 'Small sample' })}
          ${card('Expectancy / trade', pnl(data.expectancy))}
        </div>

        ${data.ambiguous_trade_count > 0 ? notice(`${data.ambiguous_trade_count} trade(s) had stop-loss and target touched inside the same bar. The true order of events is unknowable from OHLC data, so the engine assumed the stop filled first. Treat those outcomes as uncertain.`, 'amber') : ''}
        ${(data.reconciliation_problems || []).length ? notice(`Ledger reconciliation problems: ${data.reconciliation_problems.join('; ')}`, 'red') : ''}

        ${panel('Equity curve', lineChart(curve, { label: 'Portfolio equity', baseline: data.starting_capital }) + drawdownChart(curve), { note: 'Recorded equity after each evaluated bar. Values are plotted as recorded; the series is uniformly sampled for display, never smoothed or interpolated.' })}

        ${panel('Run provenance', kv([
          ['Run ID', `<code class="font-mono text-[11px]">${esc(run.run_id)}</code>`],
          ['Strategy', esc(run.strategy_name)],
          ['Symbols', esc((run.symbols || []).join(', '))],
          ['Bar interval', esc(run.interval || '')],
          ['Date range', `${esc(run.start_date || '?')} → ${esc(run.end_date || '?')}`],
          ['Data sources', Object.entries(run.data_sources || {}).map(([s, src]) => `${esc(s)}: <code class="font-mono text-[11px]">${esc(src)}</code>`).join('<br>')],
          ['Strategy parameters', `<code class="font-mono text-[11px]">${esc(JSON.stringify(run.params || {}))}</code>`],
          ['Cost assumptions', `<code class="font-mono text-[11px]">${esc(JSON.stringify(run.costs || {}))}</code>`],
          ['Risk limits used', `<code class="font-mono text-[11px]">${esc(JSON.stringify(run.risk || {}))}</code>`],
          ['Signals evaluated', num(run.signal_count, 0)],
          ['Run recorded at', ts(run.started_at)],
        ]), { note: 'CLAUDE.md section 8 requires the date range, symbols, interval, data source and cost assumptions to accompany every result, so they are recorded per run and shown here.' })}

        ${notice(data.disclaimer, 'slate')}
      </div>`;
  },
};

/* ---- generic paginated list page ---- */

function listPage({ name, title, endpoint, cacheKey, cols, filters, exportHref, note, defaultSort, defaultOrder = 'desc', onRow, emptyTitle, emptyDetail, extraTop }) {
  const state = {
    page: 1, page_size: 50, sort: defaultSort, order: defaultOrder, search: '', filters: {},
  };

  async function render() {
    const root = el(name);
    if (!root.dataset.ready) root.innerHTML = loading(title.toLowerCase());
    let data;
    const params = { page: state.page, page_size: state.page_size, sort: state.sort, order: state.order, search: state.search, ...state.filters };
    try { data = await getJSON(endpoint + qs(params), cacheKey); } catch (e) {
      root.innerHTML = errorState(e.message); return;
    }
    if (data.stale) return;
    root.dataset.ready = '1';

    const opts = data.filter_options || {};
    const controls = `
      <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">Search
        <input data-search type="search" value="${esc(state.search)}" placeholder="symbol or id" class="${inputCls} w-40">
      </label>
      ${(filters || []).map((f) => select(`data-filter="${f.key}"`, f.label, opts[f.optionsKey] || f.options || [], state.filters[f.key] || '')).join('')}
      <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">From
        <input data-from type="date" value="${esc(state.filters.date_from || '')}" class="${inputCls}"></label>
      <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">To
        <input data-to type="date" value="${esc(state.filters.date_to || '')}" class="${inputCls}"></label>
      <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">Rows
        <select data-size class="${inputCls}">${[25, 50, 100, 200].map((n) => `<option ${state.page_size === n ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
      ${exportHref ? linkBtn(exportHref(params), 'Export CSV') : ''}`;

    const body = data.rows.length
      ? table(cols, data.rows, { sortable: data.sortable || [], sort: state.sort, order: state.order, onSortAttr: 'data-sort', rowAttr: onRow })
        + pager(data, 'data-page-to')
      : emptyState(
          data.total === 0 && !state.search && !Object.keys(state.filters).length ? emptyTitle : 'No rows match these filters',
          data.total === 0 && !state.search && !Object.keys(state.filters).length ? emptyDetail : 'Clear the search or filters to see all recorded rows.');

    root.innerHTML = `<div class="space-y-4">
      ${extraTop ? extraTop(data) : ''}
      ${panel(title, body, { actions: controls, note: note || data.note || '' })}
    </div>`;

    // Wire controls.
    root.querySelectorAll('[data-sort]').forEach((b) => b.addEventListener('click', () => {
      const key = b.dataset.sort;
      state.order = state.sort === key && state.order === 'desc' ? 'asc' : 'desc';
      state.sort = key; state.page = 1; render();
    }));
    root.querySelectorAll('[data-page-to]').forEach((b) => b.addEventListener('click', () => {
      state.page = Number(b.dataset.pageTo); render();
    }));
    const search = root.querySelector('[data-search]');
    if (search) {
      let timer;
      search.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => { state.search = search.value.trim(); state.page = 1; render(); }, 250);
      });
    }
    root.querySelectorAll('[data-filter]').forEach((sel) => sel.addEventListener('change', () => {
      const key = sel.dataset.filter;
      if (sel.value) state.filters[key] = sel.value; else delete state.filters[key];
      state.page = 1; render();
    }));
    const from = root.querySelector('[data-from]');
    const to = root.querySelector('[data-to]');
    if (from) from.addEventListener('change', () => { if (from.value) state.filters.date_from = from.value; else delete state.filters.date_from; state.page = 1; render(); });
    if (to) to.addEventListener('change', () => { if (to.value) state.filters.date_to = to.value; else delete state.filters.date_to; state.page = 1; render(); });
    const size = root.querySelector('[data-size]');
    if (size) size.addEventListener('change', () => { state.page_size = Number(size.value); state.page = 1; render(); });

    if (onRow) {
      root.querySelectorAll('tr[data-detail]').forEach((tr) => {
        tr.classList.add('cursor-pointer');
        tr.setAttribute('tabindex', '0');
        const open = () => openDetail(tr.dataset.detail, tr.dataset.detailKind);
        tr.addEventListener('click', open);
        tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
      });
    }
  }

  return { render };
}

/* ---- Trades ---- */

const trades = listPage({
  name: 'trades', title: 'Simulated trades', endpoint: '/api/trades', cacheKey: 'trades',
  defaultSort: 'exit_at',
  emptyTitle: 'No simulated trades recorded',
  emptyDetail: 'Run a backtest with a ledger: python -m app.cli backtest --db data\\paper_trading.db',
  filters: [
    { key: 'symbol', label: 'Symbol', optionsKey: 'symbols' },
    { key: 'strategy', label: 'Strategy', optionsKey: 'strategies' },
    { key: 'status', label: 'Status', optionsKey: 'statuses' },
    { key: 'direction', label: 'Direction', optionsKey: 'directions' },
  ],
  exportHref: (p) => `/api/export/trades.csv${qs(p)}`,
  note: 'Closed round-trips recorded by the paper broker. Open positions are on the Positions page, not here.',
  onRow: (row) => `data-detail="${esc(row.trade_id)}" data-detail-kind="trade"`,
  cols: [
    { key: 'trade_id', label: 'Trade', sort: null, render: (r) => `<code class="font-mono text-[11px]" title="${esc(r.trade_id)}">${esc(String(r.trade_id).slice(0, 8))}</code>` },
    { key: 'symbol', label: 'Symbol', sort: 'symbol', render: (r) => `<span class="font-medium">${esc(r.symbol)}</span>` },
    { key: 'strategy_name', label: 'Strategy', render: (r) => r.strategy_name ? badge(r.strategy_name, 'blue') : MISSING },
    { key: 'side', label: 'Direction', render: (r) => statusBadge(r.side || 'LONG', 'v1 is long-only cash equity') },
    { key: 'status', label: 'Status', render: (r) => statusBadge(r.status || 'CLOSED') },
    { key: 'entry_at', label: 'Entry', sort: 'entry_at', render: (r) => dateOnly(r.entry_at) },
    { key: 'entry_price', label: 'Entry ₹', align: 'right', render: (r) => num(r.entry_price) },
    { key: 'exit_at', label: 'Exit', sort: 'exit_at', render: (r) => dateOnly(r.exit_at) },
    { key: 'exit_price', label: 'Exit ₹', align: 'right', render: (r) => num(r.exit_price) },
    { key: 'quantity', label: 'Qty', align: 'right', sort: 'quantity', render: (r) => num(r.quantity, 0) },
    { key: 'stop_loss', label: 'Stop', align: 'right', render: (r) => num(r.stop_loss) },
    { key: 'target', label: 'Target', align: 'right', render: (r) => num(r.target) },
    { key: 'gross_pnl', label: 'Gross P&L', align: 'right', sort: 'gross_pnl', render: (r) => pnl(r.gross_pnl) },
    { key: 'costs', label: 'Costs', align: 'right', sort: 'costs', render: (r) => money(r.costs) },
    { key: 'net_pnl', label: 'Net P&L', align: 'right', sort: 'net_pnl', render: (r) => pnl(r.net_pnl) },
    { key: 'exit_reason', label: 'Exit reason', render: (r) => `<span class="block max-w-xs truncate" title="${esc(r.exit_reason)}">${esc(r.exit_reason)}</span>` },
    { key: 'ambiguous_exit', label: 'Exit certain?', align: 'center', render: (r) => r.ambiguous_exit ? badge('ambiguous', 'amber', 'Stop and target were both touched in the same bar; the engine assumed the stop filled first.') : badge('yes', 'slate') },
  ],
});

/* ---- Signals ---- */

const signals = listPage({
  name: 'signals', title: 'Signals', endpoint: '/api/signals', cacheKey: 'signals',
  defaultSort: 'timestamp',
  emptyTitle: 'No signals recorded',
  emptyDetail: 'Signals are recorded by a backtest run. Run: python -m app.cli backtest --db data\\paper_trading.db',
  filters: [
    { key: 'symbol', label: 'Symbol', optionsKey: 'symbols' },
    { key: 'strategy', label: 'Strategy', optionsKey: 'strategies' },
    { key: 'action', label: 'Action', optionsKey: 'actions' },
    { key: 'outcome', label: 'Outcome', optionsKey: 'outcomes' },
  ],
  exportHref: (p) => `/api/export/signals.csv${qs(p)}`,
  onRow: (row) => `data-detail="${esc(row.run_id)}|${esc(row.signal_id)}" data-detail-kind="signal"`,
  extraTop: (data) => {
    const counts = data.action_counts || {};
    const cards = `<div class="grid grid-cols-3 gap-3">
      ${card('BUY signals', num(counts.BUY || 0, 0))}
      ${card('SELL signals', num(counts.SELL || 0, 0))}
      ${card('NO_TRADE', num(counts.NO_TRADE || 0, 0), { hint: 'Evaluated and declined' })}
    </div>`;
    const noTrade = data.no_trade_only
      ? notice('NO TRADE — every recorded signal declined to trade. That is a valid outcome: the strategy found no qualifying setup, or the data was unusable. Open a row to see the exact rule inputs behind each decision.', 'slate')
      : '';
    return cards + (noTrade ? `<div class="mt-3">${noTrade}</div>` : '');
  },
  cols: [
    { key: 'signal_id', label: 'Signal', render: (r) => `<code class="font-mono text-[11px]" title="${esc(r.signal_id)}">${esc(String(r.signal_id).slice(0, 28))}${String(r.signal_id).length > 28 ? '…' : ''}</code>` },
    { key: 'symbol', label: 'Symbol', sort: 'symbol', render: (r) => `<span class="font-medium">${esc(r.symbol)}</span>` },
    { key: 'strategy_name', label: 'Strategy', sort: 'strategy_name', render: (r) => badge(r.strategy_name, r.strategy_name && String(r.strategy_name).startsWith('engine:') ? 'violet' : 'blue', String(r.strategy_name || '').startsWith('engine:') ? 'Engine-initiated exit, not a strategy signal' : '') },
    { key: 'timestamp', label: 'Signal time', sort: 'timestamp', render: (r) => ts(r.timestamp) },
    { key: 'action', label: 'Action', sort: 'action', render: (r) => statusBadge(r.action) },
    { key: 'outcome', label: 'Outcome', sort: 'outcome', render: (r) => statusBadge(r.outcome || 'UNKNOWN') },
    { key: 'stop_loss', label: 'Stop', align: 'right', render: (r) => num(r.stop_loss) },
    { key: 'target', label: 'Target', align: 'right', render: (r) => num(r.target) },
    { key: 'reason', label: 'Reason', render: (r) => `<span class="block max-w-md truncate" title="${esc(r.reason)}">${esc(r.reason)}</span>` },
    { key: 'trade_id', label: 'Trade', render: (r) => r.trade_id ? `<code class="font-mono text-[11px]">${esc(String(r.trade_id).slice(0, 8))}</code>` : badge('none', 'slate') },
  ],
});

/* ---- Orders ---- */

const orders = listPage({
  name: 'orders', title: 'Simulated orders', endpoint: '/api/orders', cacheKey: 'orders',
  defaultSort: 'submitted_at',
  emptyTitle: 'No simulated orders recorded',
  emptyDetail: 'Orders are recorded by a backtest run with --db.',
  filters: [
    { key: 'symbol', label: 'Symbol', optionsKey: 'symbols' },
    { key: 'side', label: 'Side', optionsKey: 'sides' },
    { key: 'status', label: 'Status', optionsKey: 'statuses' },
  ],
  exportHref: (p) => `/api/export/orders.csv${qs(p)}`,
  cols: [
    { key: 'order_id', label: 'Order', render: (r) => `<code class="font-mono text-[11px]" title="${esc(r.order_id)}">${esc(String(r.order_id).slice(0, 8))}</code>` },
    { key: 'symbol', label: 'Symbol', sort: 'symbol', render: (r) => `<span class="font-medium">${esc(r.symbol)}</span>` },
    { key: 'side', label: 'Side', render: (r) => statusBadge(r.side) },
    { key: 'order_type', label: 'Type', render: (r) => badge(r.order_type || 'MARKET', 'slate', 'The paper broker simulates market-style fills only') },
    { key: 'status', label: 'Status', sort: 'status', render: (r) => statusBadge(r.status) },
    { key: 'quantity', label: 'Req qty', align: 'right', sort: 'quantity', render: (r) => num(r.quantity, 0) },
    { key: 'filled_quantity', label: 'Filled qty', align: 'right', render: (r) => num(r.filled_quantity, 0) },
    { key: 'basis', label: 'Pre-slip ₹', align: 'right', render: (r) => r.economics && r.economics.fill_price_basis !== null ? `${num(r.economics.fill_price_basis)}<span class="ml-1 text-[10px] text-slate-400" title="Derived from the run's recorded slippage rate, not stored">derived</span>` : MISSING },
    { key: 'filled_price', label: 'Fill ₹', align: 'right', render: (r) => num(r.filled_price) },
    { key: 'slippage', label: 'Slippage ₹', align: 'right', render: (r) => r.economics && r.economics.slippage_total !== null ? `${num(r.economics.slippage_total)}<span class="ml-1 text-[10px] text-slate-400" title="Derived">derived</span>` : MISSING },
    { key: 'costs', label: 'Costs ₹', align: 'right', render: (r) => {
        const e = r.economics;
        if (!e || e.costs === null) return MISSING;
        const assumed = e.charge_profile_assumed;
        const label = assumed ? 'assumed' : 'derived';
        const why = assumed
          ? `Derived, but this order recorded no charge profile, so DELIVERY rates were assumed. Delivery and intraday charge different STT, stamp duty and DP fees, so this figure may be wrong for an intraday order.`
          : `Derived from the run's recorded cost assumptions under the ${e.charge_profile} charge profile.`;
        return `${num(e.costs)}<span class="ml-1 text-[10px] ${assumed ? 'text-amber-600 dark:text-amber-400' : 'text-slate-400'}" title="${esc(why)}">${label}</span>`;
      } },
    { key: 'charge_profile', label: 'Charge profile', render: (r) => {
        const e = r.economics || {};
        if (!e.charge_profile) return MISSING;
        return badge(e.charge_profile, e.charge_profile_assumed ? 'amber' : 'slate',
          e.charge_profile_assumed ? 'Not recorded on this order; DELIVERY assumed' : 'Recorded on this order');
      } },
    { key: 'submitted_at', label: 'Submitted', sort: 'submitted_at', render: (r) => ts(r.submitted_at) },
    { key: 'filled_at', label: 'Filled', sort: 'filled_at', render: (r) => r.filled_at ? ts(r.filled_at) : badge('never filled', 'slate') },
    { key: 'rejection_reason', label: 'Reject / cancel reason', render: (r) => r.rejection_reason ? `<span class="block max-w-md truncate text-amber-800 dark:text-amber-300" title="${esc(r.rejection_reason)}">${esc(r.rejection_reason)}</span>` : '<span class="text-slate-400">—</span>' },
    { key: 'trade_id', label: 'Trade', render: (r) => r.trade_id ? `<code class="font-mono text-[11px]">${esc(String(r.trade_id).slice(0, 8))}</code>` : badge('none', 'slate') },
  ],
});

/* ---- Positions ---- */

const positions = {
  async render() {
    const root = el('positions');
    root.innerHTML = loading('positions');
    let data;
    try { data = await getJSON('/api/positions', 'positions'); } catch (e) {
      root.innerHTML = errorState(e.message); return;
    }
    if (data.stale) return;

    if (!data.has_data) {
      root.innerHTML = panel('Open positions', emptyState('No recorded runs yet',
        'Run a backtest with --db to record a position snapshot.'));
      return;
    }

    const cols = [
      { key: 'symbol', label: 'Symbol', render: (r) => `<span class="font-medium">${esc(r.symbol)}</span>` },
      { key: 'quantity', label: 'Qty', align: 'right', render: (r) => num(r.quantity, 0) },
      { key: 'avg_entry_price', label: 'Avg entry ₹', align: 'right', render: (r) => num(r.avg_entry_price) },
      { key: 'last_price', label: 'Last price ₹', align: 'right', render: (r) => r.price_available ? num(r.last_price) : badge('no price', 'red', r.price_note || '') },
      { key: 'market_value', label: 'Market value', align: 'right', render: (r) => money(r.market_value) },
      { key: 'unrealized_pnl', label: 'Unrealized P&L', align: 'right', render: (r) => pnl(r.unrealized_pnl) },
      { key: 'unrealized_pnl_pct', label: 'Unreal %', align: 'right', render: (r) => pctPnl(r.unrealized_pnl_pct) },
      { key: 'stop_loss', label: 'Stop', align: 'right', render: (r) => num(r.stop_loss) },
      { key: 'target', label: 'Target', align: 'right', render: (r) => num(r.target) },
      { key: 'exposure_pct', label: 'Exposure %', align: 'right', render: (r) => pct(r.exposure_pct) },
      { key: 'holding_days', label: 'Held (days)', align: 'right', render: (r) => num(r.holding_days, 0) },
      { key: 'last_price_kind', label: 'Data', render: (r) => dataKindBadge(r.last_price_kind) },
      { key: 'breaches', label: 'Warnings', render: (r) => (r.breaches || []).length ? (r.breaches || []).map((b) => badge('!', 'red', b)).join(' ') + `<span class="ml-1 text-[11px] text-rose-700 dark:text-rose-400">${esc(r.breaches[0])}</span>` : '<span class="text-slate-400">—</span>' },
    ];

    const rec = data.reconciliation || {};
    root.innerHTML = `<div class="space-y-4">
      ${notice(`Snapshot from run ${data.run.run_id.slice(0, 8)} (${data.run.strategy_name}). Recorded when the run finished — not a live portfolio.`, 'blue')}

      <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        ${card('Open positions', num(data.rows.length, 0))}
        ${card('Portfolio value', money(data.portfolio_value))}
        ${card('Total exposure', money(data.exposure_total))}
        ${card('Exposure % of value', pct(data.exposure_pct_of_portfolio), { hint: `Limit: ${(data.limits.max_total_exposure_pct * 100).toFixed(0)}%` })}
      </div>

      ${panel('Open positions', data.rows.length ? table(cols, data.rows) : emptyState('No open positions', 'Every simulated position was closed before the run ended. That is a normal outcome, not an error.'), { note: data.note })}

      ${panel('Cash and position reconciliation', kv([
        ['Cash (last snapshot)', money(rec.cash)],
        ['Marked position value', money(rec.marked_positions_value)],
        ['Cash + positions', money(rec.cash_plus_positions)],
        ['Recorded equity', money(rec.recorded_equity)],
        ['Difference', rec.difference === null ? MISSING : pnl(rec.difference)],
        ['Positions missing a price', (rec.positions_with_missing_price || []).length ? esc(rec.positions_with_missing_price.join(', ')) : '<span class="text-slate-500">none</span>'],
      ]), { note: rec.note })}
    </div>`;
  },
};

/* ---- Strategies ---- */

const strategies = {
  async render() {
    const root = el('strategies');
    root.innerHTML = loading('strategy performance');
    let data, runsData;
    try {
      [data, runsData] = await Promise.all([
        getJSON('/api/strategies', 'strategies'),
        getJSON('/api/runs?page_size=50', 'strategy-runs'),
      ]);
    } catch (e) { root.innerHTML = errorState(e.message); return; }
    if (data.stale || runsData.stale) return;

    const strategyCols = [
      { key: 'strategy_name', label: 'Strategy', render: (r) => `<span class="font-medium">${esc(r.strategy_name)}</span>` },
      { key: 'backtest_count', label: 'Backtests', align: 'right', render: (r) => num(r.backtest_count, 0) },
      { key: 'range', label: 'Date range', render: (r) => `${esc(r.first_start_date || '?')} → ${esc(r.last_end_date || '?')}` },
      { key: 'trade_count', label: 'Trades', align: 'right', render: (r) => num(r.trade_count, 0) },
      { key: 'win_rate', label: 'Win rate', align: 'right', render: (r) => r.win_rate === null ? MISSING : pct(r.win_rate, { digits: 1 }) },
      { key: 'avg_win', label: 'Avg win', align: 'right', render: (r) => pnl(r.avg_win) },
      { key: 'avg_loss', label: 'Avg loss', align: 'right', render: (r) => pnl(r.avg_loss) },
      { key: 'expectancy', label: 'Expectancy', align: 'right', render: (r) => pnl(r.expectancy) },
      { key: 'gross_pnl', label: 'Gross P&L', align: 'right', render: (r) => pnl(r.gross_pnl) },
      { key: 'costs', label: 'Costs', align: 'right', render: (r) => money(r.costs) },
      { key: 'net_pnl', label: 'Net P&L', align: 'right', render: (r) => pnl(r.net_pnl) },
      { key: 'warn', label: 'Sample', render: (r) => badge(r.trade_count < 30 ? 'too small' : 'small', 'amber', r.sample_warning) },
    ];

    const runCols = [
      { key: 'run_id', label: 'Run', render: (r) => `<code class="font-mono text-[11px]" title="${esc(r.run_id)}">${esc(String(r.run_id).slice(0, 8))}</code>` },
      { key: 'strategy_name', label: 'Strategy', sort: 'strategy_name', render: (r) => badge(r.strategy_name, 'blue') },
      { key: 'symbols', label: 'Symbols', render: (r) => esc((r.symbols || []).join(', ')) },
      { key: 'interval', label: 'Interval', render: (r) => badge(r.interval || '?', 'slate') },
      { key: 'start_date', label: 'From', sort: 'start_date', render: (r) => esc(r.start_date || '?') },
      { key: 'end_date', label: 'To', sort: 'end_date', render: (r) => esc(r.end_date || '?') },
      { key: 'starting_cash', label: 'Start cash', align: 'right', render: (r) => money(r.starting_cash, { compact: true }) },
      { key: 'final_equity', label: 'Final equity', align: 'right', sort: 'final_equity', render: (r) => money(r.final_equity, { compact: true }) },
      { key: 'ret', label: 'Net return', align: 'right', render: (r) => (r.final_equity && r.starting_cash) ? pctPnl((r.final_equity - r.starting_cash) / r.starting_cash) : MISSING },
      { key: 'dd', label: 'Max DD', align: 'right', render: (r) => (r.metrics && r.metrics.max_drawdown_pct !== undefined && r.metrics.max_drawdown_pct !== null) ? pct(r.metrics.max_drawdown_pct) : MISSING },
      { key: 'bench', label: 'Benchmark', align: 'right', render: (r) => (r.metrics && r.metrics.benchmark_return_pct !== undefined && r.metrics.benchmark_return_pct !== null) ? pctPnl(r.metrics.benchmark_return_pct) : MISSING },
      { key: 'started_at', label: 'Run at', sort: 'started_at', render: (r) => ts(r.started_at) },
    ];

    root.innerHTML = `<div class="space-y-4">
      ${notice(data.out_of_sample.note, 'amber')}
      ${notice(data.disclaimer, 'slate')}

      ${panel('Strategy summary (historical backtests)', data.rows.length ? table(strategyCols, data.rows) : emptyState('No strategy results recorded'), { note: `Result kind: ${data.result_kind}. These are backtest results, which are not forward paper-trading results. ${data.benchmark_note}` })}

      ${panel('Backtest runs', runsData.rows.length ? table(runCols, runsData.rows, { sortable: runsData.sortable || [], sort: 'started_at', order: 'desc' }) + pager(runsData, 'data-noop') : emptyState('No runs recorded'), { note: 'Viewing this page does not start a backtest or change any parameter. Runs are created only by the CLI.' })}

      ${panel('Currently configured parameters', kv(
        Object.entries(data.configured_parameters).map(([group, params]) =>
          [group, `<code class="font-mono text-[11px]">${esc(JSON.stringify(params))}</code>`])
      ), { note: 'Read from app/config.py in the running server. These are EXPERIMENTAL defaults, not tuned or validated values. A run records the parameters it actually used — see each run above.' })}
    </div>`;
  },
};

/* ---- Risk ---- */

const risk = {
  async render() {
    const root = el('risk');
    root.innerHTML = loading('risk settings');
    let data;
    try { data = await getJSON('/api/risk', 'risk'); } catch (e) {
      root.innerHTML = errorState(e.message); return;
    }
    if (data.stale) return;

    const limits = data.configured_limits || {};
    const used = data.limits_used_by_run;
    const current = data.current || {};
    const ks = data.kill_switch || {};

    const limitRow = (label, key, fmt) => [
      label,
      `${fmt(limits[key])}${used && used[key] !== undefined && String(used[key]) !== String(limits[key])
        ? ` <span class="ml-1 text-[11px] text-amber-700 dark:text-amber-400">(run used ${esc(String(used[key]))})</span>` : ''}`,
    ];

    const rejectedCols = [
      { key: 'symbol', label: 'Symbol', render: (r) => esc(r.symbol) },
      { key: 'submitted_at', label: 'When', render: (r) => ts(r.submitted_at) },
      { key: 'rejection_reason', label: 'Reason', render: (r) => `<span class="text-amber-800 dark:text-amber-300">${esc(r.rejection_reason)}</span>` },
    ];

    root.innerHTML = `<div class="space-y-4">
      ${panel('Kill switch', `<div class="flex flex-wrap items-center gap-3 px-4 py-4">
          <div class="text-2xl">${ks.enabled ? badge('ENABLED', 'red') : badge('DISABLED', 'green')}</div>
          <div class="min-w-0 flex-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">
            <div>${esc(ks.effect || '')}</div>
            <div class="mt-1">${esc(ks.why_read_only || '')}</div>
          </div>
        </div>`, { note: 'Read-only. This dashboard intentionally exposes no control that changes a risk setting or the kill switch.' })}

      <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        ${card('Open positions', num(current.open_positions, 0), { hint: `Limit: ${limits.max_open_positions}` })}
        ${card('Exposure', money(current.exposure_value))}
        ${card('Exposure %', pct(current.exposure_pct), { hint: `Limit: ${(limits.max_total_exposure_pct * 100).toFixed(0)}%` })}
        ${card('Realized net P&L', pnl(current.realized_net_pnl))}
      </div>

      ${data.breaches.length
        ? notice(`Risk limit breaches detected: ${data.breaches.map((b) => `${b.limit} — ${b.detail}`).join('; ')}`, 'red')
        : notice('No risk-limit breach detected in the recorded run.', 'slate')}

      ${panel('Configured limits', kv([
        limitRow('Max risk per trade', 'risk_per_trade_pct', (v) => pct(v, { digits: 2 })),
        limitRow('Max position size', 'max_position_pct', (v) => pct(v, { digits: 0 })),
        limitRow('Max portfolio exposure', 'max_total_exposure_pct', (v) => pct(v, { digits: 0 })),
        limitRow('Max daily loss', 'max_daily_loss_pct', (v) => pct(v, { digits: 0 })),
        limitRow('Max open positions', 'max_open_positions', (v) => num(v, 0)),
        ['Source', `<code class="font-mono text-[11px]">${esc(data.limits_source)}</code>`],
        ['Daily P&L', `${MISSING} <span class="text-[11px] text-slate-500">per-day P&L is not persisted in this version; the max-daily-loss limit is enforced during a run but not recorded afterwards</span>`],
      ]), { note: 'Percentages are of available cash or portfolio equity as noted in app/paper/risk.py.' })}

      ${panel('Position sizing formula', `<div class="px-4 py-3 text-xs leading-relaxed"><code class="block whitespace-pre-wrap font-mono text-[11px]">${esc(data.sizing_formula)}</code></div>`)}

      ${panel('Orders rejected by risk checks', data.risk_rejected_orders.length
        ? table(rejectedCols, data.risk_rejected_orders)
        : emptyState('No rejected orders recorded', 'Nothing was blocked by a risk check in the recorded runs.'),
        { note: 'Every rejection recorded by the paper broker, including sizing-to-zero and limit breaches.' })}
    </div>`;
  },
};

/* ---- Market data ---- */

const marketdata = {
  symbol: null,
  source: 'csv',
  async render() {
    const root = el('marketdata');
    root.innerHTML = loading('market data');
    let sources, list;
    try {
      [sources, list] = await Promise.all([
        getJSON('/api/marketdata/sources', 'md-sources'),
        getJSON(`/api/marketdata/watchlist?source=${encodeURIComponent(this.source)}`, 'watchlist'),
      ]);
    } catch (e) {
      root.innerHTML = errorState(e.message); return;
    }
    if (sources.stale || list.stale) return;

    const sourcePicker = `<div class="flex flex-wrap items-center gap-2">
      <span class="text-[11px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">Source</span>
      ${(sources.sources || []).map((s) => {
        const active = s.key === this.source;
        const disabled = !s.configured;
        return `<button data-source="${esc(s.key)}" ${disabled ? 'disabled' : ''}
          title="${esc(disabled ? (s.how_to_enable || 'Not configured') : s.directory || '')}"
          class="rounded border px-2 py-1 text-xs font-medium ${active
            ? 'border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300'
            : 'border-slate-300 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800'}
            ${disabled ? 'cursor-not-allowed opacity-50' : ''}">${esc(s.label)}${disabled ? ' (not configured)' : ''}</button>`;
      }).join('')}
    </div>`;

    const caveats = (list.caveats || []).length
      ? `<ul class="list-disc space-y-1 pl-5">${list.caveats.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>`
      : '';
    const caveatPanel = caveats
      ? panel('What this data is and is not', `<div class="px-4 py-3 text-xs leading-relaxed text-slate-700 dark:text-slate-300">${caveats}</div>`)
      : '';

    const wireSources = () => {
      root.querySelectorAll('[data-source]').forEach((b) => b.addEventListener('click', () => {
        if (b.disabled) return;
        this.source = b.dataset.source;
        this.symbol = null;
        this.render();
      }));
    };

    if (!list.rows.length) {
      root.innerHTML = `<div class="space-y-4">
        ${panel('Data source', `<div class="px-4 py-3">${sourcePicker}</div>`)}
        ${caveatPanel}
        ${panel('Watchlist', emptyState(
          list.error ? 'This source could not be read' : 'No symbols available from this source',
          list.error || `Nothing found in ${list.directory}. For CSV, regenerate samples with: python scripts\\generate_sample_data.py`))}
      </div>`;
      wireSources();
      return;
    }

    if (!this.symbol || !list.rows.some((r) => r.symbol === this.symbol)) {
      this.symbol = list.rows[0].symbol;
    }

    let bars;
    try {
      bars = await getJSON(
        `/api/marketdata/bars/${encodeURIComponent(this.symbol)}?limit=240&source=${encodeURIComponent(this.source)}`,
        'bars',
      );
    } catch (e) {
      bars = { error: e.message, rows: [], provider_status: 'ERROR' };
    }
    if (bars.stale) return;

    const wlCols = [
      { key: 'symbol', label: 'Symbol', render: (r) => `<button data-symbol="${esc(r.symbol)}" class="font-medium ${r.symbol === this.symbol ? 'text-indigo-700 underline dark:text-indigo-400' : 'hover:underline'}">${esc(r.symbol)}</button>` },
      { key: 'file', label: 'File', render: (r) => r.file ? `<code class="font-mono text-[11px]">${esc(r.file)}</code>` : '<span class="text-slate-400" title="A bhavcopy is one file per DATE covering every symbol, so there is no per-symbol file">per-date file</span>' },
      { key: 'data_kind', label: 'Data kind', render: (r) => dataKindBadge(r.data_kind) },
      { key: 'file_modified_ist', label: 'File modified', render: (r) => r.file_modified_ist ? ts(r.file_modified_ist) : '<span class="text-slate-400">—</span>' },
      { key: 'size_bytes', label: 'Size', align: 'right', render: (r) => r.size_bytes === null || r.size_bytes === undefined ? '<span class="text-slate-400">—</span>' : `<span class="nums">${(r.size_bytes / 1024).toFixed(1)} KB</span>` },
    ];

    const barCols = [
      { key: 'timestamp', label: 'Timestamp', render: (r) => ts(r.timestamp) },
      { key: 'open', label: 'Open', align: 'right', render: (r) => num(r.open) },
      { key: 'high', label: 'High', align: 'right', render: (r) => num(r.high) },
      { key: 'low', label: 'Low', align: 'right', render: (r) => num(r.low) },
      { key: 'close', label: 'Close', align: 'right', render: (r) => num(r.close) },
      { key: 'volume', label: 'Volume', align: 'right', render: (r) => num(r.volume, 0) },
      { key: 'quality', label: 'Quality', render: (r) => statusBadge(r.quality) },
    ];

    const latest = bars.latest_bar;
    const detail = bars.provider_status === 'OK' ? `
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        ${card('Latest bar', latest ? dateOnly(latest.timestamp) : MISSING)}
        ${card('Close', latest ? num(latest.close) : MISSING)}
        ${card('Bars available', num(bars.bar_count, 0))}
        ${card('Data kind', dataKindBadge(bars.data_kind), { hint: bars.quote_available === false ? 'No quote available from this source' : '' })}
      </div>
      <div class="grid gap-3 lg:grid-cols-2">
        ${panel(`${this.symbol} — OHLC`, candleChart(bars.rows), { note: `Source: ${bars.source}. Read at ${bars.retrieved_at}. ${bars.note}` })}
        ${panel('Latest OHLCV', kv([
          ['Open', num(latest.open)], ['High', num(latest.high)], ['Low', num(latest.low)],
          ['Close', num(latest.close)], ['Volume', num(latest.volume, 0)],
          ['Quality', statusBadge(latest.quality)],
          ['Quality notes', (latest.quality_notes || []).length ? esc(latest.quality_notes.join('; ')) : '<span class="text-slate-500">none</span>'],
          ['Interval', badge(bars.interval, 'slate')],
          ['First bar', dateOnly(bars.first_timestamp)],
          ['Latest bar', dateOnly(bars.latest_timestamp)],
        ]))}
      </div>
      ${panel('Validation issues', bars.issue_count
        ? `<div class="px-4 py-2 text-xs text-amber-800 dark:text-amber-300">${bars.issue_count} bar(s) flagged</div>` +
          table([
            { key: 'timestamp', label: 'Timestamp', render: (r) => ts(r.timestamp) },
            { key: 'quality', label: 'Quality', render: (r) => statusBadge(r.quality) },
            { key: 'notes', label: 'Notes', render: (r) => esc((r.notes || []).join('; ')) },
          ], bars.issues)
        : emptyState('No validation issues', 'Every bar passed OHLC consistency, duplicate, gap and staleness checks.'),
        { note: 'Flags come from app/data/validation.py. A flagged bar is not silently repaired: the strategy returns NO_TRADE on non-OK data.' })}
      ${panel('Recent bars', table(barCols, bars.rows.slice(-60).reverse()))}
    ` : notice(bars.error || 'The provider could not read this symbol.', 'red');

    root.innerHTML = `<div class="space-y-4">
      ${panel('Data source', `<div class="px-4 py-3">${sourcePicker}</div>`, { note: sources.note })}
      ${notice(`Provider: ${list.provider} — file-based. ${list.note || ''}`, 'blue')}
      ${caveatPanel}
      ${panel('Watchlist', table(wlCols, list.rows), { note: `Directory: ${list.directory}. There is no scanner or scheduler polling this automatically.${(list.files_loaded || []).length ? ` Files loaded: ${list.files_loaded.join(', ')}.` : ''}` })}
      ${detail}
    </div>`;

    wireSources();
    root.querySelectorAll('[data-symbol]').forEach((b) => b.addEventListener('click', () => {
      this.symbol = b.dataset.symbol; this.render();
    }));
  },
};

/* ---- Database explorer ---- */

const database = {
  table: null,
  state: { page: 1, page_size: 50, search: '', sort: '', order: 'desc', date_from: '', date_to: '', symbol: '' },
  async render() {
    const root = el('database');
    root.innerHTML = loading('database schema');
    let meta;
    try { meta = await getJSON('/api/db/tables', 'db-tables'); } catch (e) {
      root.innerHTML = errorState(e.message); return;
    }
    if (meta.stale) return;

    const available = meta.tables.filter((t) => t.row_count !== null);
    if (!this.table && available.length) this.table = (available.find((t) => t.row_count > 0) || available[0]).name;

    const tableCols = [
      { key: 'name', label: 'Table', render: (t) => `<button data-table="${esc(t.name)}" class="font-medium ${t.name === this.table ? 'text-indigo-700 underline dark:text-indigo-400' : 'hover:underline'}">${esc(t.name)}</button>` },
      { key: 'row_count', label: 'Rows', align: 'right', render: (t) => t.row_count === null ? badge('missing', 'red', t.error || '') : num(t.row_count, 0) },
      { key: 'column_count', label: 'Columns', align: 'right', render: (t) => t.column_count === null ? MISSING : num(t.column_count, 0) },
    ];

    const db = meta.database || {};
    const caps = meta.capabilities || {};

    root.innerHTML = `<div class="space-y-4">
      ${notice(`Read-only explorer. ${caps.note}`, 'blue')}
      <div class="grid gap-3 lg:grid-cols-3">
        <div class="lg:col-span-1 space-y-3">
          ${panel('Tables', table(tableCols, meta.tables))}
          ${panel('Database', kv([
            ['Location', `<code class="break-all font-mono text-[11px]">${esc(db.path)}</code>`],
            ['Size', `<span class="nums">${((db.size_bytes || 0) / 1024).toFixed(1)} KB</span>`],
            ['Journal mode', badge(db.journal_mode, 'slate')],
            ['Access', badge(db.access, 'green')],
            ['Last write', ts(db.last_write_at)],
            ['Write / delete / SQL', `${badge('disabled', 'green', 'This page cannot modify data')}`],
          ]), { note: 'This application stores no credentials, API keys or tokens, so none can be exposed here.' })}
        </div>
        <div class="lg:col-span-2" id="db-rows">${loading('rows')}</div>
      </div>
    </div>`;

    root.querySelectorAll('[data-table]').forEach((b) => b.addEventListener('click', () => {
      this.table = b.dataset.table;
      this.state = { page: 1, page_size: 50, search: '', sort: '', order: 'desc', date_from: '', date_to: '', symbol: '' };
      this.render();
    }));

    if (this.table) this.renderRows();
  },

  async renderRows() {
    const host = document.getElementById('db-rows');
    if (!host) return;
    let data;
    try {
      data = await getJSON(`/api/db/tables/${encodeURIComponent(this.table)}${qs({ ...this.state })}`, 'db-rows');
    } catch (e) { host.innerHTML = errorState(e.message); return; }
    if (data.stale) return;

    const cols = data.columns.map((c) => ({
      key: c.name, label: c.name, sort: c.name,
      render: (row) => {
        const value = row[c.name];
        if (value === null || value === undefined) {
          return '<span class="text-slate-400 dark:text-slate-500" title="SQL NULL">NULL</span>';
        }
        const text = String(value);
        const looksTime = /^\d{4}-\d{2}-\d{2}T/.test(text);
        if (looksTime) return ts(text);
        if (typeof value === 'number') return num(value, 4);
        return `<span class="block max-w-xs truncate font-mono text-[11px]" title="${esc(text)}">${esc(text)}</span>`;
      },
    }));

    const schemaRows = data.columns.map((c) =>
      `<span class="mr-2 inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] dark:bg-slate-800"><code class="font-mono">${esc(c.name)}</code><span class="text-slate-500">${esc(c.type)}</span>${c.primary_key ? badge('PK', 'blue') : ''}${c.notnull ? badge('NOT NULL', 'slate') : ''}</span>`
    ).join('');

    const controls = `
      <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">Search
        <input data-dbsearch type="search" value="${esc(this.state.search)}" placeholder="text columns" class="${inputCls} w-36"></label>
      ${data.date_column ? `
        <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">From
          <input data-dbfrom type="date" value="${esc(this.state.date_from)}" class="${inputCls}"></label>
        <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">To
          <input data-dbto type="date" value="${esc(this.state.date_to)}" class="${inputCls}"></label>` : ''}
      ${data.columns.some((c) => c.name === 'symbol') ? `
        <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">Symbol
          <input data-dbsymbol type="text" value="${esc(this.state.symbol)}" placeholder="e.g. SAMPLE_TCS" class="${inputCls} w-32"></label>` : ''}
      <label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">Rows
        <select data-dbsize class="${inputCls}">${[25, 50, 100, 200].map((n) => `<option ${this.state.page_size === n ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
      ${linkBtn(`/api/db/export/${encodeURIComponent(this.table)}.csv${qs({ ...this.state })}`, 'Export CSV')}`;

    const body = data.rows.length
      ? table(cols, data.rows, {
          sortable: data.columns.map((c) => c.name), sort: data.sort, order: data.order,
          onSortAttr: 'data-dbsort',
          rowAttr: (row) => `data-dbrow="${esc(encodeURIComponent(JSON.stringify(row)))}"`,
        }) + pager(data, 'data-dbpage')
      : emptyState(data.total === 0 ? `Table "${data.table}" is empty` : 'No rows match these filters',
          data.total === 0 ? 'Nothing has been written to this table yet. That is a fact about the data, not an error.' : 'Clear the filters to see all rows.');

    host.innerHTML = `${panel(`${data.table} — rows`, body, { actions: controls })}
      <div class="mt-3">${panel('Schema', `<div class="px-4 py-3 leading-7">${schemaRows}</div>`, { note: `Column names and declared SQLite types for "${data.table}". ${data.date_column ? `Date filters apply to "${data.date_column}".` : 'This table has no timestamp column, so date filters are unavailable.'}` })}</div>`;

    host.querySelectorAll('[data-dbsort]').forEach((b) => b.addEventListener('click', () => {
      const key = b.dataset.dbsort;
      this.state.order = this.state.sort === key && this.state.order === 'desc' ? 'asc' : 'desc';
      this.state.sort = key; this.state.page = 1; this.renderRows();
    }));
    host.querySelectorAll('[data-dbpage]').forEach((b) => b.addEventListener('click', () => {
      this.state.page = Number(b.dataset.dbpage); this.renderRows();
    }));
    const search = host.querySelector('[data-dbsearch]');
    if (search) {
      let timer;
      search.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => { this.state.search = search.value.trim(); this.state.page = 1; this.renderRows(); }, 250);
      });
    }
    const bind = (sel, key) => {
      const node = host.querySelector(sel);
      if (node) node.addEventListener('change', () => { this.state[key] = node.value; this.state.page = 1; this.renderRows(); });
    };
    bind('[data-dbfrom]', 'date_from');
    bind('[data-dbto]', 'date_to');
    bind('[data-dbsymbol]', 'symbol');
    const size = host.querySelector('[data-dbsize]');
    if (size) size.addEventListener('change', () => { this.state.page_size = Number(size.value); this.state.page = 1; this.renderRows(); });

    host.querySelectorAll('tr[data-dbrow]').forEach((tr) => {
      tr.classList.add('cursor-pointer');
      tr.setAttribute('tabindex', '0');
      const open = () => {
        const row = JSON.parse(decodeURIComponent(tr.dataset.dbrow));
        showDrawer(`${data.table} record`, kv(Object.entries(row).map(([k, v]) => [
          k, v === null || v === undefined
            ? '<span class="text-slate-400">NULL</span>'
            : `<span class="break-all font-mono text-[11px]">${esc(String(v))}</span>`,
        ])));
      };
      tr.addEventListener('click', open);
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    });
  },
};

/* ---- Health ---- */

const health = {
  severity: '',
  async render() {
    const root = el('health');
    root.innerHTML = loading('system health');
    let data;
    try { data = await getJSON('/api/health', 'health'); } catch (e) {
      root.innerHTML = errorState(e.message); return;
    }
    if (data.stale) return;

    const compCards = data.components.map((c) => `
      <div class="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
        <div class="pt-0.5">${statusBadge(c.status)}</div>
        <div class="min-w-0">
          <div class="text-sm font-medium">${esc(c.name)}</div>
          <div class="mt-0.5 break-words text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">${esc(c.detail || '')}</div>
        </div>
      </div>`).join('');

    const events = this.severity
      ? data.events.filter((e) => e.severity === this.severity)
      : data.events;

    const eventCols = [
      { key: 'ts', label: 'Timestamp', render: (r) => ts(r.ts) },
      { key: 'severity', label: 'Severity', render: (r) => statusBadge(r.severity === 'INFO' ? 'OK' : r.severity === 'WARNING' ? 'DEGRADED' : r.severity === 'ERROR' ? 'UNHEALTHY' : r.severity) + ` <span class="text-[11px] text-slate-500">${esc(r.severity)}</span>` },
      { key: 'component', label: 'Component', render: (r) => badge(r.component, 'slate') },
      { key: 'message', label: 'Message', render: (r) => esc(r.message) },
      { key: 'run_id', label: 'Run', render: (r) => r.run_id ? `<code class="font-mono text-[11px]">${esc(String(r.run_id).slice(0, 8))}</code>` : '<span class="text-slate-400">—</span>' },
      { key: 'detail_json', label: 'Detail', render: (r) => r.detail_json && r.detail_json !== '{}' ? `<span class="block max-w-md truncate font-mono text-[11px]" title="${esc(r.detail_json)}">${esc(r.detail_json)}</span>` : '<span class="text-slate-400">—</span>' },
    ];

    const sevSelect = `<label class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">Severity
      <select data-sev class="${inputCls}">
        <option value="">All</option>
        ${(data.event_severities || []).map((s) => `<option ${this.severity === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
      </select></label>`;

    root.innerHTML = `<div class="space-y-4">
      ${notice('A component that was never built reports NOT_IMPLEMENTED, and one that could not be checked reports UNKNOWN. Neither is reported as HEALTHY.', 'blue')}
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">${compCards}</div>
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
        ${card('Checked at', ts(data.checked_at_ist))}
        ${card('Last successful DB write', ts(data.last_successful_db_write))}
        ${card('Recorded events', num(data.events.length, 0))}
      </div>
      ${panel('Application events', events.length
        ? table(eventCols, events)
        : emptyState('No events recorded', data.log_note), { actions: sevSelect, note: data.log_note })}
    </div>`;

    const sev = root.querySelector('[data-sev]');
    if (sev) sev.addEventListener('change', () => { this.severity = sev.value; this.render(); });
  },
};

/* ------------------------------------------------------- detail drawer */

const drawer = document.getElementById('drawer');
const drawerScrim = document.getElementById('drawer-scrim');
const drawerBody = document.getElementById('drawer-body');
const drawerTitle = document.getElementById('drawer-title');
let lastFocus = null;

function showDrawer(title, html) {
  lastFocus = document.activeElement;
  drawerTitle.textContent = title;
  drawerBody.innerHTML = html;
  drawer.classList.remove('translate-x-full');
  drawerScrim.classList.remove('hidden');
  drawer.focus();
}

function hideDrawer() {
  drawer.classList.add('translate-x-full');
  drawerScrim.classList.add('hidden');
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

document.getElementById('drawer-close').addEventListener('click', hideDrawer);
drawerScrim.addEventListener('click', hideDrawer);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideDrawer(); });

async function openDetail(id, kind) {
  showDrawer(kind === 'trade' ? 'Trade detail' : 'Signal detail', loading('detail'));
  try {
    if (kind === 'trade') {
      const data = await getJSON(`/api/trades/${encodeURIComponent(id)}`, 'detail');
      if (data.stale) return;
      drawerBody.innerHTML = renderTradeDetail(data);
    } else {
      const [runId, signalId] = id.split('|');
      const data = await getJSON(`/api/signals/${encodeURIComponent(runId)}/${encodeURIComponent(signalId)}`, 'detail');
      if (data.stale) return;
      drawerBody.innerHTML = renderSignalDetail(data);
    }
  } catch (e) {
    drawerBody.innerHTML = errorState(e.message);
  }
}

function renderTradeDetail({ trade, orders: ords, signals: sigs, run, cost_assumptions: costs }) {
  const lifecycle = ords.map((o) => {
    const eco = o.economics || {};
    return `<div class="rounded border border-slate-200 p-3 dark:border-slate-800">
      <div class="flex flex-wrap items-center gap-2">
        ${statusBadge(o.side)} ${statusBadge(o.status)}
        <code class="font-mono text-[11px]">${esc(String(o.order_id).slice(0, 8))}</code>
        <span class="ml-auto text-[11px] text-slate-500">${esc(o.order_type || 'MARKET')}</span>
      </div>
      ${kv([
        ['Requested qty', num(o.quantity, 0)],
        ['Filled qty', num(o.filled_quantity, 0)],
        ['Pre-slippage price', eco.fill_price_basis !== null && eco.fill_price_basis !== undefined ? `${num(eco.fill_price_basis)} <span class="text-[10px] text-slate-400">derived</span>` : (eco.unavailable_reason ? `<span class="text-slate-400">${esc(eco.unavailable_reason)}</span>` : MISSING)],
        ['Simulated fill price', num(o.filled_price)],
        ['Slippage (total)', eco.slippage_total !== null && eco.slippage_total !== undefined ? `${money(eco.slippage_total)} <span class="text-[10px] text-slate-400">derived</span>` : MISSING],
        ['Costs (this leg)', eco.costs !== null && eco.costs !== undefined ? `${money(eco.costs)} <span class="text-[10px] text-slate-400">derived</span>` : MISSING],
        ['Submitted', ts(o.submitted_at)],
        ['Filled', o.filled_at ? ts(o.filled_at) : '<span class="text-slate-400">never filled</span>'],
        ['Reject / cancel reason', o.rejection_reason ? esc(o.rejection_reason) : '<span class="text-slate-400">—</span>'],
      ])}
    </div>`;
  }).join('<div class="h-2"></div>');

  const signalBlocks = sigs.map((s) => `
    <div class="rounded border border-slate-200 p-3 dark:border-slate-800">
      <div class="flex flex-wrap items-center gap-2">${statusBadge(s.action)} ${badge(s.strategy_name, String(s.strategy_name).startsWith('engine:') ? 'violet' : 'blue')} ${ts(s.timestamp)}</div>
      <p class="mt-2 text-xs leading-relaxed">${esc(s.reason)}</p>
      ${s.inputs && Object.keys(s.inputs).length ? `<div class="mt-2">${kv(Object.entries(s.inputs).map(([k, v]) => [k, `<span class="nums font-mono text-[11px]">${esc(typeof v === 'number' ? NUM.format(v) : JSON.stringify(v))}</span>`]))}</div>` : ''}
    </div>`).join('<div class="h-2"></div>');

  return `<div class="space-y-4">
    <div class="grid grid-cols-2 gap-2">
      ${card('Symbol', `<span class="text-base">${esc(trade.symbol)}</span>`)}
      ${card('Net P&L', pnl(trade.net_pnl))}
    </div>
    ${trade.ambiguous_exit ? notice('Stop-loss and target were both touched inside the same bar. OHLC data cannot reveal which happened first, so the engine assumed the stop filled (the worse outcome). Treat this result as uncertain.', 'amber') : ''}
    ${panel('Trade', kv([
      ['Trade ID', `<code class="break-all font-mono text-[11px]">${esc(trade.trade_id)}</code>`],
      ['Run ID', trade.run_id ? `<code class="break-all font-mono text-[11px]">${esc(trade.run_id)}</code>` : MISSING],
      ['Strategy', trade.strategy_name ? badge(trade.strategy_name, 'blue') : MISSING],
      ['Direction', statusBadge(trade.side || 'LONG')],
      ['Status', statusBadge(trade.status || 'CLOSED')],
      ['Quantity', num(trade.quantity, 0)],
      ['Entry', `${ts(trade.entry_at)} @ ${num(trade.entry_price)}`],
      ['Exit', `${ts(trade.exit_at)} @ ${num(trade.exit_price)}`],
      ['Stop-loss', num(trade.stop_loss)],
      ['Target', num(trade.target)],
      ['Gross P&L', pnl(trade.gross_pnl)],
      ['Costs (both legs)', money(trade.costs)],
      ['Net P&L', pnl(trade.net_pnl)],
      ['Exit reason', esc(trade.exit_reason)],
      ['Recorded at', ts(trade.recorded_at)],
    ]))}
    ${panel('Simulated orders', `<div class="p-3">${lifecycle || emptyState('No order records linked')}</div>`, { note: 'Simulated fills. A real exchange order may fill at a different price, partially, or not at all.' })}
    ${panel('Signals behind this trade', `<div class="p-3">${signalBlocks || emptyState('No signal records linked')}</div>`)}
    ${costs ? panel('Cost assumptions used by this run', kv(Object.entries(costs).map(([k, v]) => [k, `<span class="nums">${esc(String(v))}</span>`])), { note: 'Unverified user-configurable assumptions, recorded with the run.' }) : ''}
    ${run ? panel('Run', kv([
      ['Symbols', esc((run.symbols || []).join(', '))],
      ['Interval', esc(run.interval || '')],
      ['Date range', `${esc(run.start_date || '?')} → ${esc(run.end_date || '?')}`],
      ['Data sources', Object.entries(run.data_sources || {}).map(([s, src]) => `${esc(s)}: <code class="font-mono text-[11px]">${esc(src)}</code>`).join('<br>')],
    ])) : ''}
  </div>`;
}

function renderSignalDetail({ signal, orders: ords, trade }) {
  const inputs = signal.inputs || {};
  return `<div class="space-y-4">
    <div class="flex flex-wrap items-center gap-2">
      ${statusBadge(signal.action)} ${statusBadge(signal.outcome || 'UNKNOWN')}
      ${badge(signal.strategy_name, String(signal.strategy_name).startsWith('engine:') ? 'violet' : 'blue',
        String(signal.strategy_name).startsWith('engine:') ? 'Engine-initiated exit (stop, target or session flatten), not a strategy entry signal' : '')}
    </div>
    ${panel('Decision', kv([
      ['Signal ID', `<code class="break-all font-mono text-[11px]">${esc(signal.signal_id)}</code>`],
      ['Symbol', `<span class="font-medium">${esc(signal.symbol)}</span>`],
      ['Signal time', ts(signal.timestamp)],
      ['Action', statusBadge(signal.action)],
      ['Outcome', statusBadge(signal.outcome || 'UNKNOWN')],
      ['Stop-loss', num(signal.stop_loss)],
      ['Target', num(signal.target)],
      ['Linked order', signal.order_id ? `<code class="font-mono text-[11px]">${esc(signal.order_id)}</code>` : '<span class="text-slate-400">none</span>'],
      ['Linked trade', signal.trade_id ? `<code class="font-mono text-[11px]">${esc(signal.trade_id)}</code>` : '<span class="text-slate-400">none</span>'],
    ]))}
    ${panel('Reason', `<p class="px-4 py-3 text-xs leading-relaxed">${esc(signal.reason)}</p>`,
      { note: 'The strategy\'s own explanation, recorded at decision time.' })}
    ${panel('Rule inputs at decision time', Object.keys(inputs).length
      ? kv(Object.entries(inputs).map(([k, v]) => [k, `<span class="nums font-mono text-[11px]">${esc(typeof v === 'number' ? NUM.format(v) : JSON.stringify(v))}</span>`]))
      : emptyState('No inputs recorded for this signal'),
      { note: 'The exact indicator values the rule saw. No confidence or strength score is shown because no strategy in this version computes one.' })}
    ${panel('Orders from this signal', ords.length ? table([
      { key: 'order_id', label: 'Order', render: (o) => `<code class="font-mono text-[11px]">${esc(String(o.order_id).slice(0, 8))}</code>` },
      { key: 'status', label: 'Status', render: (o) => statusBadge(o.status) },
      { key: 'quantity', label: 'Qty', align: 'right', render: (o) => num(o.quantity, 0) },
      { key: 'filled_price', label: 'Fill ₹', align: 'right', render: (o) => num(o.filled_price) },
      { key: 'rejection_reason', label: 'Reason', render: (o) => o.rejection_reason ? esc(o.rejection_reason) : '—' },
    ], ords) : emptyState('No order was created', signal.action === 'NO_TRADE'
        ? 'This was a NO_TRADE decision, so no order exists. That is the expected outcome, not a failure.'
        : 'The engine never queued this signal — for example because the session entry window had closed.'))}
    ${trade ? panel('Resulting trade', kv([
      ['Trade ID', `<code class="break-all font-mono text-[11px]">${esc(trade.trade_id)}</code>`],
      ['Net P&L', pnl(trade.net_pnl)],
      ['Exit reason', esc(trade.exit_reason)],
    ])) : ''}
  </div>`;
}

/* --------------------------------------------------------------- routing */

const PAGES = [
  { id: 'overview', label: 'Overview', icon: '◱', view: overview },
  { id: 'trades', label: 'Trades', icon: '⇄', view: trades },
  { id: 'signals', label: 'Signals', icon: '◎', view: signals },
  { id: 'orders', label: 'Orders', icon: '≡', view: orders },
  { id: 'positions', label: 'Positions', icon: '▣', view: positions },
  { id: 'strategies', label: 'Strategies', icon: '⌁', view: strategies },
  { id: 'risk', label: 'Risk', icon: '⚠', view: risk },
  { id: 'marketdata', label: 'Market data', icon: '▤', view: marketdata },
  { id: 'database', label: 'Database', icon: '⛁', view: database },
  { id: 'health', label: 'System health', icon: '✚', view: health },
];

const navList = document.getElementById('nav');
navList.innerHTML = PAGES.map((p) => `
  <li><a href="#${p.id}" data-nav="${p.id}" class="flex items-center gap-2 rounded px-2.5 py-2 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800">
    <span aria-hidden="true" class="w-4 text-center opacity-70">${p.icon}</span>${esc(p.label)}
  </a></li>`).join('');

let currentPage = null;

function show(id) {
  const page = PAGES.find((p) => p.id === id) || PAGES[0];
  currentPage = page;
  document.querySelectorAll('[data-page]').forEach((s) => s.classList.toggle('active', s.dataset.page === page.id));
  document.querySelectorAll('[data-nav]').forEach((a) => {
    const active = a.dataset.nav === page.id;
    a.classList.toggle('bg-indigo-50', active);
    a.classList.toggle('text-indigo-700', active);
    a.classList.toggle('font-medium', active);
    a.classList.toggle('dark:bg-indigo-950', active);
    a.classList.toggle('dark:text-indigo-300', active);
    if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  document.getElementById('page-title').textContent = page.label;
  document.title = `${page.label} — Smart Trader (paper trading)`;
  closeSidebar();
  page.view.render();
}

window.addEventListener('hashchange', () => show(location.hash.slice(1)));

/* ---------------------------------------------------------- status strip */

async function refreshStatus() {
  let data;
  try { data = await getJSON('/api/status', 'status'); } catch (e) {
    setDot('db-dot', 'bg-rose-500'); document.getElementById('db-text').textContent = 'unreachable';
    showGlobalAlert(e.message);
    return;
  }
  if (data.stale) return;
  hideGlobalAlert();

  const session = data.market_session || {};
  document.getElementById('session-text').textContent = session.status || 'UNKNOWN';
  setDot('session-dot', session.status === 'OPEN' ? 'bg-emerald-500' : session.status === 'CLOSED' ? 'bg-slate-400' : 'bg-amber-500');
  document.getElementById('session-dot').title = session.caveat || '';

  const db = data.database || {};
  document.getElementById('db-text').textContent = db.status === 'HEALTHY'
    ? `ok (${(db.table_counts || {}).trades ?? 0} trades)` : db.status;
  setDot('db-dot', db.status === 'HEALTHY' ? 'bg-emerald-500' : db.status === 'UNKNOWN' ? 'bg-amber-500' : 'bg-rose-500');

  const provider = data.data_provider || {};
  document.getElementById('provider-text').textContent = `${provider.name} (${provider.symbol_count} symbols, not live)`;
  document.getElementById('provider-text').title = provider.note || '';

  document.getElementById('sidebar-db').textContent = db.path || '';
  document.getElementById('sidebar-db').title = db.path || '';
  document.getElementById('sidebar-access').innerHTML =
    `Read-only API · ${db.last_write_at ? `last write ${esc(String(db.last_write_at).slice(0, 16).replace('T', ' '))}` : 'no writes recorded'}`;

  document.getElementById('refreshed-text').textContent =
    new Date().toLocaleTimeString('en-IN', { hour12: false });

  if (db.status !== 'HEALTHY') {
    showGlobalAlert(db.detail
      ? `Ledger problem: ${db.detail}`
      : `Ledger status is ${db.status}. Run a backtest with --db to create the database.`);
  }
}

function setDot(id, cls) {
  const node = document.getElementById(id);
  node.className = `h-2 w-2 rounded-full ${cls}`;
}

function showGlobalAlert(message) {
  const bar = document.getElementById('global-alert');
  bar.textContent = message;
  bar.classList.remove('hidden');
}
function hideGlobalAlert() {
  document.getElementById('global-alert').classList.add('hidden');
}

/* ----------------------------------------------------------------- chrome */

const sidebar = document.getElementById('sidebar');
const sidebarScrim = document.getElementById('sidebar-scrim');
const sidebarOpen = document.getElementById('sidebar-open');

function openSidebar() {
  sidebar.classList.remove('-translate-x-full');
  sidebarScrim.classList.remove('hidden');
  sidebarOpen.setAttribute('aria-expanded', 'true');
}
function closeSidebar() {
  if (window.innerWidth < 1024) {
    sidebar.classList.add('-translate-x-full');
    sidebarScrim.classList.add('hidden');
    sidebarOpen.setAttribute('aria-expanded', 'false');
  }
}
sidebarOpen.addEventListener('click', openSidebar);
document.getElementById('sidebar-close').addEventListener('click', closeSidebar);
sidebarScrim.addEventListener('click', closeSidebar);

document.getElementById('refresh').addEventListener('click', () => {
  refreshStatus();
  if (currentPage) currentPage.view.render();
});

const THEME_KEY = 'smart-trader-theme';
function applyTheme(mode) {
  document.documentElement.classList.toggle('dark', mode === 'dark');
  document.documentElement.classList.toggle('light', mode === 'light');
}
document.getElementById('theme-toggle').addEventListener('click', () => {
  const isDark = document.documentElement.classList.contains('dark')
    || (!document.documentElement.classList.contains('light')
        && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const next = isDark ? 'light' : 'dark';
  applyTheme(next);
  try { localStorage.setItem(THEME_KEY, next); } catch { /* storage may be blocked */ }
});
try {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved) applyTheme(saved);
} catch { /* private mode: fall back to the media query */ }

/* ------------------------------------------------------------------ boot */

refreshStatus();
show(location.hash.slice(1));

// Poll the status strip only. Page data refreshes on navigation or on demand,
// so an idle dashboard does not hammer the API.
setInterval(refreshStatus, 30000);
