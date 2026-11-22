/* The Markets Desk card (read-only): pulse and session on the left, the chart in the middle, the seat roster on the right, a footer with the source and the not-advice line.
   Every number comes from /api/markets/* (a data layer that stamps each value with its source and time); a feed that is down shows "—". Charts are TradingView Lightweight Charts
   (Apache-2.0, vendored in /static/vendor/lwc, no CDN). Nothing redraws unless data or the user changed it; polling is once a minute and only while the desk is visible. There is no
   order, buy or sell control anywhere in this file. */
const SEAT_ANGLES = (n, i) => (i / n) * Math.PI * 2 - Math.PI / 2;
const COL = { up: '#4be39a', down: '#ff4b5c', line: '#9cc2ff' };

export function mount(host, card, api) {
  const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  host.classList.add('mkt'); host.textContent = '';
  const left = el('div', 'mkt-l'), mid = el('div', 'mkt-m'), right = el('div', 'mkt-r'), foot = el('div', 'mkt-f');
  const main = el('div', 'mkt-main'); main.append(left, mid, right); host.append(main, foot);
  const st = { symbol: null, range: '3mo', ind: [], levels: false, band: false, cmp: null, type: 'candle', alive: true, last: '', pulseT: 0 };
  let chart = null, cs = null, ls = null, vs = null, extra = [], lines = [], info = el('div', 'mkt-info'), data = null;

  // ---- left: tiles, session, signal log
  const tiles = el('div', 'mkt-tiles'), sess = el('div', 'mkt-sess'), logEl = el('div', 'mkt-log'), chip = el('div', 'mkt-health');
  left.append(el('div', 'mkt-h', 'MARKET PULSE'), tiles, sess, el('div', 'mkt-h', 'SIGNAL LOG'), logEl, chip);
  const log = t => { const d = el('div', null, new Date().toTimeString().slice(0, 5) + '  ' + t); logEl.prepend(d); while (logEl.children.length > 6) logEl.lastChild.remove(); };
  const fmt = (x, nd = 2) => (x == null || !isFinite(x)) ? '—' : x.toLocaleString(undefined, { minimumFractionDigits: nd, maximumFractionDigits: nd });
  function paintPulse(p) {
    tiles.textContent = '';
    (p.tiles || []).forEach(t => { const d = el('button', 'mkt-tile' + (t.pct == null ? '' : t.pct < 0 ? ' dn' : ' up') + (t.stale ? ' stale' : ''));
      d.append(el('b', null, t.name), el('span', null, fmt(t.price)), el('small', null, t.pct == null ? '—' : (t.pct >= 0 ? '+' : '') + t.pct.toFixed(2) + '%')); d.title = (t.source || 'no data') + (t.stale ? ' (stale)' : ''); d.onclick = () => load({ symbol: t.symbol }); tiles.append(d); });
    const s = p.session || {}; sess.textContent = ''; sess.append(el('b', null, s.label || '—'), el('span', null, s.countdown || ''), el('small', null, s.note ? 'NY ' + s.ny + ' · ' + s.note : ''));
    chip.textContent = ''; Object.entries(p.health || {}).forEach(([k, v]) => { const c = el('span', 'hc ' + v, k.toUpperCase()); c.title = k + ': ' + v; chip.append(c); });
    foot.textContent = `Data: ${[...new Set((p.tiles || []).map(t => t.source).filter(Boolean))].join(', ') || 'none right now'} · updated ${p.updated || '—'} · ${card.notadvice || 'Educational, not financial advice.'}`;
    (p.econ || []).slice(0, 1).forEach(e => { const d = el('div', 'mkt-econ', e.date + '  ' + e.what.slice(0, 60)); sess.append(d); });
  }
  async function pulse() {
    if (!st.alive || document.hidden || !host.isConnected) return;
    try { const p = await fetch('/api/markets/pulse').then(r => r.json()); if (!p.error) paintPulse(p); else foot.textContent = 'The desk is switched off in settings.'; } catch (_) { foot.textContent = 'Price feed is down. · ' + (card.notadvice || ''); }
  }

  // ---- right: the seat roster around a central orb (the speaking seat lights up)
  const roster = el('div', 'mkt-roster'), orb = el('div', 'mkt-orb', 'MASTER'); roster.append(orb);
  const seats = (card.seats || []).filter(s => s.name !== 'MASTER'), nodes = {};
  seats.forEach((s, i) => { const a = SEAT_ANGLES(seats.length, i), n = el('div', 'mkt-seat', s.name); n.style.left = (50 + Math.cos(a) * 40) + '%'; n.style.top = (50 + Math.sin(a) * 40) + '%'; n.style.setProperty('--sc', s.color); n.title = s.name + ': ' + s.role; roster.append(n); nodes[s.name] = n; });
  const act = el('div', 'mkt-act'); right.append(el('div', 'mkt-h', 'SEATS'), roster, el('div', 'mkt-h', 'ACTIVITY'), act);
  function lightSeat(seat, what) { Object.values(nodes).forEach(n => n.classList.remove('on')); if (nodes[seat]) nodes[seat].classList.add('on'); orb.classList.toggle('talk', !!nodes[seat]); if (what) { const d = el('div', null, seat + '  ' + what); act.prepend(d); while (act.children.length > 5) act.lastChild.remove(); } clearTimeout(lightSeat.t); lightSeat.t = setTimeout(() => { Object.values(nodes).forEach(n => n.classList.remove('on')); orb.classList.remove('talk'); }, 6000); }

  // ---- middle: toolbar + chart
  const bar = el('div', 'mkt-bar'), box = el('div', 'mkt-chart'), ov = el('div', 'mkt-ov'), cap = el('div', 'mkt-cap');
  const sym = el('input', 'mkt-sym'); sym.placeholder = 'symbol, e.g. NVDA'; sym.maxLength = 12; sym.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter' && sym.value.trim()) load({ symbol: sym.value.trim().toUpperCase() }); };
  const mk = (t, fn, cls) => { const b = el('button', 'tiny box mb' + (cls ? ' ' + cls : ''), t); b.onclick = fn; bar.append(b); return b; };
  bar.append(sym);
  const rb = {}; [['1D', '1d'], ['5D', '5d'], ['1M', '1mo'], ['3M', '3mo'], ['6M', '6mo'], ['1Y', '1y'], ['5Y', '5y']].forEach(([t, r]) => { rb[r] = mk(t, () => load({ range: r }), 'rg'); });
  const tgl = (t, key, val) => { const b = mk(t, () => { if (key === 'ind') { const i = st.ind.indexOf(val); i < 0 ? st.ind.push(val) : st.ind.splice(i, 1); } else st[key] = !st[key]; load({}); }); return b; };
  const tType = mk('LINE', () => { st.type = st.type === 'candle' ? 'line' : 'candle'; paintType(); }); tgl('MA50', 'ind', 'ma50'); tgl('LEVELS', 'levels'); tgl('BAND', 'band'); const tVol = mk('VOL', () => { vs && vs.applyOptions({ visible: !vs.options().visible }); });
  box.append(ov); mid.append(bar, box, info, cap); info.textContent = 'Tap a candle for its numbers.';

  function ensureChart() {
    if (chart) return true;
    if (!window.LightweightCharts) return false;
    const css = getComputedStyle(document.documentElement), fg = css.getPropertyValue('--fg').trim() || '#e8f0ff', dim = css.getPropertyValue('--dim').trim() || '#6f86b8', acc = css.getPropertyValue('--acc').trim() || '#3d8bff';
    chart = window.LightweightCharts.createChart(box, { autoSize: true, layout: { background: { color: 'transparent' }, textColor: fg, fontFamily: 'Noto Sans Mono, monospace', fontSize: 11 }, grid: { vertLines: { color: 'rgba(130,170,255,.08)' }, horzLines: { color: 'rgba(130,170,255,.08)' } },
      rightPriceScale: { borderColor: dim }, timeScale: { borderColor: dim, timeVisible: true }, crosshair: { mode: 1 }, handleScroll: true, handleScale: true });
    cs = chart.addCandlestickSeries({ upColor: COL.up, downColor: COL.down, wickUpColor: COL.up, wickDownColor: COL.down, borderVisible: false });
    ls = chart.addLineSeries({ color: acc, lineWidth: 2, visible: false });
    vs = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: 'rgba(130,170,255,.35)', visible: false }); chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chart.subscribeClick(p => { if (!p.time || !data) return; const c = p.seriesData.get(cs) || p.seriesData.get(ls); if (!c) return; const row = data.candles.find(x => x.time === p.time) || {};
      info.textContent = `${new Date(p.time * 1000).toLocaleDateString()}  O ${fmt(row.open)}  H ${fmt(row.high)}  L ${fmt(row.low)}  C ${fmt(row.close)}  V ${row.volume != null ? row.volume.toLocaleString() : '—'}`; });
    return true;
  }
  function paintType() { if (!cs) return; cs.applyOptions({ visible: st.type === 'candle' }); ls.applyOptions({ visible: st.type === 'line' }); tType.textContent = st.type === 'candle' ? 'LINE' : 'CANDLE'; }
  function clearExtras() { extra.forEach(s => { try { chart.removeSeries(s); } catch (_) {} }); extra = []; lines.forEach(l => { try { cs.removePriceLine(l); } catch (_) {} }); lines = []; }

  function draw(p) {
    if (p.error) { ov.textContent = (p.error === 'price feed is down' ? 'Price feed is down. —' : p.error); ov.style.display = ''; cap.textContent = '—'; return; }
    ov.style.display = 'none'; if (!ensureChart()) { ov.textContent = 'Loading the chart…'; ov.style.display = ''; return; }
    const key = JSON.stringify([p.symbol, p.range, p.candles.length, p.candles.length && p.candles[p.candles.length - 1], Object.keys(p.indicators), p.levels.length, p.band.length, p.compare && p.compare.symbol, st.type]);
    if (key === st.last) return; st.last = key;                                  // identical data: no redraw (idle costs nothing)
    data = p; clearExtras(); api.draws = (api.draws || 0) + 1; window.__mktDraws = api.draws;
    cs.setData(p.candles); ls.setData(p.candles.map(c => ({ time: c.time, value: c.close }))); vs.setData(p.candles.map(c => ({ time: c.time, value: c.volume, color: c.close >= c.open ? 'rgba(75,227,154,.35)' : 'rgba(255,75,92,.35)' })));
    Object.entries(p.indicators || {}).forEach(([k, arr]) => { const s = chart.addLineSeries({ color: '#ffd84a', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: k.toUpperCase() }); s.setData(arr); extra.push(s); });
    (p.levels || []).slice(0, 12).forEach(l => lines.push(cs.createPriceLine({ price: l.price, color: 'rgba(156,194,255,.75)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: l.label })));
    if (p.compare) { const s = chart.addLineSeries({ color: '#ff9a3c', lineWidth: 2, title: p.compare.symbol }); s.setData(p.compare.series); extra.push(s); }
    if ((p.band || []).length) { const last = p.candles[p.candles.length - 1].time, step = p.range === '1d' ? 300 : p.range === '5d' ? 1800 : 86400, mkS = (k, dash) => { const s = chart.addLineSeries({ color: '#ff7bc8', lineWidth: 1, lineStyle: dash ? 2 : 0, priceLineVisible: false, lastValueVisible: false }); s.setData([{ time: last, value: p.candles[p.candles.length - 1].close }, ...p.band.map(b => ({ time: last + b.i * step, value: b[k] }))]); extra.push(s); };
      mkS('hi', true); mkS('mid', false); mkS('lo', true); }
    paintType(); chart.timeScale().fitContent();
    Object.entries(rb).forEach(([r, b]) => b.classList.toggle('on', r === p.range));
    cap.textContent = `${p.name || p.symbol} (${p.symbol}) · ${p.source}${p.stale ? ' · STALE since ' + new Date(p.t * 1000).toTimeString().slice(0, 5) : ' · ' + new Date(p.t * 1000).toTimeString().slice(0, 5)}` + (p.band && p.band.length ? ' · ' + p.band_label : '') + (p.levels && p.levels.length ? ' · levels describe the past, they are not signals' : '');
    if (p.levels && p.levels.length) info.textContent = p.levels.map(l => `${l.label} ${fmt(l.price)} (${l.how})`).join('  ·  ');
  }
  async function load(ch) {
    Object.assign(st, ch || {}); if (!st.symbol) return; sym.value = st.symbol;
    const q = new URLSearchParams({ sym: st.symbol, range: st.range, ind: st.ind.join(','), levels: st.levels ? '1' : '0', band: st.band ? '1' : '0', cmp: st.cmp || '' });
    try { draw(await fetch('/api/markets/chart?' + q).then(r => r.json())); } catch (_) { draw({ error: 'price feed is down' }); }
  }

  // ---- commands from the master (voice): {"op": "chart"|"type"|"watchlist"|"close", ...}
  const ctl = { apply(ops) { (ops || []).forEach(o => { if (o.op === 'chart') { const p = o.payload || {}; st.symbol = p.symbol || st.symbol; st.range = p.range || st.range; st.type = o.type || st.type; st.band = !!(p.band && p.band.length); st.levels = !!(p.levels && p.levels.length); st.ind = Object.keys(p.indicators || {}); st.cmp = p.compare ? p.compare.symbol : null; sym.value = st.symbol || ''; draw(p); log('chart ' + (st.symbol || '')); lightSeat('CHARTIST', st.symbol); }
      else if (o.op === 'seat') lightSeat(o.seat, o.what); else if (o.op === 'close') { api.send({ t: 'mkt_closed' }); } else if (o.op === 'type') { st.type = o.type; paintType(); } else if (o.op === 'watchlist') pulse(); }); }, seat: lightSeat, log, dispose() { st.alive = false; clearInterval(iv); try { chart && chart.remove(); } catch (_) {} } };
  const iv = setInterval(() => { if (!host.isConnected) { ctl.dispose(); return; } pulse(); }, 60000);
  const lib = document.createElement('script'); lib.src = '/static/vendor/lwc/lightweight-charts.standalone.production.js';
  const start = () => { pulse(); if (card.symbol) load({ symbol: card.symbol }); }; window.LightweightCharts ? start() : (lib.onload = start, lib.onerror = () => { ov.textContent = 'The chart library did not load.'; ov.style.display = ''; }, document.head.append(lib));
  return ctl;
}
