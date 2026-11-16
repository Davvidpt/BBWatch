'use strict';
const $ = (s) => document.querySelector(s);
const app = { instruments: [], lastScan: null, busy: false, toastTimer: null };

function toast(message, error = false) {
  const box = $('#toast');
  box.textContent = message;
  box.classList.toggle('error', error);
  box.classList.remove('hidden');
  clearTimeout(app.toastTimer);
  app.toastTimer = setTimeout(() => box.classList.add('hidden'), 4500);
}
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
async function api(path, options = {}) {
  const res = await fetch(path, {credentials:'same-origin', cache:'no-store', ...options,
    headers: {'Content-Type':'application/json', ...(options.headers || {})}});
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) {
    if (res.status === 401 && !path.includes('/auth/login')) showLogin();
    throw new Error(typeof data.detail === 'string' ? data.detail : `Permintaan gagal (HTTP ${res.status})`);
  }
  return data;
}
function showLogin() {
  $('#loading').classList.add('hidden');
  $('#dashboard').classList.add('hidden');
  $('#auth').classList.remove('hidden');
}
function showDashboard() {
  $('#loading').classList.add('hidden');
  $('#auth').classList.add('hidden');
  $('#dashboard').classList.remove('hidden');
}
function formatNumber(value, digits = 2) {
  return Number(value).toLocaleString('en-US', {maximumFractionDigits: digits});
}
function dateText(value) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.valueOf()) ? '—' : d.toLocaleString('id-ID', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Jakarta'}) + ' WIB';
}
function priceText(value) {
  if (value == null) return '—';
  const n = Number(value);
  return Number.isFinite(n) ? formatNumber(n, n < 1 ? 8 : n < 100 ? 5 : 2) : '—';
}
function render() {
  const list = app.instruments;
  const active = list.filter(a => !a.paused);
  const squeeze = active.filter(a => a.status?.state === 'squeeze');
  $('#stat-total').textContent = list.length;
  $('#stat-active').textContent = active.length;
  $('#stat-squeeze').textContent = squeeze.length;
  $('#stat-alerts').textContent = app.lastScan ? `${app.lastScan.alerts ?? 0} terkirim` : 'Belum ada';
  $('#stat-alerts-note').textContent = app.lastScan ? `Scan ${dateText(app.lastScan.checked_at)}` : 'Hasil scan terakhir';
  $('#count-pill').textContent = `${list.length} / 20 instrumen`;
  $('#last-scan').textContent = app.lastScan ? `Terakhir dipindai: ${dateText(app.lastScan.checked_at)}` : 'Belum ada scan';
  const body = $('#asset-rows');
  if (list.length === 0) {
    body.innerHTML = '<tr><td class="empty-state" colspan="6">Belum ada instrumen. Tambahkan BTCUSDT, ETHUSDT, atau pasangan spot lainnya di atas.</td></tr>';
    return;
  }
  body.innerHTML = list.map(item => {
    const s = item.status || {};
    const state = item.paused ? 'paused' : (s.state || 'pending');
    const titles = {paused:'Dijeda',squeeze:'Squeeze',normal:'Normal',pending:'Belum scan',error:'Error'};
    const base = item.symbol.replace(/(USDT|USDC|FDUSD|BTC|ETH|EUR|TRY)$/,'') || item.symbol;
    const quote = item.symbol.slice(base.length) || 'SPOT';
    const coinClass = ['BTC','ETH','SOL'].includes(base) ? base.toLowerCase() : '';
    let bw = '—';
    if (s.bandwidth != null && s.threshold != null) {
      const ratio = Math.max(0, Math.min(100, s.bandwidth / Math.max(s.threshold * 2, 0.001) * 100));
      bw = `<div class="band-metrics">${formatNumber(s.bandwidth,3)}% <small>/ ${formatNumber(s.threshold,3)}%</small></div><div class="tiny-track ${state==='squeeze'?'orange':''}"><span style="width:${ratio}%"></span></div>`;
    }
    return `<tr>
      <td><div class="asset-id"><div class="coin-logo ${coinClass}">${esc(base.slice(0,1))}</div><div class="coin-text"><b>${esc(base)} / ${esc(quote)}</b><small>${esc(item.symbol)} · Binance Spot</small></div></div></td>
      <td><span class="band-badge ${esc(state)}"><span class="badge-dot"></span>${esc(titles[state] || state)}</span></td>
      <td>${bw}</td><td>${s.price != null ? esc(priceText(s.price)) : '<span class="muted-value">—</span>'}</td>
      <td>${esc(dateText(s.checked_at))}</td>
      <td><div class="action-row"><button class="mini-btn" data-action="pause" data-symbol="${esc(item.symbol)}">${item.paused?'Aktifkan':'Jeda'}</button><button class="mini-btn remove" data-action="remove" data-symbol="${esc(item.symbol)}">Hapus</button></div></td>
    </tr>`;
  }).join('');
}
async function load() {
  const data = await api('/api/instruments');
  app.instruments = data.instruments || [];
  app.lastScan = data.last_scan || null;
  render();
}
async function addInstrument(value) {
  const symbol = String(value || '').trim().toUpperCase().replace(/[\s/\-]/g, '');
  if (!symbol) return;
  if (app.busy) return;
  app.busy = true;
  const btn = $('#add-form button[type="submit"]');
  btn.disabled = true;
  try {
    await api('/api/instruments', {method:'POST', body:JSON.stringify({symbol})});
    $('#symbol-input').value = '';
    toast(`${symbol} berhasil ditambahkan ke watchlist.`);
    await load();
  } catch(e) { toast(e.message, true); }
  finally { app.busy = false; btn.disabled = false; }
}
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('#login-error').textContent = '';
  const button = $('#login-form button[type="submit"]');
  button.disabled = true;
  try {
    await api('/api/auth/login', {method:'POST', body:JSON.stringify({password:$('#password').value})});
    $('#password').value = '';
    showDashboard();
    await load();
  } catch(e) { $('#login-error').textContent = e.message; }
  finally { button.disabled = false; }
});
$('#show-password').addEventListener('click', () => {
  const input = $('#password');
  input.type = input.type === 'password' ? 'text' : 'password';
});
$('#logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout', {method:'POST'}); } catch {}
  showLogin();
});
$('#add-form').addEventListener('submit', e => {e.preventDefault(); addInstrument($('#symbol-input').value);});
document.querySelectorAll('.quick-chip').forEach(btn => btn.addEventListener('click', () => addInstrument(btn.dataset.symbol)));
$('#asset-rows').addEventListener('click', async e => {
  const btn = e.target.closest('button[data-action]');
  if (!btn || app.busy) return;
  const symbol = btn.dataset.symbol;
  const item = app.instruments.find(i => i.symbol === symbol);
  if (!item) return;
  if (btn.dataset.action === 'remove' && !confirm(`Hapus ${symbol} dari daftar monitoring?`)) return;
  app.busy = true; btn.disabled = true;
  try {
    if (btn.dataset.action === 'remove') {
      await api(`/api/instruments/${encodeURIComponent(symbol)}`, {method:'DELETE'});
      toast(`${symbol} dihapus.`);
    } else {
      await api(`/api/instruments/${encodeURIComponent(symbol)}/pause`, {method:'PATCH', body:JSON.stringify({paused:!item.paused})});
      toast(`${symbol} ${item.paused ? 'diaktifkan':'dijeda'}.`);
    }
    await load();
  } catch(err) {toast(err.message,true);}
  finally {app.busy = false; btn.disabled = false;}
});
$('#test-telegram').addEventListener('click', async () => {
  const btn = $('#test-telegram'); btn.disabled = true;
  try { await api('/api/telegram/test', {method:'POST'}); toast('Pesan tes berhasil dikirim ke Telegram.'); }
  catch(e) {toast(e.message, true);}
  finally {btn.disabled = false;}
});
$('#scan-now').addEventListener('click', async () => {
  const btn = $('#scan-now'); btn.disabled = true; btn.textContent = 'Memindai...';
  try {
    const scan = await api('/api/scan', {method:'POST'});
    if (scan.state === 'already_running') toast('Scanner lain masih bekerja.');
    else toast(`Scan selesai: ${scan.total} instrumen, ${scan.alerts} alert, ${scan.errors} error.`);
    await load();
  } catch(e) {toast(e.message,true);}
  finally {btn.disabled = false;btn.innerHTML='<span class="btn-icon">◌</span> Scan sekarang';}
});
(async () => {
  try {
    const status = await api('/api/auth/me');
    if (!status.authenticated) return showLogin();
    showDashboard();
    await load();
  } catch(e) {
    if ($('#dashboard').classList.contains('hidden')) showLogin();
    toast('Gagal memuat data: ' + e.message, true);
  }
})();
