/* chat.js - Agent 后台运行与可恢复交互层。
 * 这是 Agent 聊天页面的唯一 JS 入口，模板中不再包含内联脚本。
 */

// === 状态 ===
let conversationId = 0;
let isStreaming = false;
let pendingConfirm = null;
let uploadedFiles = [];
let activeRunId = null;
let activeEventSource = null;
let activeTurnEl = null;
let workspaceSettingsCache = null;

// === DOM 引用 ===
const messagesEl = document.getElementById('chat-messages');
const inputEl = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const stopBtn = document.getElementById('stop-btn');

// === marked 初始化 ===
marked.setOptions({
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, {language: lang}).value;
    return hljs.highlightAuto(code).value;
  },
  breaks: true,
  gfm: true,
});

// === 工具标签 ===
const toolLabels = {
  search_knowledge: '检索知识库', get_item: '读取知识条目', run_pipeline: '提交内容处理',
  web_search: '联网搜索', web_fetch: '读取网页', read_file: '读取文件',
  list_dir: '查看目录', glob_files: '搜索文件', write_file: '写入文件',
  edit_file: '编辑文件', run_command: '执行命令', run_python_sandbox: '运行代码沙箱',
  ocr_image: '识别图片文字',
};

// === 工具函数 ===
function esc(text) {
  const d = document.createElement('div');
  d.textContent = text || '';
  return d.innerHTML;
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function toggleHistory() {
  document.getElementById('history-panel').classList.toggle('open');
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

// === 输入框自适应高度 ===
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
});

// === 拖拽上传 ===
const wrapperEl = document.getElementById('chat-input-wrapper');
if (wrapperEl) {
  wrapperEl.addEventListener('dragover', e => { e.preventDefault(); wrapperEl.classList.add('dragover'); });
  wrapperEl.addEventListener('dragleave', e => { e.preventDefault(); wrapperEl.classList.remove('dragover'); });
  wrapperEl.addEventListener('drop', e => {
    e.preventDefault();
    wrapperEl.classList.remove('dragover');
    const files = Array.from(e.dataTransfer.files);
    files.forEach(f => {
      if (f.size > 10 * 1024 * 1024) { appendMessage('system', `文件 ${f.name} 超过 10MB 限制`); return; }
      uploadedFiles.push(f);
    });
    renderFileList();
  });
}
// === 流式状态 ===
function setStreaming(value) {
  isStreaming = value;
  sendBtn.style.display = value ? 'none' : 'flex';
  stopBtn.style.display = value ? 'flex' : 'none';
  const badge = document.querySelector('.chat-header-badge');
  if (badge) badge.textContent = value ? '工作中' : '在线';
  document.querySelectorAll('.message-revert-btn').forEach(button => {
    button.disabled = value;
  });
}

// === 消息渲染 ===
function appendMessage(role, content, files, messageId = 0) {
  const div = document.createElement('div');
  div.className = `message ${role}`;
  if (role === 'user') {
    let filesHtml = '';
    if (files && files.length) {
      filesHtml = '<div class="message-files">' + files.map(f => `<span class="message-file-badge">${esc(f.name)}</span>`).join('') + '</div>';
    }
    div.innerHTML = `
      <div class="message-avatar user-avatar">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      </div>
      <div class="message-body">
        <div class="message-role">你</div>
        ${filesHtml}
        <div class="message-content">${esc(content)}</div>
        <div class="message-actions"></div>
      </div>`;
  } else if (role === 'assistant') {
    div.innerHTML = `
      <div class="message-avatar assistant-avatar">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
      </div>
      <div class="message-body">
        <div class="message-role">Agent</div>
        <div class="message-content">${content ? marked.parse(content) : ''}</div>
      </div>`;
  } else {
    div.className = 'message system';
    div.innerHTML = `<div class="message-system-text">⚠ ${esc(content)}</div>`;
  }
  messagesEl.appendChild(div);
  if (role === 'user' && messageId) setMessageRevert(div, messageId);
  scrollToBottom();
  return div;
}

