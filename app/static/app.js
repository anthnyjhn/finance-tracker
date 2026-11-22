/* Finance Tracker — single-page UI (no build step). */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const todayISO = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };

let S = null;            // latest /api/summary
let CFG = null;          // config
let moneyFmt, moneyFmt0;
let currentRefresh = null;
let charts = [];

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(path, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* ignore */ }
    toast('Error: ' + msg);
    throw new Error(msg);
  }
  return r.json();
}

function setFormats() {
  const locale = CFG.locale || 'en-AU';
  const cur = CFG.currency || 'AUD';
  moneyFmt = new Intl.NumberFormat(locale, { style: 'currency', currency: cur, minimumFractionDigits: 2, maximumFractionDigits: 2 });
  moneyFmt0 = new Intl.NumberFormat(locale, { style: 'currency', currency: cur, notation: 'compact', maximumFractionDigits: 1 });
}
const money = v => (v === null || v === undefined || Number.isNaN(v)) ? '—' : moneyFmt.format(v);
const signed = v => (v > 0 ? '+' : '') + money(v);
const cls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
const pct = (v, d = 1) => v === null || v === undefined ? '—' : (v * 100).toFixed(d) + '%';
const fdate = iso => iso ? new Date(iso + 'T00:00:00').toLocaleDateString(CFG.locale || 'en-AU', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
const months = m => m === null || m === undefined ? '—' : (m <= 0 ? 'reached' : (m < 1 ? '< 1 month' : `${m.toFixed(1)} months`));

function toast(msg) {
  const t = $('#tooltip');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2500);
}
function savedFlash() {
  const s = $('#saved'); s.textContent = 'Saved ✓';
  clearTimeout(s._h); s._h = setTimeout(() => s.textContent = '', 1500);
}

async function loadSummary() {
  S = await api('GET', '/api/summary');
  CFG = S.config;
  setFormats();
}
let refreshTimer;
function changed() {
  savedFlash();
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => { await loadSummary(); if (currentRefresh) currentRefresh(); }, 150);
}

/* ------------------------------------------------------------------ charts */

function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
const SERIES = () => [1, 2, 3, 4, 5, 6, 7, 8].map(i => cssVar('--s' + i));

function makeChart(canvas, cfg) {
  if (!canvas) return;
  const muted = cssVar('--muted'), grid = cssVar('--grid'), ink = cssVar('--ink');
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.color = muted;
  const moneyTick = v => moneyFmt0.format(v);
  const base = {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: (cfg.data.datasets.length > 1), position: 'top', align: 'start', labels: { boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 3, color: ink } },
      tooltip: { callbacks: { label: c => `${c.dataset.label ? c.dataset.label + ': ' : ''}${money(c.parsed[cfg.options?.indexAxis === 'y' ? 'x' : 'y'])}` } },
    },
    scales: {
      x: { grid: { display: false }, border: { color: cssVar('--axis') }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
      y: { grid: { color: grid }, border: { display: false }, ticks: { callback: moneyTick } },
    },
  };
  if (cfg.options?.indexAxis === 'y') {
    base.scales = {
      x: { grid: { color: grid }, border: { display: false }, ticks: { callback: moneyTick } },
      y: { grid: { display: false }, border: { color: cssVar('--axis') } },
    };
  }
  const merged = { ...cfg, options: deepMerge(base, cfg.options || {}) };
  charts.push(new Chart(canvas, merged));
}
function deepMerge(a, b) {
  const out = { ...a };
  for (const [k, v] of Object.entries(b)) out[k] = (v && typeof v === 'object' && !Array.isArray(v) && a[k]) ? deepMerge(a[k], v) : v;
  return out;
}
function destroyCharts() { charts.forEach(c => c.destroy()); charts = []; }
const lineDs = (label, data, color, extra = {}) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: data.length > 40 ? 0 : 3, pointHoverRadius: 5, tension: 0.25, ...extra });

/* ------------------------------------------------------------------ editable tables */

/**
 * Mount an editable table bound to /api/t/<table>.
 * opt: {table, filter, cols:[{key,label,type,options,list,step,width}], defaults(), totals:[keys], sort(a,b), where(row), empty}
 */
