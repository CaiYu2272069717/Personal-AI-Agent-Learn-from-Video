let allCases = [];
let activeRun = null;
let resultFilter = 'all';

const categoryNames = {
  completion: 'Task Completion', citation: 'Citation Correctness', tool: 'Tool Accuracy',
  security: 'Security Guard', reliability: 'Revert & Recovery', observability: 'Trace Coverage',
  comparison: 'Experiment Readiness'
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

async function api(url, options = {}) {
  const response = await fetch(url, {headers: {'Content-Type': 'application/json', ...(options.headers || {})}, ...options});
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try { const data = await response.json(); message = data.detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

async function initConsole() {
  try {
    const data = await api('/api/evaluation/cases');
    allCases = data.cases;
    document.getElementById('case-count').textContent = data.count;
    renderCaseMatrix();
    await loadRuns();
  } catch (error) {
    document.getElementById('category-matrix').innerHTML = `<div class="eval-empty">加载失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderCaseMatrix() {
  const counts = {};
  allCases.forEach(item => { counts[item.category] = (counts[item.category] || 0) + 1; });
  const resultBuckets = activeRun?.metrics?.categories || {};
  document.getElementById('category-matrix').innerHTML = Object.entries(counts).map(([key, total]) => {
    const stats = resultBuckets[key];
    const rate = stats ? Math.round(stats.rate * 100) : 0;
    const display = stats ? `${rate}%` : `${total}`;
    const meta = stats ? `${stats.passed} / ${stats.total} passed` : `${total} golden cases`;
    return `<div class="eval-metric"><span class="eval-metric-label">${escapeHtml(categoryNames[key] || key)}</span><strong class="eval-metric-value">${display}</strong><span class="eval-metric-meta">${meta}</span><div class="eval-meter"><i style="width:${stats ? rate : Math.min(100, total * 10)}%"></i></div></div>`;
  }).join('');
}

async function runSuite() {
  const button = document.getElementById('run-button');
  const mode = document.getElementById('run-mode').value;
  if (mode === 'live' && !confirm('LIVE 模式会调用当前配置的真实模型 API，并产生 Token 与费用。继续吗？')) return;
  button.disabled = true; button.textContent = 'RUNNING...';
  try {
    const run = await api('/api/evaluation/runs', {method: 'POST', body: JSON.stringify({mode, label: mode === 'live' ? 'live-regression' : 'offline-regression'})});
    await selectRun(run.id, run);
    await loadRuns();
  } catch (error) { alert(`评测失败：${error.message}`); }
  finally { button.disabled = false; button.textContent = '运行回归套件'; }
}

async function loadRuns() {
  const data = await api('/api/evaluation/runs?limit=30');
  const list = document.getElementById('runs-list');
  if (!data.runs.length) { list.innerHTML = '<div class="eval-empty">暂无运行，先执行一次离线回归。</div>'; return; }
  list.innerHTML = data.runs.map(run => {
    const rate = Math.round((run.metrics?.pass_rate || 0) * 100);
    return `<div class="eval-run ${activeRun?.id === run.id ? 'active' : ''}" onclick="selectRun('${run.id}')"><i class="eval-run-marker"></i><div><strong>${escapeHtml(run.label)}</strong><small>${escapeHtml(run.mode)} · ${escapeHtml((run.created_at || '').replace('T',' '))}</small></div><span class="eval-run-rate">${rate}%</span></div>`;
  }).join('');
  if (!activeRun) await selectRun(data.runs[0].id);
}

async function selectRun(runId, provided = null) {
  activeRun = provided || await api(`/api/evaluation/runs/${runId}`);
  const metrics = activeRun.metrics || {};
  document.getElementById('pass-rate').textContent = `${Math.round((metrics.pass_rate || 0) * 100)}%`;
  document.getElementById('latency').textContent = `${Math.round(metrics.p50_latency_ms || 0)} / ${Math.round(metrics.p95_latency_ms || 0)}`;
  document.getElementById('usage').textContent = `${metrics.total_tokens || 0} / $${Number(metrics.cost_usd || 0).toFixed(4)}`;
  document.getElementById('current-run-id').textContent = `RUN ${activeRun.id.slice(0, 12).toUpperCase()} · ${activeRun.status.toUpperCase()}`;
  document.getElementById('current-model').textContent = `MODEL ${activeRun.model || '—'} · ${activeRun.mode.toUpperCase()}`;
  const report = document.getElementById('report-link'); report.href = `/api/evaluation/runs/${activeRun.id}/report`; report.classList.remove('disabled');
  renderCaseMatrix(); renderResults();
  document.getElementById('trace-list').innerHTML = '<div class="eval-empty">选择案例查看单步骤 Trace</div>';
  document.getElementById('trace-count').textContent = '0 STEPS';
  await refreshRunHighlight();
}

async function refreshRunHighlight() {
  const data = await api('/api/evaluation/runs?limit=30');
  document.getElementById('runs-list').innerHTML = data.runs.map(run => {
    const rate = Math.round((run.metrics?.pass_rate || 0) * 100);
    return `<div class="eval-run ${activeRun?.id === run.id ? 'active' : ''}" onclick="selectRun('${run.id}')"><i class="eval-run-marker"></i><div><strong>${escapeHtml(run.label)}</strong><small>${escapeHtml(run.mode)} · ${escapeHtml((run.created_at || '').replace('T',' '))}</small></div><span class="eval-run-rate">${rate}%</span></div>`;
  }).join('');
}

function filterResults(filter, button) {
  resultFilter = filter;
  document.querySelectorAll('.eval-filter button').forEach(item => item.classList.remove('active'));
  button.classList.add('active'); renderResults();
}

function renderResults() {
  const body = document.getElementById('results-body');
  if (!activeRun?.results?.length) { body.innerHTML = '<tr><td colspan="6" class="eval-empty">当前运行无案例结果</td></tr>'; return; }
  const results = activeRun.results.filter(item => resultFilter === 'all' || (resultFilter === 'passed' && item.passed) || (resultFilter === 'failed' && !item.passed && item.status !== 'skipped') || item.status === resultFilter);
  body.innerHTML = results.map(item => {
    const state = item.status === 'skipped' ? ['skip','SKIP'] : item.passed ? ['pass','PASS'] : ['fail','FAIL'];
    const reasons = item.error_message || item.details?.reasons?.join('; ') || item.details?.reason || '—';
    return `<tr onclick="loadTrace('${item.case_id}')"><td><strong>${escapeHtml(item.case_name)}</strong><br><span class="eval-code">${escapeHtml(item.case_id)}</span></td><td>${escapeHtml(categoryNames[item.category] || item.category)}</td><td><span class="eval-status ${state[0]}">${state[1]}</span></td><td class="eval-code">${Number(item.latency_ms || 0).toFixed(1)} ms</td><td class="eval-attribution">${escapeHtml(reasons)}</td><td><span class="eval-trace-link">OPEN →</span></td></tr>`;
  }).join('') || '<tr><td colspan="6" class="eval-empty">没有匹配结果</td></tr>';
}

async function loadTrace(caseId) {
  if (!activeRun) return;
  const data = await api(`/api/evaluation/runs/${activeRun.id}/traces?case_id=${encodeURIComponent(caseId)}`);
  document.getElementById('trace-count').textContent = `${data.traces.length} STEPS`;
  document.getElementById('trace-list').innerHTML = data.traces.length ? data.traces.map(trace => {
    const payload = JSON.stringify(trace.payload || {});
    return `<div class="eval-trace"><div class="eval-trace-head"><span>${escapeHtml(trace.kind)} / ${escapeHtml(trace.name)} / ${escapeHtml(trace.phase)}</span><span>${Number(trace.duration_ms || 0).toFixed(1)} ms</span></div><p>${escapeHtml(payload.slice(0, 260))}</p></div>`;
  }).join('') : '<div class="eval-empty">该案例没有 Trace（通常是离线跳过或纯规则判定）。</div>';
}

function openCompare() { document.getElementById('compare-modal').classList.add('active'); }
function closeCompare() { document.getElementById('compare-modal').classList.remove('active'); }

async function runCompare() {
  const mode = document.getElementById('run-mode').value;
  if (mode === 'live' && !confirm('将连续运行 baseline 和 candidate 两套真实模型评测，费用约为单次的两倍。继续吗？')) return;
  const button = document.getElementById('compare-button'); button.disabled = true; button.textContent = 'COMPARING...';
  const result = document.getElementById('compare-result'); result.textContent = '';
  const variant = prefix => ({
    model: document.getElementById(`${prefix}-model`).value.trim() || undefined,
    temperature: Number(document.getElementById(`${prefix}-temp`).value),
    prompt_suffix: document.getElementById(`${prefix}-prompt`).value.trim(),
    prompt_version: prefix
  });
  try {
    const data = await api('/api/evaluation/compare', {method:'POST', body: JSON.stringify({mode, baseline: variant('base'), candidate: variant('candidate')})});
    const delta = data.delta;
    result.innerHTML = `<strong>DELTA (Candidate - Baseline)</strong><br>通过率 ${delta.pass_rate >= 0 ? '+' : ''}${(delta.pass_rate * 100).toFixed(1)}pp<br>P50 ${delta.p50_latency_ms >= 0 ? '+' : ''}${delta.p50_latency_ms.toFixed(1)} ms<br>P95 ${delta.p95_latency_ms >= 0 ? '+' : ''}${delta.p95_latency_ms.toFixed(1)} ms<br>Token ${delta.total_tokens >= 0 ? '+' : ''}${delta.total_tokens}<br>Cost ${delta.cost_usd >= 0 ? '+' : ''}$${delta.cost_usd.toFixed(6)}`;
    await selectRun(data.candidate.id, data.candidate);
  } catch (error) { result.textContent = `对比失败：${error.message}`; }
  finally { button.disabled = false; button.textContent = '开始对比'; }
}

document.addEventListener('DOMContentLoaded', initConsole);