function setMessageRevert(message, messageId) {
  if (!message || !messageId) return;
  message.dataset.messageId = messageId;
  const actions = message.querySelector('.message-actions');
  if (!actions) return;
  actions.innerHTML = `
    <button class="message-revert-btn" type="button"
      onclick="revertToMessage(${Number(messageId)}, this)"
      title="将会话和 Agent 改动的文件恢复到这一轮之前"
      ${isStreaming ? 'disabled' : ''}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 14l-4-4 4-4"/><path d="M5 10h8a6 6 0 1 1 0 12h-3"/></svg>
      回退到此轮之前
    </button>`;
}

async function revertToMessage(messageId, button) {
  if (!conversationId || isStreaming) return;
  const confirmed = confirm(
    '确定回退到这一轮之前吗？\n\n这一轮及之后的对话会被删除，Agent 在这些轮次中创建、编辑或删除的文件会恢复。'
  );
  if (!confirmed) return;
  button.disabled = true;
  try {
    const response = await fetch('/api/agent/revert', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversation_id: conversationId, user_message_id: messageId}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '回退失败');
    await loadConversation(conversationId, false);
    appendMessage(
      'system',
      `已回退：移除 ${data.removed_messages || 0} 条消息，恢复 ${data.restored_files || 0} 个文件，移除 ${data.removed_files || 0} 个新建文件。`
    );
  } catch (error) {
    button.disabled = false;
    appendMessage('system', `回退失败: ${error.message}`);
  }
}

function renderWelcome() {
  messagesEl.innerHTML = `
    <div class="chat-welcome" id="chat-welcome">
      <div class="chat-welcome-icon"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></div>
      <h2>Learn Video Agent</h2>
      <p>基于你的知识库回答问题 · 支持联网搜索 · 可执行工具调用</p>
    </div>`;
}