async function editor(el, opt) {
  const q = new URLSearchParams(opt.filter || {}).toString();
  let rows = await api('GET', `/api/t/${opt.table}${q ? '?' + q : ''}`);
  if (opt.where) rows = rows.filter(opt.where);
  if (opt.sort) rows.sort(opt.sort);
  const listId = c => `dl-${opt.table}-${c.key}`;

  function cell(r, c) {
    const v = r[c.key];
    const attrs = `data-id="${r.id}" data-key="${c.key}" aria-label="${esc(c.label)}"`;
    if (c.type === 'select') {
      return `<select ${attrs}>${c.options.map(o => { const [val, lab] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(val)}" ${String(v ?? '') === String(val) ? 'selected' : ''}>${esc(lab)}</option>`; }).join('')}</select>`;
    }
    if (c.type === 'check') return `<input type="checkbox" ${attrs} ${v ? 'checked' : ''}>`;
    if (c.type === 'number') return `<input type="number" step="${c.step || '0.01'}" ${attrs} value="${v ?? ''}">`;
    if (c.type === 'date') return `<input type="date" ${attrs} value="${esc(v || '')}">`;
    return `<input type="text" ${attrs} value="${esc(v ?? '')}" ${c.list ? `list="${listId(c)}"` : ''}>`;
  }
  function footer() {
    if (!opt.totals) return '';
    return `<tfoot><tr class="total">${opt.cols.map((c, i) => opt.totals.includes(c.key)
      ? `<td class="num">${money(rows.reduce((s, r) => s + (Number(r[c.key]) || 0), 0))}</td>`
      : `<td>${i === 0 ? 'Total' : ''}</td>`).join('')}<td></td></tr></tfoot>`;
  }
  function draw() {
    el.innerHTML = `
      ${opt.cols.filter(c => c.list).map(c => `<datalist id="${listId(c)}">${(typeof c.list === 'function' ? c.list() : c.list).map(o => `<option value="${esc(o)}">`).join('')}</datalist>`).join('')}
      <div class="${rows.length > 12 ? 'scroll' : ''}"><table>
        <thead><tr>${opt.cols.map(c => `<th class="${c.type === 'number' ? 'num' : ''}" ${c.width ? `style="width:${c.width}"` : ''}>${esc(c.label)}</th>`).join('')}<th style="width:32px"></th></tr></thead>
        <tbody>${rows.length ? rows.map(r => `<tr>${opt.cols.map(c => `<td>${cell(r, c)}</td>`).join('')}<td><button class="icon" data-del="${r.id}" title="Delete row" aria-label="Delete row">✕</button></td></tr>`).join('')
        : `<tr><td colspan="${opt.cols.length + 1}" class="muted small">${opt.empty || 'Nothing here yet.'}</td></tr>`}</tbody>
        ${footer()}
      </table></div>
      <div class="row" style="margin-top:8px"><button data-add>+ ${esc(opt.addLabel || 'Add row')}</button></div>`;
  }
  draw();
  el.addEventListener('change', async e => {
    const t = e.target; if (!t.dataset.key) return;
    const c = opt.cols.find(c => c.key === t.dataset.key);
    let v = c.type === 'check' ? (t.checked ? 1 : 0) : t.value;
    if (c.type === 'number') v = v === '' ? null : Number(v);
    const row = await api('PUT', `/api/t/${opt.table}/${t.dataset.id}`, { [c.key]: v });
    const i = rows.findIndex(r => r.id === row.id); rows[i] = row;
    if (opt.totals) { const tf = $('tfoot', el); if (tf) tf.outerHTML = footer(); }
    opt.onChange?.(); changed();
  });
  el.addEventListener('click', async e => {
    const del = e.target.closest('[data-del]');
    if (del) {
      if (!confirm('Delete this row?')) return;
      await api('DELETE', `/api/t/${opt.table}/${del.dataset.del}`);
      rows = rows.filter(r => r.id !== Number(del.dataset.del)); draw(); opt.onChange?.(); changed();
    }
    if (e.target.closest('[data-add]')) {
      const row = await api('POST', `/api/t/${opt.table}`, { ...(opt.filter || {}), ...(opt.defaults ? opt.defaults(rows) : {}) });
      if (opt.sort && opt.newFirst) rows.unshift(row); else rows.push(row);
      draw(); opt.onChange?.(); changed();
      const inputs = $$(`[data-id="${row.id}"]`, el); inputs[0]?.focus();
    }
  });
  return { reload: async () => { rows = await api('GET', `/api/t/${opt.table}${q ? '?' + q : ''}`); if (opt.where) rows = rows.filter(opt.where); if (opt.sort) rows.sort(opt.sort); draw(); } };
}

/* Inputs bound to config values: <input data-cfg="tax.withheld" type=number> */
function cfgInput(path, label, type = 'number', extra = '') {
  const v = path.split('.').reduce((o, k) => o?.[k], CFG);
  if (type === 'check') return `<label class="field"><span>${esc(label)}</span><input type="checkbox" data-cfg="${path}" ${v ? 'checked' : ''} ${extra}></label>`;
  if (type === 'select') return `<label class="field">${esc(label)}<select data-cfg="${path}">${extra.map(o => `<option ${o === v ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`;
  return `<label class="field">${esc(label)}<input type="${type}" ${type === 'number' ? 'step="any"' : ''} data-cfg="${path}" value="${esc(v ?? '')}" ${extra}></label>`;
}
function bindCfg(el) {
  el.addEventListener('change', async e => {
    const t = e.target; if (!t.dataset.cfg) return;
    const [top, ...rest] = t.dataset.cfg.split('.');
    let v = t.type === 'checkbox' ? t.checked : (t.type === 'number' ? Number(t.value) : t.value);
    let obj = v;
    if (rest.length) { obj = JSON.parse(JSON.stringify(CFG[top])); let o = obj; rest.slice(0, -1).forEach(k => o = o[k]); o[rest.at(-1)] = v; }
    CFG = await api('PUT', '/api/config', { [top]: obj });
    setFormats(); changed();
  });
}

const tile = (label, value, sub = '', valueCls = '') => `<div class="card tile"><div class="label">${label}</div><div class="value ${valueCls}">${value}</div><div class="sub">${sub}</div></div>`;
const bar = (p, color) => `<div class="bar" role="progressbar" aria-valuenow="${Math.round(p * 100)}" aria-valuemin="0" aria-valuemax="100"><span style="width:${Math.max(0, Math.min(100, p * 100))}%;${color ? 'background:' + color : ''}"></span></div>`;

/* ------------------------------------------------------------------ pages */

const PAGES = {};

PAGES.dashboard = { title: 'Dashboard', async render(el) {
  el.innerHTML = `<div class="tiles" id="tiles"></div>
    <div class="grid">
      <div class="card"><h2>Net worth over time</h2><div class="chart"><canvas id="cHist"></canvas></div><div id="growth" class="small muted" style="margin-top:8px"></div></div>
      <div class="card"><h2>Where your money is</h2><div class="chart"><canvas id="cComp"></canvas></div></div>
      <div class="card"><h2>Goals <a class="right small" href="#/goals">Manage</a></h2><div id="dGoals"></div></div>
      <div class="card"><h2>Money lent — repayments <a class="right small" href="#/loans">Open</a></h2><div id="dLoans"></div></div>
      <div class="card"><h2>Forecast <a class="right small" href="#/forecast">Details</a></h2><div class="chart"><canvas id="cFc"></canvas></div></div>
      <div class="card"><h2>Cash flow (last 12 months) <a class="right small" href="#/transactions">Transactions</a></h2><div id="dCf"></div></div>
    </div>`;
  return () => {
    destroyCharts();
    const nw = S.networth, g = S.growth || {}, h = S.health;
    const goal = S.goals.find(x => x.link === 'networth');
    $('#tiles').innerHTML = [
      tile('Net worth', money(nw.total), g.month ? `<span class="${cls(g.month.change)}">${signed(g.month.change)}</span> since ${fdate(g.month.from)}` : 'Tracking starts today'),
      tile('Spendable (all-in)', money(nw.spendable), 'Net worth excluding super'),
      tile('Monthly surplus (forecast)', money(S.forecast.surplus), `Savings rate ${pct(S.forecast.savings_rate)}`),
      tile('Money lent out', money(S.lent_total), `${S.loans.filter(l => l.payable > 0).length} active`),
      tile('Investments gain/loss', `<span class="${cls(S.holdings_total.profit)}">${signed(S.holdings_total.profit)}</span>`, `on ${money(S.holdings_total.invested)} invested`),
      tile('Emergency cover', h.emergency_months == null ? '—' : `${h.emergency_months.toFixed(1)} mo`, `Cash + savings ÷ monthly expenses`),
      goal ? tile(esc(goal.name), pct(goal.progress, 0), goal.achieved ? 'Achieved 🎉' : `${money(goal.left)} to go`) : '',
    ].join('');

    const snaps = S.snapshots;
    makeChart($('#cHist'), { type: 'line', data: { labels: snaps.map(s => fdate(s.date)), datasets: [lineDs('Net worth', snaps.map(s => s.networth), SERIES()[0], { fill: false })] } });
    const parts = [];
    if (g.previous) parts.push(`Since last snapshot: <span class="${cls(g.previous.change)}">${signed(g.previous.change)}</span>`);
    if (g.ytd) parts.push(`Year to date: <span class="${cls(g.ytd.change)}">${signed(g.ytd.change)}</span>`);
    if (g.avg_monthly !== undefined) parts.push(`Average: ${signed(g.avg_monthly)}/month`);
    $('#growth').innerHTML = parts.join(' · ') || 'A snapshot is saved automatically each day you open the app.';

    makeChart($('#cComp'), { type: 'bar', data: { labels: nw.groups.map(x => x.name), datasets: [{ label: 'Amount', data: nw.groups.map(x => x.amount), backgroundColor: SERIES()[0], borderRadius: 4, barThickness: 18 }] }, options: { indexAxis: 'y', plugins: { legend: { display: false } } } });

    $('#dGoals').innerHTML = goalsHTML(S.goals) || '<p class="muted small">No goals yet.</p>';
    $('#dLoans').innerHTML = S.loans.map(l => `<div class="goal"><div class="top"><span>${esc(l.name)}</span><span>${money(l.payable)} left · ${l.payoff_date ? 'paid off ~' + fdate(l.payoff_date) : (l.payable <= 0 ? 'repaid ✓' : 'no schedule')}</span></div>${bar(l.progress, SERIES()[2])}</div>`).join('') || '<p class="muted small">No loans.</p>';

    const fc = S.forecast.rows;
    makeChart($('#cFc'), { type: 'line', data: { labels: fc.map(r => 'Y' + r.year), datasets: [lineDs('Net worth', fc.map(r => r.networth), SERIES()[0]), lineDs('Cash (spendable)', fc.map(r => r.cash), SERIES()[1])] } });

    const cf = S.cashflow;
    if (cf.months.some(m => m.income || m.expenses)) {
      $('#dCf').innerHTML = '<div class="chart"><canvas id="cCf"></canvas></div>';
      makeChart($('#cCf'), { type: 'bar', data: { labels: cf.months.map(m => m.month.slice(5) + '/' + m.month.slice(2, 4)), datasets: [
        { label: 'Income', data: cf.months.map(m => m.income), backgroundColor: SERIES()[0], borderRadius: 4 },
        { label: 'Expenses', data: cf.months.map(m => m.expenses), backgroundColor: SERIES()[1], borderRadius: 4 }] } });
    } else {
      $('#dCf').innerHTML = `<p class="hint">Record income and spending on the <a href="#/transactions">Transactions</a> page (or import a bank CSV/OFX) to see monthly cash flow, savings rate and spending by category here.</p>`;
    }
  };
} };

function goalsHTML(goals) {
  return goals.map(g => `<div class="goal"><div class="top"><span>${esc(g.name)}</span><span>${money(g.current)} / ${money(g.target)}</span></div>${bar(g.progress, g.achieved ? cssVar('--good') : '')}
    <div class="small muted" style="margin-top:4px">${g.achieved ? `Achieved — ${money(-g.left)} over target` : `${money(g.left)} left · ${months(g.months_left)} at ${money(CFG.monthly_savings)}/mo · ETA ${fdate(g.eta)}`}${g.monthly_needed ? ` · need ${money(g.monthly_needed)}/mo to hit ${fdate(g.target_date)}` : ''}</div></div>`).join('');
}

const ACCOUNT_CATS = [['cash', 'Cash'], ['savings', 'Savings / bank'], ['investment', 'Investments'], ['super', 'Superannuation'], ['property', 'Property / vehicle'], ['other', 'Other asset'], ['debt', 'Debt / liability']];

PAGES.networth = { title: 'Net worth', async render(el) {
  el.innerHTML = `<div class="tiles" id="nwTiles"></div><div class="grid">
    <div class="card wide"><h2>Accounts & assets</h2>
      <p class="hint">Update balances here whenever you check your bank. Money lent, money parked with family and investment holdings (crypto) have their own pages and are added to net worth automatically. Untick “Spendable” for money you can’t access (e.g. super).</p>
      <div id="acc"></div></div>
    <div class="card"><h2>Overview</h2><div id="ov"></div></div>
    <div class="card"><h2>Goals</h2><div class="fields">${cfgInput('monthly_savings', 'Expected savings per month')}</div><p class="hint">Used to estimate time left to each goal (your sheet used $1,990 × 2). Edit goal targets on the <a href="#/goals">Goals</a> page.</p><div id="nwGoals"></div></div>
  </div>`;
  bindCfg(el);
  await editor($('#acc'), { table: 'accounts', cols: [
    { key: 'name', label: 'Name' }, { key: 'category', label: 'Category', type: 'select', options: ACCOUNT_CATS, width: '170px' },
    { key: 'balance', label: 'Balance', type: 'number', width: '150px' }, { key: 'spendable', label: 'Spendable', type: 'check', width: '80px' },
    { key: 'notes', label: 'Notes' }], totals: ['balance'], defaults: rows => ({ name: 'New account', category: 'savings', balance: 0, spendable: 1, sort: rows.length + 1 }), addLabel: 'Add account' });
  return () => {
    const nw = S.networth;
    $('#nwTiles').innerHTML = tile('Total net worth', money(nw.total)) + tile('Spendable (all-in)', money(nw.spendable)) + tile('Assets', money(nw.assets)) + tile('Liabilities', money(nw.liabilities));
    $('#ov').innerHTML = `<table><thead><tr><th>Category</th><th class="num">Amount</th><th class="num">Share</th></tr></thead><tbody>${nw.groups.map(g => `
      <tr><td><b>${esc(g.name)}</b></td><td class="num"><b>${money(g.amount)}</b></td><td class="num">${pct(nw.total ? g.amount / nw.total : 0)}</td></tr>
      ${nw.lines.filter(l => l.group === g.name).length > 1 || nw.lines.find(l => l.group === g.name)?.name !== g.name ? nw.lines.filter(l => l.group === g.name).map(l => `<tr class="sub"><td style="padding-left:20px">${esc(l.name)}${l.spendable ? '' : ' <span class="pill">not spendable</span>'}</td><td class="num">${money(l.amount)}</td><td></td></tr>`).join('') : ''}`).join('')}
      <tr class="total"><td>Total</td><td class="num">${money(nw.total)}</td><td></td></tr>
      <tr><td>Spendable (all-in)</td><td class="num">${money(nw.spendable)}</td><td></td></tr></tbody></table>`;
    $('#nwGoals').innerHTML = goalsHTML(S.goals.filter(g => ['networth', 'spendable'].includes(g.link)));
  };
} };

const PERIOD_PER_MONTH = { weekly: 52 / 12, fortnightly: 26 / 12, monthly: 1 };

PAGES.budget = { title: 'Pay budget', async render(el) {
  el.innerHTML = `<div class="grid">
    <div class="card"><h2>Income per pay</h2><div class="fields">${cfgInput('budget_income', 'Net pay')}${cfgInput('budget_period', 'Pay frequency', 'select', ['weekly', 'fortnightly', 'monthly'])}</div>
      <p class="hint">Fixed amounts come out first; whatever is left is split by percentage.</p></div>
    <div class="card"><h2>Fixed amounts</h2><div id="bFixed"></div></div>
    <div class="card"><h2>Split the rest by percentage</h2><div id="bPct"></div></div>
    <div class="card wide"><h2>Where each pay goes</h2><div id="bOut"></div>
      <details><summary>Split a different amount (e.g. a bonus)</summary><div class="row" style="margin-top:8px"><input type="number" id="bAlt" placeholder="Amount" step="any"><span id="bAltOut" class="small"></span></div></details></div>
  </div>`;
  bindCfg(el);
  await editor($('#bFixed'), { table: 'budget_items', filter: { kind: 'fixed' }, cols: [{ key: 'name', label: 'Fund' }, { key: 'value', label: 'Amount', type: 'number', width: '130px' }], totals: ['value'], defaults: () => ({ name: 'New fund', value: 0 }), addLabel: 'Add fixed amount' });
  await editor($('#bPct'), { table: 'budget_items', filter: { kind: 'percent' }, cols: [{ key: 'name', label: 'Fund' }, { key: 'value', label: 'Percent %', type: 'number', width: '100px' }], defaults: () => ({ name: 'New fund', value: 0 }), addLabel: 'Add percentage' });
  const altRender = () => {
    const v = Number($('#bAlt').value); const b = S.budget;
    if (!v) { $('#bAltOut').textContent = ''; return; }
    const rem = v - b.fixed_total;
    $('#bAltOut').innerHTML = b.fixed.map(f => `${esc(f.name)} ${money(f.value)}`).concat(b.percent.map(p => `${esc(p.name)} ${money(rem * p.value / 100)}`)).join(' · ');
  };
  $('#bAlt').addEventListener('input', altRender);
  return () => {
    const b = S.budget, per = PERIOD_PER_MONTH[CFG.budget_period] || 1;
    const rows = [...b.fixed.map(f => ({ name: f.name, how: 'Fixed', amt: f.value })), ...b.percent.map(p => ({ name: p.name, how: `${p.value}% of ${money(b.remainder)}`, amt: p.amount }))];
    $('#bOut').innerHTML = `${Math.abs(b.percent_total - 100) > 0.001 && b.percent.length ? `<div class="warnbox">⚠ Percentages add up to ${b.percent_total}% — ${money(b.unallocated)} of each pay is not allocated.</div>` : ''}
      ${b.remainder < 0 ? `<div class="warnbox">⚠ Fixed amounts are more than your pay.</div>` : ''}
      <table><thead><tr><th>Fund</th><th>Rule</th><th class="num">Per pay</th><th class="num">Per month</th><th class="num">Per year</th></tr></thead><tbody>
      ${rows.map(r => `<tr><td>${esc(r.name)}</td><td class="muted">${r.how}</td><td class="num">${money(r.amt)}</td><td class="num">${money(r.amt * per)}</td><td class="num">${money(r.amt * per * 12)}</td></tr>`).join('')}
      <tr class="total"><td>Allocated</td><td>${b.balanced ? '✓ ' : ''}${money(b.allocated)} / ${money(b.income)}</td><td class="num">${money(b.allocated)}</td><td class="num">${money(b.allocated * per)}</td><td class="num">${money(b.allocated * per * 12)}</td></tr></tbody></table>`;
    altRender();
  };
} };

const TX_CATS = ['Salary', 'Other income', 'Interest', 'Rent', 'Groceries', 'Eating out', 'Fuel', 'Transport', 'Utilities', 'Phone & internet', 'Insurance', 'Health', 'University', 'Family support', 'Shopping', 'Entertainment', 'Subscriptions', 'Gifts', 'Travel', 'Fees', 'Transfer'];

PAGES.transactions = { title: 'Transactions', async render(el) {
  const accts = () => [...new Set([...(S.networth.lines.filter(l => l.kind === 'account').map(l => l.name))])];
  el.innerHTML = `<div class="grid">
    <div class="card wide"><h2>Add a transaction</h2>
      <form id="txForm" class="row">
        <input type="date" name="date" value="${todayISO()}" required aria-label="Date">
        <select name="sign" aria-label="Type"><option value="-1">Expense</option><option value="1">Income</option></select>
        <input type="number" name="amount" step="0.01" min="0" placeholder="Amount" required aria-label="Amount" style="width:120px">
        <input name="description" placeholder="Description / payee" aria-label="Description">
        <input name="category" placeholder="Category" list="txcats" aria-label="Category">
        <input name="account" placeholder="Account" list="txaccts" aria-label="Account">
        <button class="primary">Add</button>
      </form>
      <datalist id="txcats">${TX_CATS.map(c => `<option value="${c}">`).join('')}</datalist><datalist id="txaccts">${accts().map(c => `<option value="${esc(c)}">`).join('')}</datalist>
      <p class="hint">Use category “Transfer” for moving money between your own accounts — it is left out of income/expense totals.</p></div>
    <div class="card"><h2>Monthly cash flow</h2><div class="chart"><canvas id="cCf"></canvas></div><div id="cfStats" class="small" style="margin-top:8px"></div></div>
    <div class="card"><h2>Spending by category</h2><div class="row small" style="margin-bottom:8px"><label><input type="radio" name="catp" value="month" checked> This month</label><label><input type="radio" name="catp" value="year"> Last 12 months</label></div><div id="cats"></div></div>
    <div class="card wide"><h2>All transactions <span class="right row small"><input type="month" id="txMonth" aria-label="Filter month"><input id="txSearch" placeholder="Search" aria-label="Search"></span></h2><div id="txList"></div></div>
    <div class="card wide"><h2>Import from your bank or another app</h2>${importHTML()}</div>
  </div>`;
  let ed;
  const mount = async () => {
    const m = $('#txMonth').value, s = $('#txSearch').value.toLowerCase();
    ed = await editor($('#txList'), { table: 'transactions', where: r => (!m || (r.date || '').startsWith(m)) && (!s || JSON.stringify(r).toLowerCase().includes(s)), cols: [
      { key: 'date', label: 'Date', type: 'date', width: '150px' }, { key: 'description', label: 'Description' },
      { key: 'category', label: 'Category', list: TX_CATS, width: '160px' }, { key: 'account', label: 'Account', list: accts, width: '150px' },
      { key: 'amount', label: 'Amount (− spent)', type: 'number', width: '140px' }, { key: 'notes', label: 'Notes' }],
      totals: ['amount'], defaults: () => ({ date: todayISO(), amount: 0 }), sort: (a, b) => (b.date || '').localeCompare(a.date || '') || b.id - a.id, newFirst: true, empty: 'No transactions yet.' });
  };
  // Re-create the list container on filter change so old listeners are dropped.
  const remount = () => { const old = $('#txList'); const n = old.cloneNode(false); old.replaceWith(n); mount(); };
  $('#txMonth').addEventListener('change', remount);
  let st; $('#txSearch').addEventListener('input', () => { clearTimeout(st); st = setTimeout(remount, 300); });
  await mount();
  $('#txForm').addEventListener('submit', async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    await api('POST', '/api/t/transactions', { date: f.date, description: f.description, category: f.category, account: f.account, amount: Number(f.amount) * Number(f.sign) });
    e.target.amount.value = ''; e.target.description.value = '';
    toast('Transaction added'); remount(); changed();
  });
  bindImport(el, () => { remount(); changed(); });
  let period = 'month';
  $$('[name=catp]', el).forEach(r => r.addEventListener('change', () => { period = r.value; refresh(); }));
  const refresh = () => {
    destroyCharts();
    const cf = S.cashflow;
    makeChart($('#cCf'), { type: 'bar', data: { labels: cf.months.map(m => m.month.slice(5) + '/' + m.month.slice(2, 4)), datasets: [
      { label: 'Income', data: cf.months.map(m => m.income), backgroundColor: SERIES()[0], borderRadius: 4 },
      { label: 'Expenses', data: cf.months.map(m => m.expenses), backgroundColor: SERIES()[1], borderRadius: 4 }] } });
    const tm = cf.this_month;
    $('#cfStats').innerHTML = `This month: income ${money(tm.income)} · spent ${money(tm.expenses)} · net <span class="${cls(tm.net)}">${signed(tm.net)}</span>${tm.savings_rate != null ? ` · savings rate ${pct(tm.savings_rate)}` : ''}<br>12-month average: income ${money(cf.avg_income)} · spending ${money(cf.avg_expenses)}`;
    const cats = period === 'month' ? cf.categories_month : cf.categories_year;
    const tot = cats.reduce((s, c) => s + c[1], 0);
    $('#cats').innerHTML = cats.length ? `<table><tbody>${cats.map(([c, v]) => `<tr><td style="width:40%">${esc(c)}</td><td>${bar(tot ? v / tot : 0)}</td><td class="num" style="width:120px">${money(v)}</td></tr>`).join('')}<tr class="total"><td>Total</td><td></td><td class="num">${money(tot)}</td></tr></tbody></table>` : '<p class="muted small">No spending recorded for this period.</p>';
  };
  return refresh;
} };

