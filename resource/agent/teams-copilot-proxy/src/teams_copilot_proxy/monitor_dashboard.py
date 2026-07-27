"""Monitor 面板：单页 HTML，浏览器端轮询 /monitor/api/*。

回环客户端（127.0.0.1）默认免 Bearer 直连，页面顶部显示当前 substrate token
状态（掩码 + 过期时间）并提供一键复制；非回环访问仍需输入 Bearer token
（存 localStorage）。面板提供一键清空调试数据按钮，无配置修改、无导出。
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Copilot Proxy Monitor</title>
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
  <h1>Copilot Proxy Monitor</h1>
  <nav>
    <button data-view="summary" class="active">Summary</button>
    <button data-view="requests">Requests</button>
    <button data-view="context">Context</button>
    <button data-view="errors">Errors</button>
  </nav>
  <span id="tokeninfo" class="muted" style="margin-left:auto;font-size:12px"></span>
  <button id="copytoken" style="display:none;background:#274b73;color:#cde;border:none;border-radius:4px;padding:4px 8px;cursor:pointer;font-size:12px" onclick="copyToken()">copy token</button>
  <button id="cleardb" style="background:#c62828;color:#fff;border:none;border-radius:4px;padding:4px 8px;cursor:pointer;font-size:12px" onclick="clearDB()">clear db</button>
  <span id="status" class="muted" style="font-size:12px"></span>
</header>
<div id="tokenbar">
  Bearer token required (remote access only):
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

async function loadSession() {
  try {
    const s = await api('session');
    const t = s.token || {};
    const label = (t.valid ? 'token ' : 'token INVALID ')
      + (t.masked || '') + (t.seconds_remaining != null ? ' · ' + Math.floor(t.seconds_remaining / 60) + 'min left' : '');
    document.getElementById('tokeninfo').textContent = label;
    document.getElementById('copytoken').style.display = s.loopback ? '' : 'none';
  } catch (e) { /* 401 handled by api() */ }
}
async function copyToken() {
  try {
    const d = await api('token');
    await navigator.clipboard.writeText(d.access_token);
    const btn = document.getElementById('copytoken');
    btn.textContent = 'copied!';
    setTimeout(() => { btn.textContent = 'copy token'; }, 1500);
  } catch (e) {
    document.getElementById('status').textContent = 'copy failed: ' + e.message;
  }
}
async function clearDB() {
  if (!confirm('Clear all monitor data? This cannot be undone.')) return;
  try {
    const res = await fetch('/monitor/api/clear', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token() }
    });
    if (res.status === 401) { document.getElementById('tokenbar').style.display = 'block'; throw new Error('unauthorized'); }
    if (!res.ok) throw new Error('http ' + res.status);
    const d = await res.json();
    document.getElementById('status').textContent =
      `cleared requests=${d.requests} attempts=${d.attempts} tool_calls=${d.tool_calls} events=${d.events}`;
    await refresh();
  } catch (e) {
    document.getElementById('status').textContent = 'clear failed: ' + e.message;
  }
}
function card(k, v) { return `<div class="card"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`; }

async function renderSummary() {
  const [s, tools, eff, ge] = await Promise.all([
    api('summary'), api('tools'), api('tool-efficiency'), api('guard-effectiveness')
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
  html += '<h2>Guard effectiveness (retry recovery by guard / tone)</h2>';
  if (ge.guards.length) {
    html += '<table><tr><th>guard</th><th>tone</th><th>hits</th>'
      + '<th>recovered</th><th>exhausted</th><th>recovery rate</th></tr>'
      + ge.guards.map(g => `<tr><td>${esc(g.guard)}</td><td>${esc(g.tone)}</td>`
        + `<td>${g.hits}</td><td class="ok">${g.recovered}</td>`
        + `<td class="guard">${g.exhausted}</td>`
        + `<td>${(g.recovery_rate * 100).toFixed(1)}%</td></tr>`).join('')
      + '</table>';
  } else {
    html += '<p class="muted">no guard hits yet</p>';
  }
  html += '<h2>Tones</h2><table><tr><th>tone</th><th>requests</th></tr>'
    + s.tones.map(t => `<tr><td>${esc(t.tone)}</td><td>${t.count}</td></tr>`).join('')
    + '</table>';
  html += '<h2>Tools</h2><table><tr><th>tool</th><th>category</th><th>calls</th><th>closed</th><th>errors</th><th>error rate</th></tr>'
    + tools.tools.map(t => `<tr><td>${esc(t.name)}</td><td>${esc(t.category)}</td><td>${t.calls}</td><td>${t.closed}</td><td>${t.errors}</td><td>${(t.error_rate * 100).toFixed(1)}%</td></tr>`).join('')
    + '</table>';
  content.innerHTML = html;
}

function shortPath(p) {
  if (!p) return '';
  const parts = String(p).split('/');
  return parts.length > 2 ? '…/' + parts.slice(-2).join('/') : p;
}
function pct(v) { return v == null ? '' : (v * 100).toFixed(1) + '%'; }

async function renderRequests() {
  const data = await api('requests?limit=100');
  let html = '<table><tr><th>time</th><th>id</th><th>session</th><th>project</th><th>turn</th><th>model</th><th>tone</th><th>effort</th><th>mode</th><th>tools</th><th>ctx%</th><th>stream</th><th>status</th><th>guard</th><th>tokens</th><th>ms</th></tr>'
    + data.requests.map(r =>
      `<tr class="req" data-id="${esc(r.id)}"><td>${fmtTs(r.ts)}</td><td>${esc(r.id.slice(0, 18))}…</td>`
      + `<td>${esc(r.client_session_id || r.session_key || '')}</td>`
      + `<td title="${esc(r.project_path || '')}">${esc(shortPath(r.project_path))}</td>`
      + `<td>${esc(r.turn_kind || '')}</td><td>${esc(r.model)}</td><td>${esc(r.tone)}</td>`
      + `<td>${esc(r.reasoning_effort || '')}</td><td>${esc(r.planning_mode || '')}</td>`
      + `<td title="${esc(r.tool_kinds || '')}">${r.tools_count || ''}</td><td>${pct(r.context_pct)}</td>`
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
  html += '<h2>Request shape</h2><table><tr><th>project</th><th>turn</th><th>client</th>'
    + '<th>msgs</th><th>transcript B</th><th>system B</th><th>ctx%</th><th>tools</th>'
    + '<th>tool kinds</th><th>tools fp</th><th>temp</th><th>top_p</th><th>max tok</th>'
    + '<th>resp fmt</th><th>injections</th></tr>'
    + `<tr><td>${esc(d.project_path || '')}</td><td>${esc(d.turn_kind || '')}</td>`
    + `<td>${esc(d.client_agent || '')}</td><td>${d.messages_count ?? ''}</td>`
    + `<td>${d.transcript_bytes ?? ''}</td><td>${d.system_bytes ?? ''}</td>`
    + `<td>${pct(d.context_pct)}</td><td>${d.tools_count ?? ''}</td>`
    + `<td>${esc(d.tool_kinds || '')}</td><td>${esc(d.tools_fingerprint || '')}</td>`
    + `<td>${d.temperature ?? ''}</td><td>${d.top_p ?? ''}</td><td>${d.max_tokens ?? ''}</td>`
    + `<td>${esc(d.response_format || '')}</td><td>${esc(d.injections || '')}</td></tr></table>`;
  if (d.prompt_summary) html += '<h2>Prompt excerpt</h2><pre>' + esc(d.prompt_summary) + '</pre>';
  if (d.reply_snippet) html += '<h2>Reply excerpt</h2><pre>' + esc(d.reply_snippet) + '</pre>';
  html += '<h2>Attempt chain</h2><table><tr><th>#</th><th>ms</th><th>phase</th><th>guard</th><th>retried</th><th>status</th><th>why</th><th>text</th></tr>'
    + d.attempts.map(a =>
      `<tr><td>${a.seq}</td><td>${a.duration_ms}</td><td>${esc(a.phase || '')}</td><td>${esc(a.guard || '')}</td>`
      + `<td>${a.retried ? 'yes' : ''}</td><td class="${esc(a.status)}">${esc(a.status)}</td>`
      + `<td>${a.error_detail ? esc(a.error_detail) : ''}</td>`
      + `<td>${a.text ? '<pre>' + esc(a.text) + '</pre>' : '<span class="muted">not captured</span>'}</td></tr>`
    ).join('') + '</table>';
  html += '<h2>Upstream (M365)</h2><table><tr><th>#</th><th>conversation</th><th>req id</th>'
    + '<th>sent B</th><th>1st frame ms</th><th>frames</th><th>msg types</th>'
    + '<th>reply B</th><th>cites</th><th>clean end</th><th>status</th><th>close</th>'
    + '<th>injections</th></tr>'
    + d.attempts.map(a =>
      `<tr><td>${a.seq}</td><td>${esc((a.conversation_id || '').slice(0, 12))}</td>`
      + `<td>${esc((a.client_request_id || '').slice(0, 8))}</td><td>${a.sent_bytes ?? ''}</td>`
      + `<td>${a.first_frame_ms ?? ''}</td><td>${a.frames ?? ''}</td>`
      + `<td>${esc(a.message_types || '')}</td><td>${a.reply_bytes ?? ''}</td>`
      + `<td>${a.citations ?? ''}</td>`
      + `<td class="${a.terminated_cleanly === 0 ? 'error' : ''}">${a.terminated_cleanly == null ? '' : (a.terminated_cleanly ? 'yes' : 'NO')}</td>`
      + `<td>${a.upstream_status ?? ''}</td><td>${esc(a.close_reason || '')}</td>`
      + `<td>${esc(a.injections || '')}</td></tr>`
      + (a.final_frame ? `<tr><td></td><td colspan="12"><pre>${esc(a.final_frame)}</pre></td></tr>` : '')
      + (a.sent_head ? `<tr><td></td><td colspan="12"><pre>${esc(a.sent_head)}${a.sent_tail ? '\\n…\\n' + esc(a.sent_tail) : ''}</pre></td></tr>` : '')
    ).join('') + '</table>';
  box.innerHTML = html;
  box.style.display = 'block';
}

async function renderContext() {
  const data = await api('context-pressure?limit=200');
  content.innerHTML = '<h2>Context pressure</h2>'
    + '<table><tr><th>time</th><th>session</th><th>project</th><th>turn</th>'
    + '<th>msgs</th><th>prompt tokens</th><th>ctx%</th><th>transcript B</th>'
    + '<th>system B</th><th>tools</th><th>status</th><th>guard</th></tr>'
    + data.requests.slice().reverse().map(r =>
      `<tr><td>${fmtTs(r.ts)}</td><td>${esc(r.client_session_id || r.session_key || '')}</td>`
      + `<td title="${esc(r.project_path || '')}">${esc(shortPath(r.project_path))}</td>`
      + `<td>${esc(r.turn_kind || '')}</td><td>${r.messages_count ?? ''}</td>`
      + `<td>${r.prompt_tokens ?? ''}</td><td>${pct(r.context_pct)}</td>`
      + `<td>${r.transcript_bytes ?? ''}</td><td>${r.system_bytes ?? ''}</td>`
      + `<td>${r.tools_count ?? ''}</td><td class="${esc(r.status)}">${esc(r.status)}</td>`
      + `<td>${esc(r.guard || '')}</td></tr>`
    ).join('') + '</table>';
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
  loadSession();
  try {
    document.getElementById('status').textContent = 'refreshing…';
    if (view === 'summary') await renderSummary();
    else if (view === 'requests') await renderRequests();
    else if (view === 'context') await renderContext();
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