// === Agent Turn（思考/执行过程折叠面板） ===
function appendAgentTurn() {
  const div = document.createElement('div');
  div.className = 'message assistant agent-turn';
  div.innerHTML = `
    <div class="message-avatar assistant-avatar">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
    </div>
    <div class="message-body">
      <div class="message-role">Agent</div>
      <details class="agent-process" open>
        <summary><span class="process-pulse"></span><span class="process-summary">正在思考</span><span class="process-chevron">⌄</span></summary>
        <div class="process-events"><div class="process-event thinking"><span class="process-event-icon"></span><span>正在理解问题并规划下一步</span></div></div>
      </details>
      <div class="message-content agent-answer" hidden></div>
    </div>`;
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function processSummary(turn, text, running = true) {
  if (!turn) return;
  const summary = turn.querySelector('.process-summary');
  const pulse = turn.querySelector('.process-pulse');
  if (summary) summary.textContent = text;
  if (pulse) pulse.classList.toggle('complete', !running);
}

function addProcessEvent(turn, data) {
  if (!turn) return;
  const events = turn.querySelector('.process-events');
  if (!events) return;
  const callId = data.call_id || `${data.type}-${events.children.length}`;
  let row = events.querySelector(`[data-call-id="${CSS.escape(callId)}"]`);

  if (data.type === 'status') {
    processSummary(turn, data.label || '正在思考', true);
    return;
  }
  if (data.type === 'confirm_needed') {
    processSummary(turn, `等待批准：${toolLabels[data.tool] || data.tool}`, true);
  } else if (data.type === 'tool_call') {
    processSummary(turn, `正在${toolLabels[data.tool] || data.tool}`, true);
  }

  if (!row) {
    row = document.createElement('div');
    row.className = 'process-event';
    row.dataset.callId = callId;
    events.appendChild(row);
  }
  const args = data.args ? JSON.stringify(data.args, null, 2) : '';
  const result = data.result || data.reason || '';
  if (data.type === 'tool_result') row.classList.add(data.rejected ? 'rejected' : 'done');
  if (data.type === 'tool_blocked') row.classList.add('blocked');
  row.innerHTML = `
    <span class="process-event-icon"></span>
    <span class="process-event-main">
      <strong>${esc(toolLabels[data.tool] || data.tool || '执行过程')}</strong>
      ${data.type === 'confirm_needed' ? '<small>等待你的批准</small>' : ''}
      ${data.type === 'tool_result' ? `<small>${data.rejected ? '已拒绝' : '执行完成'}</small>` : ''}
      ${(args || result) ? `<details class="process-detail"><summary>查看详情</summary><pre>${esc(args || result)}</pre></details>` : ''}
    </span>`;
}

// === Run 事件处理 ===
function handleRunEvent(data, turn) {
  if (data.type === 'text') {
    const answer = turn.querySelector('.agent-answer');
    answer.hidden = false;
    answer.dataset.markdown = (answer.dataset.markdown || '') + data.content;
    answer.innerHTML = marked.parse(answer.dataset.markdown);
    processSummary(turn, '正在组织回复', true);
  } else if (['status', 'tool_call', 'tool_result', 'confirm_needed', 'tool_blocked'].includes(data.type)) {
    addProcessEvent(turn, data);
    if (data.type === 'confirm_needed') showConfirmDialog(data);
  } else if (data.type === 'done') {
    const answer = turn.querySelector('.agent-answer');
    if (answer.hidden && data.content) {
      answer.hidden = false;
      answer.dataset.markdown = data.content;
      answer.innerHTML = marked.parse(data.content);
    }
    processSummary(turn, '思考与执行过程', false);
    turn.querySelector('.agent-process')?.removeAttribute('open');
    finishActiveRun();
  } else if (data.type === 'cancelled') {
    processSummary(turn, '任务已停止', false);
    appendMessage('system', data.content || '已停止生成');
    finishActiveRun();
  } else if (data.type === 'error') {
    processSummary(turn, '任务遇到错误', false);
    appendMessage('system', data.content || 'Agent 运行失败');
    finishActiveRun();
  }
  scrollToBottom();
}

function finishActiveRun() {
  if (activeEventSource) activeEventSource.close();
  activeEventSource = null;
  activeRunId = null;
  activeTurnEl = null;
  setStreaming(false);
  loadConversations();
}

function attachRun(runId, turn, after = 0) {
  if (activeEventSource) activeEventSource.close();
  activeRunId = runId;
  activeTurnEl = turn;
  setStreaming(true);
  const source = new EventSource(`/api/agent/runs/${runId}/stream?after=${after}`);
  activeEventSource = source;
  source.onmessage = event => {
    try { handleRunEvent(JSON.parse(event.data), turn); } catch (error) { console.warn(error); }
  };
  source.onerror = async () => {
    try {
      const response = await fetch(`/api/agent/runs/${runId}`);
      if (!response.ok) return;
      const run = await response.json();
      if (['completed', 'failed', 'cancelled'].includes(run.status)) finishActiveRun();
    } catch (_) {}
  };
}

// === 发送消息（后台 run） ===
async function sendMessage() {
  const msg = inputEl.value.trim();
  if (!msg || isStreaming) return;
  document.getElementById('chat-welcome')?.remove();
  let fullMsg = msg;
  if (uploadedFiles.length) fullMsg = `[附件: ${uploadedFiles.map(f => f.name).join(', ')}]\n${msg}`;

  const userMessage = appendMessage('user', msg, uploadedFiles);
  inputEl.value = '';
  inputEl.style.height = 'auto';
  uploadedFiles = [];
  document.getElementById('file-list').innerHTML = '';
  const turn = appendAgentTurn();
  setStreaming(true);

  try {
    const response = await fetch('/api/agent/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: fullMsg, conversation_id: conversationId}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '提交失败');
    conversationId = data.conversation_id;
    setMessageRevert(userMessage, data.user_message_id);
    localStorage.setItem('lfv.currentConversationId', conversationId);
    loadConversations();
    attachRun(data.run_id, turn);
  } catch (error) {
    turn.remove();
    appendMessage('system', `连接错误: ${error.message}`);
    setStreaming(false);
  }
}

async function stopGeneration() {
  if (!activeRunId) return;
  stopBtn.disabled = true;
  try { await fetch(`/api/agent/runs/${activeRunId}/cancel`, {method: 'POST'}); }
  finally { stopBtn.disabled = false; }
}

// === 确认机制 ===
function showConfirmDialog(data) {
  pendingConfirm = data;
  const body = document.getElementById('confirm-body');
  body.innerHTML = `
    <p><strong>${esc(data.tool)}</strong> 需要你的确认</p>
    <p class="confirm-reason">${esc(data.reason || '')}</p>
    <pre class="confirm-args">${esc(JSON.stringify(data.args, null, 2))}</pre>`;
  document.getElementById('chat-confirm').style.display = 'flex';
}

async function handleConfirm(approved) {
  document.getElementById('chat-confirm').style.display = 'none';
  if (!pendingConfirm) return;
  const request = pendingConfirm;
  pendingConfirm = null;
  try {
    const response = await fetch('/api/agent/confirm', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversation_id: conversationId, call_id: request.call_id, approved}),
    });
    if (!response.ok) throw new Error('确认请求已失效');
  } catch (error) { appendMessage('system', error.message); }
}