function importHTML() {
  return `<p class="hint">Accepts CSV (most banks and apps — needs a Date column and an Amount, Inflow/Outflow or Debit/Credit column), OFX/QFX and QIF files. Duplicates are skipped.</p>
    <form id="impForm" class="row">
      <input type="file" name="file" accept=".csv,.ofx,.qfx,.qif,.txt" required aria-label="File">
      <input name="account" placeholder="Put into account (optional)" aria-label="Account">
      <select name="dayfirst" aria-label="Date format"><option value="1">Dates are DD/MM/YYYY</option><option value="0">Dates are MM/DD/YYYY</option></select>
      <button type="button" id="impPreview">Preview</button><button class="primary">Import</button>
    </form><div id="impOut" class="small" style="margin-top:8px"></div>`;
}
function bindImport(el, done) {
  const send = async dry => {
    const fd = new FormData($('#impForm')); fd.set('dry_run', dry ? '1' : '0');
    if (!fd.get('file')?.size) { toast('Choose a file first'); return; }
    const r = await api('POST', '/api/import/transactions', fd);
    if (dry) $('#impOut').innerHTML = `${r.count} transactions found.` + (r.preview.length ? `<table style="margin-top:6px"><tbody>${r.preview.map(p => `<tr><td>${p.date}</td><td>${esc(p.description)}</td><td>${esc(p.category)}</td><td class="num">${money(p.amount)}</td></tr>`).join('')}</tbody></table>` : '');
    else { $('#impOut').textContent = `Imported ${r.added} transactions (${r.skipped_duplicates} duplicates skipped).`; done?.(); }
  };
  $('#impPreview', el).addEventListener('click', () => send(true));
  $('#impForm', el).addEventListener('submit', e => { e.preventDefault(); send(false); });
}

