"""只读 Monitor 面板：单页 HTML，浏览器端用 Bearer token 轮询 /monitor/api/*。

页面本身不含任何数据；所有数据请求都带 Authorization 头，token 首次输入后存
localStorage。纯只读——无配置修改、无清库、无导出。
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Teams Copilot Proxy Monitor</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #f5f6f8; color: #1c2430; }
  header { background: #16324f; color: #fff; padding: 10px 20px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 16px; margin: 0; }
  nav button { background: none; border: none; color: #bcd; padding: 6px 10px; cursor: pointer; font-size: 14px; }
  nav button.active { color: #fff; border-bottom: 2px solid #6cf; }
  main { padding: 16px 20px; }
  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
  .card { background: #fff; border-radius: 8px; padding: 12px 16px; min-width: 130px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .card .v { font-size: 22px; font-weight: 600; }
  .card .k { font-size: 12px; color: #667; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  th, td { text-align: left; padding: 7px 10px; font-size: 13px; border-bottom: 1px solid #eef; }
  th { background: #eef2f7; color: #345; }
  tr.req { cursor: pointer; }
  tr.req:hover { background: #f0f6ff; }
  .ok { color: #1a7f37; } .guard { color: #b26a00; } .error { color: #c62828; }
  #detail { background: #fff; border-radius: 8px; padding: 14px; margin-top: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.08); display: none; }
  pre { background: #f2f4f7; padding: 8px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; font-size: 12px; max-height: 240px; overflow: auto; }
  #tokenbar { display: none; padding: 14px 20px; background: #fff3cd; }
  #tokenbar input { width: 380px; padding: 5px; }
  .muted { color: #889; }
  h2 { font-size: 15px; margin: 18px 0 8px; }
</style>
</head>
<body>
<header>
  <h1>Teams Copilot Proxy Monitor</h1>
  <nav>
    <button data-view="summary" class="active">Summary</button>
    <button data-view="requests">Requests</button>
    <button data-view="errors">Errors</button>
  </nav>
  <span id="status" class="muted" style="margin-left:auto;font-size:12px"></span>
</header>
<div id="tokenbar">
  Bearer token required:
  <input id="token" type="password" placeholder="paste proxy token">
  <button onclick="saveToken()">Save</button>
</div>
<main id="content"></main>
<script>
let view = 'summary';
const content = document.getElementById('content');

function token() { return localStorage.getItem('monitor_token') || ''; }
function saveToken() {
  localStorage.setItem('monitor_token', document.getElementById('token').value.trim());
  document.getElementById('tokenbar').style.display = 'none';
  refresh();
}
async function api(path) {
  const res = await fetch('/monitor/api/' + path, {
    headers: { 'Authorization': 'Bearer ' + token() }
  });
  if (res.status === 401) { document.getElementById('tokenbar').style.display = 'block'; throw new Error('unauthorized'); }
  if (!res.ok) throw new Error('http ' + res.status);
  return res.json();
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
function fmtTs(ts) { return new Date(ts * 1000).toLocaleString(); }
function card(k, v) { return `<div class="card"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`; }

async function renderSummary() {
  const [s, tools, eff] = await Promise.all([
    api('summary'), api('tools'), api('tool-efficiency')
  ]);
  let html = '<div class="cards">'
    + card('requests', s.requests)
    + card('total tokens', s.total_tokens)
    + card('errors', s.errors)
    + card('guard hits', s.guarded)
    + card('error rate', (s.error_rate * 100).toFixed(1) + '%')
    + card('guard rate', (s.guard_rate * 100).toFixed(1) + '%')
    + '</div>';
  html += '<h2>Tool planning (baseline for router A/B)</h2>';
  if (eff.modes.length) {
    html += '<table><tr><th>mode</th><th>tool reqs</th><th>tool-call yield</th>'
      + '<th>avg attempts</th><th>avg corrections</th><th>guard rate</th>'
      + '<th>error rate</th><th>p50 ms</th><th>p95 ms</th><th>shell recov</th>'
      + '<th>deduped</th><th>repeat call</th><th>repeat fail</th></tr>'
      + eff.modes.map(m => `<tr><td>${esc(m.planning_mode)}</td><td>${m.requests}</td>`
        + `<td>${(m.tool_call_yield * 100).toFixed(1)}%</td>`
        + `<td>${m.avg_attempts.toFixed(2)}</td><td>${m.avg_corrections.toFixed(2)}</td>`
        + `<td>${(m.guard_rate * 100).toFixed(1)}%</td><td>${(m.error_rate * 100).toFixed(1)}%</td>`
        + `<td>${m.p50_duration_ms ?? ''}</td><td>${m.p95_duration_ms ?? ''}</td>`
        + `<td>${m.shell_recovered}</td><td>${m.deduped}</td>`
        + `<td>${m.repeated_call}</td><td>${m.repeated_failure}</td></tr>`).join('')
      + '</table>';
  } else {
    html += '<p class="muted">no tool-bearing requests yet</p>';
  }
  html += '<h2>Tones</h2><table><tr><th>tone</th><th>requests</th></tr>'
    + s.tones.map(t => `<tr><td>${esc(t.tone)}</td><td>${t.count}</td></tr>`).join('')
    + '</table>';
  html += '<h2>Tools</h2><table><tr><th>tool</th><th>category</th><th>calls</th><th>closed</th><th>errors</th><th>error rate</th></tr>'
    + tools.tools.map(t => `<tr><td>${esc(t.name)}</td><td>${esc(t.category)}</td><td>${t.calls}</td><td>${t.closed}</td><td>${t.errors}</td><td>${(t.error_rate * 100).toFixed(1)}%</td></tr>`).join('')
    + '</table>';
  content.innerHTML = html;
}

async function renderRequests() {
  const data = await api('requests?limit=100');
  let html = '<table><tr><th>time</th><th>id</th><th>session</th><th>model</th><th>tone</th><th>stream</th><th>status</th><th>guard</th><th>tokens</th><th>ms</th></tr>'
    + data.requests.map(r =>
      `<tr class="req" data-id="${esc(r.id)}"><td>${fmtTs(r.ts)}</td><td>${esc(r.id.slice(0, 18))}…</td>`
      + `<td>${esc(r.session_key || '')}</td><td>${esc(r.model)}</td><td>${esc(r.tone)}</td>`
      + `<td>${r.stream ? 'yes' : ''}</td><td class="${esc(r.status)}">${esc(r.status)}</td>`
      + `<td>${esc(r.guard || '')}</td><td>${r.total_tokens}</td><td>${r.duration_ms}</td></tr>`
    ).join('') + '</table><div id="detail"></div>';
  content.innerHTML = html;
  content.querySelectorAll('tr.req').forEach(tr =>
    tr.addEventListener('click', () => showDetail(tr.dataset.id)));
}

async function showDetail(id) {
  const d = await api('requests/' + id);
  const box = document.getElementById('detail');
  let html = `<b>${esc(d.id)}</b> — ${esc(d.status)}${d.guard ? ' / ' + esc(d.guard) : ''}`
    + `${d.error ? '<pre>' + esc(d.error) + '</pre>' : ''}`;
  if (d.prompt_summary) html += '<h2>Prompt excerpt</h2><pre>' + esc(d.prompt_summary) + '</pre>';
  if (d.reply_snippet) html += '<h2>Reply excerpt</h2><pre>' + esc(d.reply_snippet) + '</pre>';
  html += '<h2>Attempt chain</h2><table><tr><th>#</th><th>ms</th><th>guard</th><th>retried</th><th>status</th><th>text</th></tr>'
    + d.attempts.map(a =>
      `<tr><td>${a.seq}</td><td>${a.duration_ms}</td><td>${esc(a.guard || '')}</td>`
      + `<td>${a.retried ? 'yes' : ''}</td><td class="${esc(a.status)}">${esc(a.status)}</td>`
      + `<td>${a.text ? '<pre>' + esc(a.text) + '</pre>' : '<span class="muted">not captured</span>'}</td></tr>`
    ).join('') + '</table>';
  box.innerHTML = html;
  box.style.display = 'block';
}

async function renderErrors() {
  const data = await api('errors?limit=200');
  content.innerHTML = '<table><tr><th>time</th><th>type</th><th>session</th><th>request</th><th>detail</th></tr>'
    + data.errors.map(e =>
      `<tr><td>${fmtTs(e.ts)}</td><td class="error">${esc(e.type)}</td><td>${esc(e.session_key || '')}</td>`
      + `<td>${esc((e.request_id || '').slice(0, 18))}</td><td>${esc(e.detail || '')}</td></tr>`
    ).join('') + '</table>';
}

async function refresh() {
  try {
    document.getElementById('status').textContent = 'refreshing…';
    if (view === 'summary') await renderSummary();
    else if (view === 'requests') await renderRequests();
    else await renderErrors();
    document.getElementById('status').textContent = 'updated ' + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById('status').textContent = e.message;
  }
}
document.querySelectorAll('nav button').forEach(btn =>
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    view = btn.dataset.view;
    refresh();
  }));
setInterval(refresh, 10000);
refresh();
</script>
</body>
</html>
"""