// === 会话管理 ===
async function loadConversations() {
  try {
    const resp = await fetch('/api/agent/conversations');
    const data = await resp.json();
    renderConversationList(data.conversations || []);
  } catch (_) {}
}

function renderConversationList(conversations) {
  const list = document.getElementById('history-list');
  if (!conversations.length) {
    list.innerHTML = '<div class="history-empty">暂无历史会话</div>';
    return;
  }
  list.innerHTML = conversations.map(item => {
    const isActive = item.id === conversationId;
    const date = item.created_at ? new Date(item.created_at).toLocaleDateString('zh-CN') : '';
    const statusIcon = item.run_status === 'running' ? '<span class="history-running-dot"></span>' : '';
    return `<div class="history-item ${isActive ? 'active' : ''}" onclick="loadConversation(${item.id})">
      <div class="history-item-copy">
        <div class="history-item-title">${statusIcon}${esc(item.title || '新对话')}</div>
        <div class="history-item-date">${date}</div>
      </div>
      <button class="history-delete-btn" onclick="deleteConversation(event, ${item.id})" title="删除会话" aria-label="删除会话">×</button>
    </div>`;
  }).join('');
}

async function loadConversation(id, closePanel = true) {
  if (activeEventSource) activeEventSource.close();
  activeEventSource = null;
  activeRunId = null;
  setStreaming(false);
  conversationId = id;
  localStorage.setItem('lfv.currentConversationId', id);
  messagesEl.innerHTML = '';
  try {
    const [messagesResponse, runResponse] = await Promise.all([
      fetch(`/api/agent/conversations/${id}/messages`),
      fetch(`/api/agent/conversations/${id}/active-run`),
    ]);
    const messagesData = await messagesResponse.json();
    (messagesData.messages || []).forEach(message => {
      if (message.role === 'user') {
        appendMessage('user', message.content, null, message.can_revert ? message.id : 0);
      }
      else if (message.role === 'assistant') appendMessage('assistant', message.content);
    });
    const runData = await runResponse.json();
    if (runData.run) attachRun(runData.run.id, appendAgentTurn());
    else if (!(messagesData.messages || []).length) renderWelcome();
  } catch (error) {
    appendMessage('system', `加载会话失败: ${error.message}`);
  }
  loadConversations();
  if (closePanel && document.getElementById('history-panel').classList.contains('open')) toggleHistory();
}

async function deleteConversation(event, id) {
  event.stopPropagation();
  if (!confirm('删除这个历史会话？此操作不可撤销。')) return;
  const response = await fetch(`/api/agent/conversations/${id}`, {method: 'DELETE'});
  if (!response.ok) return;
  if (conversationId === id) newConversation(false);
  loadConversations();
}

function newConversation(closePanel = true) {
  if (activeEventSource) activeEventSource.close();
  activeEventSource = null;
  activeRunId = null;
  activeTurnEl = null;
  conversationId = 0;
  localStorage.removeItem('lfv.currentConversationId');
  setStreaming(false);
  renderWelcome();
  loadConversations();
  if (closePanel && document.getElementById('history-panel').classList.contains('open')) toggleHistory();
  inputEl.focus();
}

function clearChat() { newConversation(); }

// === 文件上传 ===
function handleFileUpload(event) {
  const files = Array.from(event.target.files);
  files.forEach(f => {
    if (f.size > 10 * 1024 * 1024) { appendMessage('system', `文件 ${f.name} 超过 10MB 限制`); return; }
    uploadedFiles.push(f);
  });
  renderFileList();
  event.target.value = '';
}