/* Card with a header form bound to a parent row (loan / fund / holding). */
function parentFields(table, row, fields) {
  return `<div class="fields" data-parent="${table}" data-pid="${row.id}">${fields.map(f => {
    const v = row[f.key];
    if (f.type === 'select') return `<label class="field">${f.label}<select data-pk="${f.key}">${f.options.map(o => `<option ${o === v ? 'selected' : ''}>${o}</option>`).join('')}</select></label>`;
    return `<label class="field">${f.label}<input type="${f.type || 'text'}" ${f.type === 'number' ? 'step="any"' : ''} data-pk="${f.key}" value="${esc(v ?? '')}"></label>`;
  }).join('')}</div>`;
}
function bindParentFields(el) {
  el.addEventListener('change', async e => {
    const t = e.target; if (!t.dataset.pk) return;
    const box = t.closest('[data-parent]');
    let v = t.type === 'number' ? (t.value === '' ? null : Number(t.value)) : t.value;
    await api('PUT', `/api/t/${box.dataset.parent}/${box.dataset.pid}`, { [t.dataset.pk]: v });
    if (t.dataset.pk === 'name') { const h = box.closest('.card').querySelector('h2 .ttl'); if (h) h.textContent = v; }
    changed();
  });
}
async function deleteParent(table, id, what) {
  if (!confirm(`Delete this ${what} and all its entries?`)) return false;
  await api('DELETE', `/api/t/${table}/${id}`); return true;
}