function renderFileList() {
  const el = document.getElementById('file-list');
  el.innerHTML = uploadedFiles.map((f, i) => `
    <span class="upload-file-tag">${esc(f.name)}<button onclick="removeFile(${i})">&times;</button></span>
  `).join('');
}

function removeFile(idx) {
  uploadedFiles.splice(idx, 1);
  renderFileList();
}

// === 导出 ===
function exportChat() {
  const msgs = messagesEl.querySelectorAll('.message');
  let text = '';
  msgs.forEach(m => {
    const role = m.classList.contains('user') ? '用户' : m.classList.contains('assistant') ? 'Agent' : '系统';
    const content = m.querySelector('.message-content')?.textContent || m.querySelector('.agent-answer')?.textContent || m.querySelector('.message-system-text')?.textContent || '';
    if (content.trim()) text += `[${role}]\n${content.trim()}\n\n`;
  });
  const blob = new Blob([text], {type: 'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `对话_${new Date().toLocaleString('zh-CN').replace(/[/:]/g, '-')}.txt`;
  a.click();
}

// === 权限开关 ===
async function syncPermissionToggle() {
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    const el = document.getElementById('agent-full-access');
    if (el) el.checked = Boolean(settings.agent_permission?.full_access);
  } catch (_) {}
}

async function setAgentFullAccess(enabled) {
  const toggle = document.getElementById('agent-full-access');
  toggle.disabled = true;
  try {
    const response = await fetch('/api/settings', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({agent_permission: {full_access: enabled}}),
    });
    if (!response.ok) throw new Error('权限设置保存失败');
  } catch (error) {
    toggle.checked = !enabled;
    appendMessage('system', error.message);
  } finally { toggle.disabled = false; }
}

// === 工作空间设置弹窗 ===
async function openWorkspaceSettings() {
  document.getElementById('workspace-modal').classList.add('active');
  try {
    workspaceSettingsCache = await fetch('/api/settings').then(r => r.json());
    document.getElementById('workspace-dir').value = workspaceSettingsCache.workspace_dir || '';
    document.getElementById('agent-model').value = workspaceSettingsCache.agent_llm?.model || '';
    document.getElementById('max-rounds').value = workspaceSettingsCache.agent_llm?.max_tool_rounds || 10;
    document.getElementById('workspace-full-access').checked = Boolean(workspaceSettingsCache.agent_permission?.full_access);
  } catch (_) {}
}

function closeWorkspaceSettings() {
  document.getElementById('workspace-modal').classList.remove('active');
}

async function fetchWorkspaceModels(button) {
  if (!workspaceSettingsCache) return;
  button.classList.add('loading');
  button.disabled = true;
  try {
    const response = await fetch('/api/settings/models', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        base_url: workspaceSettingsCache.agent_llm.base_url,
        api_key: workspaceSettingsCache.agent_llm.api_key,
        section: 'agent_llm',
      }),
    });
    const data = await response.json();
    if (data.error) throw new Error(data.error);
    document.getElementById('workspace-agent-models').innerHTML =
      (data.models || []).map(model => `<option value="${esc(model)}"></option>`).join('');
    document.getElementById('agent-model').focus();
    button.classList.add('success');
    setTimeout(() => button.classList.remove('success'), 1800);
  } catch (error) { alert(`获取失败: ${error.message}`); }
  finally { button.classList.remove('loading'); button.disabled = false; }
}

async function saveWorkspaceSettings() {
  const payload = {
    agent_llm: {
      model: document.getElementById('agent-model').value.trim(),
      max_tool_rounds: Math.max(1, Math.min(50, parseInt(document.getElementById('max-rounds').value) || 10)),
    },
    agent_permission: {full_access: document.getElementById('workspace-full-access').checked},
  };
  const response = await fetch('/api/settings', {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  });
  if (response.ok) {
    document.getElementById('agent-full-access').checked = payload.agent_permission.full_access;
    closeWorkspaceSettings();
  } else alert('保存失败');
}

// === 初始化 ===
loadConversations();
syncPermissionToggle();
const restoredConversationId = parseInt(localStorage.getItem('lfv.currentConversationId') || '0');
if (restoredConversationId) loadConversation(restoredConversationId, false);