const FREQ_LABEL = { weekly: 'weeks', fortnightly: 'fortnights', monthly: 'months' };

PAGES.loans = { title: 'Money lent', async render(el) {
  const loans = await api('GET', '/api/t/loans');
  el.innerHTML = `<p class="hint">Money you’ve lent out. The outstanding amount counts toward your net worth. Each repayment is split back to the funds the money came from.</p>
    <div class="row"><button class="primary" id="addLoan">+ New loan</button></div>
    <div class="grid">${loans.map(l => `
      <div class="card wide" data-loan="${l.id}"><h2><span class="ttl">${esc(l.name)}</span><button class="right danger" data-delloan="${l.id}">Delete</button></h2>
        ${parentFields('loans', l, [{ key: 'name', label: 'Name' }, { key: 'borrower', label: 'Borrower' }, { key: 'installment', label: 'Expected repayment', type: 'number' }, { key: 'frequency', label: 'Every', type: 'select', options: ['weekly', 'fortnightly', 'monthly'] }, { key: 'interest', label: 'Interest charged ($)', type: 'number' }])}
        <div class="tiles" data-lstats="${l.id}"></div>
        <div class="grid">
          <div><h2>Funded from</h2><div data-src="${l.id}"></div><div data-dist="${l.id}" style="margin-top:12px"></div></div>
          <div><h2>Repayments received <button class="right primary" data-pay="${l.id}">+ Record repayment today</button></h2><div data-pays="${l.id}"></div></div>
        </div></div>`).join('')}</div>`;
  bindParentFields(el);
  $('#addLoan').onclick = async () => { await api('POST', '/api/t/loans', { name: 'New loan', installment: 0, frequency: 'monthly', interest: 0 }); changed(); route(); };
  const pays = {};
  for (const l of loans) {
    await editor($(`[data-src="${l.id}"]`), { table: 'loan_sources', filter: { loan_id: l.id }, cols: [{ key: 'fund', label: 'Fund', list: ['Savings', 'Emergency Funds', 'Investment Funds', 'University Funds', 'Business Funds', 'Cash'] }, { key: 'amount', label: 'Amount', type: 'number', width: '130px' }], totals: ['amount'], defaults: () => ({ fund: 'Savings', amount: 0 }), addLabel: 'Add source' });
    pays[l.id] = await editor($(`[data-pays="${l.id}"]`), { table: 'loan_payments', filter: { loan_id: l.id }, cols: [{ key: 'date', label: 'Date', type: 'date', width: '160px' }, { key: 'amount', label: 'Amount', type: 'number', width: '130px' }, { key: 'notes', label: 'Notes' }], totals: ['amount'], sort: (a, b) => (b.date || '').localeCompare(a.date || ''), newFirst: true, defaults: () => ({ date: todayISO(), amount: 0 }), addLabel: 'Add repayment' });
  }
  el.addEventListener('click', async e => {
    const p = e.target.closest('[data-pay]');
    if (p) {
      const l = S.loans.find(x => x.id === Number(p.dataset.pay));
      const amt = prompt('Repayment amount', l.installment || '');
      if (amt === null || amt === '') return;
      await api('POST', '/api/t/loan_payments', { loan_id: l.id, date: todayISO(), amount: Number(amt) });
      await pays[l.id].reload(); changed(); toast('Repayment recorded');
    }
    const d = e.target.closest('[data-delloan]');
    if (d && await deleteParent('loans', d.dataset.delloan, 'loan')) { changed(); route(); }
  });
  return () => {
    for (const l of S.loans) {
      const box = $(`[data-lstats="${l.id}"]`); if (!box) continue;
      const unit = FREQ_LABEL[l.frequency] || 'periods';
      box.innerHTML = tile('Total lent', money(l.total), l.interest ? `incl. ${money(l.interest)} interest` : '') +
        tile('Repaid', money(l.paid), `${l.payment_count} payments · ${pct(l.progress, 0)}`) +
        tile('Still owed (payable)', money(l.payable), l.last_payment ? `Last payment ${fdate(l.last_payment)}` : 'No payments yet') +
        tile(`${unit[0].toUpperCase() + unit.slice(1)} left`, l.periods_left || (l.payable <= 0 ? '0' : '—'), l.payoff_date ? `Paid off ~${fdate(l.payoff_date)}` : (l.payable > 0 ? 'Set an expected repayment' : 'Fully repaid ✓')) +
        tile('Actual pace (6 mo)', `${money(l.monthly_pace)}/mo`, l.pace_payoff_date ? `At this pace: ~${fdate(l.pace_payoff_date)}` : '');
      const dist = $(`[data-dist="${l.id}"]`);
      dist.innerHTML = l.sources.length ? `<table><thead><tr><th>Fund</th><th class="num">Share</th><th class="num">Per repayment</th><th class="num">Returned</th><th class="num">Outstanding</th></tr></thead><tbody>${l.sources.map(s => `<tr><td>${esc(s.fund)}</td><td class="num">${pct(s.share, 2)}</td><td class="num">${money(s.per_installment)}</td><td class="num">${money(s.returned)}</td><td class="num">${money(s.outstanding)}</td></tr>`).join('')}</tbody></table>` : '';
    }
  };
} };

PAGES.parking = { title: 'Money parked', async render(el) {
  const funds = await api('GET', '/api/t/parked');
  el.innerHTML = `<p class="hint">Money you’ve parked with someone else (e.g. family) that still belongs to you. Log deposits, interest received and withdrawals (as negative amounts).</p>
    <div class="row"><button class="primary" id="addPark">+ New parked fund</button></div>
    <div class="grid">${funds.map(p => `<div class="card wide"><h2><span class="ttl">${esc(p.name)}</span><button class="right danger" data-delpark="${p.id}">Delete</button></h2>
      ${parentFields('parked', p, [{ key: 'name', label: 'Name' }, { key: 'holder', label: 'Held by' }, { key: 'notes', label: 'Notes' }])}
      <div class="tiles" data-pstats="${p.id}"></div><div style="margin-top:12px" data-pent="${p.id}"></div></div>`).join('')}</div>`;
  bindParentFields(el);
  $('#addPark').onclick = async () => { await api('POST', '/api/t/parked', { name: 'New fund' }); changed(); route(); };
  el.addEventListener('click', async e => { const d = e.target.closest('[data-delpark]'); if (d && await deleteParent('parked', d.dataset.delpark, 'fund')) { changed(); route(); } });
  for (const p of funds) {
    await editor($(`[data-pent="${p.id}"]`), { table: 'parked_entries', filter: { parked_id: p.id }, cols: [
      { key: 'date', label: 'Date', type: 'date', width: '160px' }, { key: 'amount', label: 'Amount', type: 'number', width: '130px' },
      { key: 'kind', label: 'Type', type: 'select', options: ['deposit', 'interest', 'withdrawal'], width: '130px' },
      { key: 'description', label: 'Description' }, { key: 'wallet', label: 'From wallet', list: ['Savings', 'Investment', 'UniFunds', 'Business', 'Emergency'], width: '140px' }],
      totals: ['amount'], sort: (a, b) => (b.date || '').localeCompare(a.date || ''), newFirst: true, defaults: () => ({ date: todayISO(), amount: 0, kind: 'interest' }), addLabel: 'Add entry' });
  }
  return () => {
    for (const p of S.parked) {
      const box = $(`[data-pstats="${p.id}"]`); if (!box) continue;
      box.innerHTML = tile('Balance', money(p.balance)) + tile('Deposited (net)', money(p.deposited)) + tile('Interest earned', money(p.interest), `${money(p.interest_12m)} in last 12 months`) + tile('Yield (12 mo)', pct(p.yield_estimate, 2), 'Interest ÷ money deposited');
    }
  };
} };

PAGES.investments = { title: 'Investments', async render(el) {
  const hs = await api('GET', '/api/t/holdings');
  el.innerHTML = `<p class="hint">Track each holding’s purchases and update its current value whenever you check the exchange. Your other investment accounts (lump-sum balances) live on the <a href="#/networth">Net worth</a> page.</p>
    <div class="tiles" id="invTiles"></div>
    <div class="row" style="margin-top:12px"><button class="primary" id="addHold">+ New holding</button></div>
    <div class="grid">${hs.map(h => `<div class="card wide"><h2><span class="ttl">${esc(h.name)}</span><button class="right danger" data-delhold="${h.id}">Delete</button></h2>
      ${parentFields('holdings', h, [{ key: 'name', label: 'Name' }, { key: 'symbol', label: 'Symbol' }, { key: 'kind', label: 'Type', type: 'select', options: ['crypto', 'shares', 'etf', 'managed fund', 'other'] }, { key: 'current_value', label: 'Current value (total)', type: 'number' }, { key: 'units', label: 'Units held (optional)', type: 'number' }])}
      <div class="tiles" data-hstats="${h.id}"></div><div class="grid"><div><h2>Purchases</h2><div data-buys="${h.id}"></div></div><div><h2>By platform</h2><div data-plat="${h.id}"></div></div></div></div>`).join('')}</div>`;
  bindParentFields(el);
  $('#addHold').onclick = async () => { await api('POST', '/api/t/holdings', { name: 'New holding', kind: 'crypto', current_value: 0 }); changed(); route(); };
  el.addEventListener('click', async e => { const d = e.target.closest('[data-delhold]'); if (d && await deleteParent('holdings', d.dataset.delhold, 'holding')) { changed(); route(); } });
  for (const h of hs) {
    await editor($(`[data-buys="${h.id}"]`), { table: 'holding_buys', filter: { holding_id: h.id }, cols: [
      { key: 'date', label: 'Date', type: 'date', width: '160px' }, { key: 'amount', label: 'Spent', type: 'number', width: '120px' },
      { key: 'units', label: 'Units', type: 'number', step: 'any', width: '110px' }, { key: 'platform', label: 'Platform', list: ['binance', 'coinspot'] }],
      totals: ['amount'], sort: (a, b) => (b.date || '').localeCompare(a.date || ''), newFirst: true, defaults: () => ({ date: todayISO(), amount: 0 }), addLabel: 'Add purchase' });
  }
  return () => {
    const t = S.holdings_total;
    $('#invTiles').innerHTML = tile('Total invested', money(t.invested)) + tile('Current value', money(t.value)) + tile('Gain / loss', `<span class="${cls(t.profit)}">${signed(t.profit)}</span>`, t.invested ? pct(t.profit / t.invested) : '');
    for (const h of S.holdings) {
      const box = $(`[data-hstats="${h.id}"]`); if (!box) continue;
      box.innerHTML = tile('Invested', money(h.invested)) + tile('Value', money(h.value)) + tile('Profit', `<span class="${cls(h.profit)}">${signed(h.profit)}</span>`, pct(h.profit_pct)) + (h.avg_cost ? tile('Average cost / unit', money(h.avg_cost)) : '');
      $(`[data-plat="${h.id}"]`).innerHTML = `<table><tbody>${Object.entries(h.by_platform).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${bar(h.invested ? v / h.invested : 0)}</td><td class="num">${money(v)}</td></tr>`).join('')}</tbody></table>`;
    }
  };
} };

PAGES.goals = { title: 'Goals', async render(el) {
  const linkOpts = () => [['networth', 'Net worth (auto)'], ['spendable', 'Spendable (auto)'], ['', 'Manual — enter current amount'],
    ...S.networth.lines.filter(l => l.kind !== 'loan').map(l => [`${l.kind === 'account' ? 'account' : l.kind}:${l.id}`, `${l.name} (auto)`])];
  el.innerHTML = `<div class="grid">
    <div class="card wide"><h2>Your goals</h2><p class="hint">Link a goal to net worth, spendable money or a specific account to track it automatically — or choose “Manual” and type the current amount. Add a target date to see how much you need to save each month.</p><div id="gEd"></div></div>
    <div class="card"><h2>Progress</h2><div id="gProg"></div></div>
    <div class="card"><h2>Assumptions</h2><div class="fields">${cfgInput('monthly_savings', 'Expected savings per month')}</div><p class="hint">Your forecast says you can save about <b id="gSurplus"></b> a month (income minus fixed expenses).</p></div>
  </div>`;
  bindCfg(el);
  await editor($('#gEd'), { table: 'goals', cols: [
    { key: 'name', label: 'Goal' }, { key: 'target', label: 'Target', type: 'number', width: '130px' },
    { key: 'link', label: 'Track', type: 'select', options: linkOpts(), width: '220px' }, { key: 'current', label: 'Current (manual)', type: 'number', width: '130px' },
    { key: 'target_date', label: 'Target date', type: 'date', width: '160px' }], defaults: () => ({ name: 'New goal', target: 10000, link: '' }), addLabel: 'Add goal' });
  return () => { $('#gProg').innerHTML = goalsHTML(S.goals) || '<p class="muted">No goals yet.</p>'; $('#gSurplus').textContent = money(S.forecast.surplus); };
} };

PAGES.forecast = { title: 'Forecast', async render(el) {
  el.innerHTML = `<div class="grid">
    <div class="card"><h2>Average monthly income</h2><div id="fInc"></div></div>
    <div class="card"><h2>Fixed monthly expenses</h2><div id="fExp"></div></div>
    <div class="card"><h2>Assumptions</h2><div class="fields">${cfgInput('forecast_years', 'Years to forecast')}${cfgInput('super_rate', 'Super rate (year 1 add-on)')}${cfgInput('growth_rate', 'Investment growth % / year')}</div>
      <p class="hint">Same formula as your spreadsheet: each year adds (income − expenses) × 12. Year 1 also adds monthly income × super rate. Growth is 0% by default (matches the sheet) — set e.g. 5 to include compounding.</p><div id="fSum"></div></div>
    <div class="card wide"><h2>Projection</h2><div class="chart tall"><canvas id="cFc"></canvas></div><div id="fTable" style="margin-top:12px"></div></div>
  </div>`;
  bindCfg(el);
  const cols = [{ key: 'name', label: 'Item' }, { key: 'amount', label: 'Per month', type: 'number', width: '140px' }];
  await editor($('#fInc'), { table: 'recurring', filter: { kind: 'income' }, cols, totals: ['amount'], defaults: () => ({ name: 'Income', amount: 0 }), addLabel: 'Add income' });
  await editor($('#fExp'), { table: 'recurring', filter: { kind: 'expense' }, cols, totals: ['amount'], defaults: () => ({ name: 'Expense', amount: 0 }), addLabel: 'Add expense' });
  return () => {
    destroyCharts();
    const f = S.forecast;
    $('#fSum').innerHTML = `<table><tbody><tr><td>Income</td><td class="num">${money(f.income)}</td></tr><tr><td>Fixed expenses</td><td class="num">${money(f.expenses)}</td></tr><tr class="total"><td>Surplus / month</td><td class="num">${money(f.surplus)}</td></tr><tr><td>Savings rate</td><td class="num">${pct(f.savings_rate)}</td></tr></tbody></table>`;
    makeChart($('#cFc'), { type: 'line', data: { labels: ['Now', ...f.rows.map(r => 'Year ' + r.year)], datasets: [
      lineDs('Net worth', [S.networth.total, ...f.rows.map(r => r.networth)], SERIES()[0]), lineDs('Cash (spendable)', [S.networth.spendable, ...f.rows.map(r => r.cash)], SERIES()[1])] } });
    $('#fTable').innerHTML = `<table><thead><tr><th>Year</th><th>Date expected</th><th class="num">Net worth</th><th class="num">Cash (spendable)</th></tr></thead><tbody>
      <tr><td>Now</td><td>${fdate(S.today)}</td><td class="num">${money(S.networth.total)}</td><td class="num">${money(S.networth.spendable)}</td></tr>
      ${f.rows.map(r => `<tr><td>Year ${r.year}</td><td>${fdate(r.date)}</td><td class="num">${money(r.networth)}</td><td class="num">${money(r.cash)}</td></tr>`).join('')}</tbody></table>`;
  };
} };

PAGES.tax = { title: 'Tax return', async render(el) {
  el.innerHTML = `<div class="grid">
    <div class="card"><h2>Annual income</h2><div id="tInc"></div></div>
    <div class="card"><h2>Work-related expenses</h2><div id="tWork"></div></div>
    <div class="card"><h2>Other deductions</h2><div id="tDed"></div></div>
    <div class="card"><h2>Tax paid & levy</h2><div class="fields">${cfgInput('tax.year', 'Financial year', 'text')}${cfgInput('tax.withheld', 'Salary tax withheld')}${cfgInput('tax.medicare', 'Medicare levy ($)')}${cfgInput('tax.medicare_auto', 'Calculate levy automatically', 'check')}${cfgInput('tax.medicare_rate', 'Levy rate %')}</div></div>
    <div class="card"><h2>Tax brackets</h2><p class="hint">Keep these up to date each financial year.</p><div id="tBr"></div></div>
    <div class="card"><h2>Estimate</h2><div id="tOut"></div><p class="hint" style="margin-top:12px">Estimates only — actual results vary with tax offsets, HELP/HECS repayments and other adjustments.</p></div>
  </div>`;
  bindCfg(el);
  const cols = [{ key: 'name', label: 'Item' }, { key: 'amount', label: 'Amount', type: 'number', width: '130px' }];
  await editor($('#tInc'), { table: 'tax_items', filter: { section: 'income' }, cols, totals: ['amount'], defaults: () => ({ name: 'Income', amount: 0 }), addLabel: 'Add income' });
  await editor($('#tWork'), { table: 'tax_items', filter: { section: 'work_expense' }, cols, totals: ['amount'], defaults: () => ({ name: 'Expense', amount: 0 }), addLabel: 'Add expense' });
  await editor($('#tDed'), { table: 'tax_items', filter: { section: 'deduction' }, cols, totals: ['amount'], defaults: () => ({ name: 'Deduction', amount: 0 }), addLabel: 'Add deduction' });
  const drawBrackets = () => {
    const bs = CFG.tax.brackets;
    $('#tBr').innerHTML = `<table><thead><tr><th>Income over</th><th>Up to</th><th class="num">Rate %</th><th></th></tr></thead><tbody>${bs.map((b, i) => `<tr><td style="width:30%"><input type="number" data-br="${i}" data-f="from" value="${b.from}"></td><td class="muted">${bs[i + 1] ? bs[i + 1].from.toLocaleString() : 'and over'}</td><td><input type="number" step="any" data-br="${i}" data-f="rate" value="${b.rate}"></td><td><button class="icon" data-brdel="${i}" aria-label="Delete bracket">✕</button></td></tr>`).join('')}</tbody></table><div class="row" style="margin-top:8px"><button id="brAdd">+ Add bracket</button></div>`;
  };
  drawBrackets();
  const saveBr = async bs => { bs.sort((a, b) => a.from - b.from); CFG = await api('PUT', '/api/config', { tax: { ...CFG.tax, brackets: bs } }); drawBrackets(); changed(); };
  $('#tBr').addEventListener('change', e => { const t = e.target; if (t.dataset.br === undefined) return; const bs = structuredClone(CFG.tax.brackets); bs[t.dataset.br][t.dataset.f] = Number(t.value); saveBr(bs); });
  $('#tBr').addEventListener('click', e => {
    if (e.target.id === 'brAdd') { const bs = structuredClone(CFG.tax.brackets); bs.push({ from: (bs.at(-1)?.from || 0) + 10000, rate: 0 }); saveBr(bs); }
    const d = e.target.closest('[data-brdel]'); if (d) { const bs = structuredClone(CFG.tax.brackets); bs.splice(Number(d.dataset.brdel), 1); saveBr(bs); }
  });
  return () => {
    const t = S.tax;
    $('#tOut').innerHTML = `<table><tbody>
      <tr><td>Total income</td><td class="num">${money(t.total_income)}</td></tr>
      <tr><td>− Deductions (incl. ${money(t.work_expenses)} work expenses)</td><td class="num">${money(t.deductions)}</td></tr>
      <tr class="total"><td>Taxable income</td><td class="num">${money(t.taxable)}</td></tr>
      <tr><td>Income tax</td><td class="num">${money(t.income_tax)}</td></tr>
      <tr><td>Medicare levy</td><td class="num">${money(t.medicare)}</td></tr>
      <tr class="total"><td>Total tax</td><td class="num">${money(t.total_tax)}</td></tr>
      <tr><td>Tax withheld</td><td class="num">${money(t.withheld)}</td></tr>
      <tr class="total"><td>${t.refund >= 0 ? 'Estimated refund' : 'Estimated amount owing'}</td><td class="num ${cls(t.refund)}">${money(Math.abs(t.refund))}</td></tr>
      <tr class="sub"><td>Effective tax rate · marginal rate</td><td class="num">${pct(t.effective_rate)} · ${t.marginal_rate}%</td></tr></tbody></table>`;
  };
} };

PAGES.history = { title: 'History', async render(el) {
  el.innerHTML = `<div class="tiles" id="hTiles"></div><div class="grid">
    <div class="card wide"><h2>Net worth history</h2><div class="chart tall"><canvas id="cH"></canvas></div></div>
    <div class="card wide"><h2>Snapshots</h2><p class="hint">A snapshot of your net worth is saved automatically once a day whenever you open the app (today’s keeps updating as you edit). You can also add past figures by hand.</p><div id="hEd"></div></div>
  </div>`;
  await editor($('#hEd'), { table: 'snapshots', cols: [{ key: 'date', label: 'Date', type: 'date', width: '170px' }, { key: 'networth', label: 'Net worth', type: 'number', width: '160px' }, { key: 'spendable', label: 'Spendable', type: 'number', width: '160px' }, { key: 'notes', label: 'Notes' }],
    sort: (a, b) => (b.date || '').localeCompare(a.date || ''), newFirst: true, defaults: () => ({ date: todayISO(), networth: S.networth.total, spendable: S.networth.spendable, notes: 'Manual' }), addLabel: 'Add snapshot' });
  return () => {
    destroyCharts();
    const g = S.growth || {}, sn = S.snapshots;
    const t = (label, d) => tile(label, d ? `<span class="${cls(d.change)}">${signed(d.change)}</span>` : '—', d ? `${d.pct != null ? pct(d.pct) + ' · ' : ''}since ${fdate(d.from)}` : 'Not enough history yet');
    $('#hTiles').innerHTML = t('Since last snapshot', g.previous) + t('Last 30 days', g.month) + t('Year to date', g.ytd) + t('Last 12 months', g.year) + tile('Average growth', g.avg_monthly !== undefined ? `${signed(g.avg_monthly)}/mo` : '—');
    makeChart($('#cH'), { type: 'line', data: { labels: sn.map(s => fdate(s.date)), datasets: [lineDs('Net worth', sn.map(s => s.networth), SERIES()[0]), lineDs('Spendable', sn.map(s => s.spendable), SERIES()[1], { spanGaps: true })] } });
  };
} };

PAGES.data = { title: 'Import / export', async render(el) {
  const accts = await api('GET', '/api/ledger/accounts');
  el.innerHTML = `<div class="grid">
    <div class="card wide"><h2>Export</h2>
      <p class="hint">Transactions exports include every money movement — your transactions, money lent and repaid, money parked, investment purchases — plus an opening balance for each account so the other app shows the same balances.</p>
      <div class="row"><label class="field">Account (QIF/OFX/CSV)<select id="exAcct"><option value="">All accounts</option>${accts.map(a => `<option>${esc(a)}</option>`).join('')}</select></label>
      <label class="small row"><input type="checkbox" id="exBal" checked> Include opening balances</label></div>
      <table style="margin-top:12px"><tbody>
        <tr><td><b>CSV</b> — universal; works with YNAB, Actual Budget, Firefly III, Monarch, Google Sheets, Excel…</td><td><button data-ex="csv">Download .csv</button></td></tr>
        <tr><td><b>OFX 2.2</b> — Open Financial Exchange, the bank-statement standard (GnuCash, Moneydance, Quicken, Banktivity, MoneyMoney…)</td><td><button data-ex="ofx">Download .ofx</button></td></tr>
        <tr><td><b>QIF</b> — Quicken Interchange Format (GnuCash, KMyMoney, HomeBank, Quicken, MS Money…). Dates are MM/DD/YYYY.</td><td><button data-ex="qif">Download .qif</button></td></tr>
        <tr><td><b>Excel workbook</b> — summary sheet plus every table and the full ledger</td><td><button data-ex="xlsx">Download .xlsx</button></td></tr>
        <tr><td><b>Full backup (JSON)</b> — everything, including settings; restore it below or on another server</td><td><button data-ex="json">Download .json</button></td></tr>
      </tbody></table></div>
    <div class="card"><h2>Import transactions</h2>${importHTML()}</div>
    <div class="card"><h2>Restore a backup</h2><p class="hint">Replaces <b>all</b> current data with the backup file.</p>
      <form id="restForm" class="row"><input type="file" name="file" accept=".json" required aria-label="Backup file"><button class="danger">Restore</button></form>
      <h2 style="margin-top:20px">Start over</h2><div class="row"><button id="resetSeed" class="danger">Reset to spreadsheet data</button><button id="resetEmpty" class="danger">Erase everything</button></div></div>
    <div class="card"><h2>Display settings</h2><div class="fields">${cfgInput('currency', 'Currency code', 'text')}${cfgInput('locale', 'Number/date format (locale)', 'text')}</div><p class="hint">e.g. AUD + en-AU, USD + en-US, PHP + en-PH.</p></div>
  </div>`;
  bindCfg(el);
  bindImport(el, changed);
  el.addEventListener('click', e => {
    const b = e.target.closest('[data-ex]'); if (!b) return;
    const p = new URLSearchParams({ balances: $('#exBal').checked ? 1 : 0 });
    if ($('#exAcct').value && !['json', 'xlsx'].includes(b.dataset.ex)) p.set('account', $('#exAcct').value);
    location.href = `/api/export/${b.dataset.ex}?${p}`;
  });
  $('#restForm').addEventListener('submit', async e => {
    e.preventDefault(); if (!confirm('Replace ALL data with this backup?')) return;
    await api('POST', '/api/import/backup', new FormData(e.target)); toast('Backup restored'); await loadSummary();
  });
  $('#resetSeed').onclick = async () => { if (confirm('Replace ALL data with the original spreadsheet data?')) { await api('POST', '/api/reset', { which: 'seed' }); toast('Reset done'); changed(); } };
  $('#resetEmpty').onclick = async () => { if (confirm('Erase ALL data? Download a backup first!') && confirm('Really erase everything?')) { await api('POST', '/api/reset', { which: 'empty' }); toast('All data erased'); changed(); } };
  return null;
} };

/* ------------------------------------------------------------------ router */

async function route() {
  const name = (location.hash.replace('#/', '') || 'dashboard').split('?')[0];
  const page = PAGES[name] || PAGES.dashboard;
  destroyCharts();
  $$('#nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#/' + name));
  $('#nav').classList.remove('open');
  $('#pageTitle').textContent = page.title;
  document.title = page.title + ' · Finance Tracker';
  const holder = $('#page');
  const el = document.createElement('div');
  holder.replaceChildren(el);
  await loadSummary();
  currentRefresh = await page.render(el);
  if (currentRefresh) currentRefresh();
}

window.addEventListener('hashchange', route);
$('#menuBtn').addEventListener('click', () => $('#nav').classList.toggle('open'));
route();
