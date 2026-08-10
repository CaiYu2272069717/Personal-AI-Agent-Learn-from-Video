/**
 * Chat.js — Agent 流式对话前端
 * 支持 SSE 实时流式、思考过程展开/折叠、工具调用状态、Markdown 渲染
 */

/* ====== State ====== */
let conversationId = 0;
let currentRunId = null;
let isStreaming = false;
let currentReader = null;
let messages = [];  // {role, content}
let fullAccess = false;
let welcomeHtmlCache = '';  // 首页 welcome 缓存

/* ====== DOM refs ====== */
const $ = id => document.getElementById(id);

/* ====== Init ====== */
document.addEventListener('DOMContentLoaded', () => {
    // 缓存 welcome HTML（只在首次加载时保存，后续不会丢失）
    const welcomeEl = $('chat-welcome');
    if (welcomeEl) welcomeHtmlCache = welcomeEl.outerHTML;
    loadConversations();
    loadFullAccessState();
    // 工作空间弹窗中的 full-access 开关也绑定同步
    const wsToggle = $('workspace-full-access');
    if (wsToggle) {
        wsToggle.addEventListener('change', function() {
            setAgentFullAccess(this.checked);
        });
    }
});

function autoResize() {
    const el = $('chat-input');
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

/* ====== Slash Command Suggestions ====== */
let skillsCache = null;
let selectedSuggestionIdx = -1;

async function loadSkillsCache() {
    if (skillsCache) return skillsCache;
    try {
        const resp = await fetch('/api/extensions/skills');
        if (resp.ok) {
            skillsCache = await resp.json();
        }
    } catch(e) { console.error(e); }
    return skillsCache || [];
}

function handleInput() {
    autoResize();
    const ta = $('chat-input');
    if (ta) handleSlashSuggestion(ta.value);
}

async function handleSlashSuggestion(value) {
    const panel = $('skill-suggestions');
    if (!panel) return;
    // Only trigger when input starts with '/'
    if (!value.startsWith('/')) {
        panel.style.display = 'none';
        selectedSuggestionIdx = -1;
        return;
    }
    const query = value.slice(1).trim().toLowerCase();
    const skills = await loadSkillsCache();
    let filtered = skills;
    if (query) {
        filtered = skills.filter(s =>
            s.name.toLowerCase().includes(query) ||
            (s.description || '').toLowerCase().includes(query) ||
            (s.display_name || '').toLowerCase().includes(query)
        );
    }
    if (filtered.length === 0) {
        panel.style.display = 'none';
        selectedSuggestionIdx = -1;
        return;
    }
    const items = filtered.slice(0, 8).map((s, i) => `
        <div class="skill-suggestion-item${i === selectedSuggestionIdx ? ' active' : ''}" data-idx="${i}" data-name="${s.name}">
            <span class="skill-name">/${s.name}</span>
            <span class="skill-desc">${s.display_name || s.description || ''}</span>
            <span class="skill-kbd">Tab</span>
        </div>
    `).join('');
    panel.innerHTML = `<div class="skill-suggestions-header">Skills · 输入过滤 · ↑↓ 选择 · Tab 确认</div>${items}`;
    panel.style.display = 'block';
    panel.querySelectorAll('.skill-suggestion-item').forEach(item => {
        item.onclick = () => selectSkillSuggestion(item.dataset.name);
    });
}

function selectSkillSuggestion(name) {
    const ta = $('chat-input');
    ta.value = '/' + name + ' ';
    ta.focus();
    $('skill-suggestions').style.display = 'none';
    selectedSuggestionIdx = -1;
}

function handleSuggestionKeydown(e) {
    const panel = $('skill-suggestions');
    if (!panel || panel.style.display === 'none') return false;
    const items = panel.querySelectorAll('.skill-suggestion-item');
    if (items.length === 0) return false;
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectedSuggestionIdx = (selectedSuggestionIdx + 1) % items.length;
        updateSuggestionHighlight(items);
        return true;
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectedSuggestionIdx = (selectedSuggestionIdx - 1 + items.length) % items.length;
        updateSuggestionHighlight(items);
        return true;
    } else if (e.key === 'Tab' || (e.key === 'Enter' && selectedSuggestionIdx >= 0)) {
        e.preventDefault();
        if (selectedSuggestionIdx >= 0 && selectedSuggestionIdx < items.length) {
            selectSkillSuggestion(items[selectedSuggestionIdx].dataset.name);
        } else if (items.length > 0) {
            selectSkillSuggestion(items[0].dataset.name);
        }
        return true;
    } else if (e.key === 'Escape') {
        panel.style.display = 'none';
        selectedSuggestionIdx = -1;
        return true;
    }
    return false;
}

function updateSuggestionHighlight(items) {
    items.forEach((item, i) => {
        item.classList.toggle('active', i === selectedSuggestionIdx);
    });
}

/* ====== Send Message ====== */
function handleKey(e) {
    // Let suggestion panel handle keys first
    if (handleSuggestionKeydown(e)) return;
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

async function sendMessage() {
    const input = $('chat-input');
    const message = input.value.trim();
    if (!message || isStreaming) return;

    input.value = '';
    input.style.height = 'auto';
    // Close skill suggestions
    const sugPanel = $('skill-suggestions');
    if (sugPanel) sugPanel.style.display = 'none';

    // Hide welcome
    const welcome = $('chat-welcome');
    if (welcome) welcome.style.display = 'none';

    // Show user message (revert button added after backend confirms)
    const userMsgEl = appendUserMessage(message);
    messages.push({ role: 'user', content: message });

    // Use backend run system (persists conversation + messages)
    startAgentRun(message, userMsgEl);
}

/* ====== Agent Run System (persistent, event-based) ====== */
async function startAgentRun(message, userMsgEl) {
    isStreaming = true;
    toggleStreamUI(true);

    const msgEl = createAssistantBubble();
    const thinkingContainer = msgEl.querySelector('.msg-thinking');
    const thinkingContent = msgEl.querySelector('.msg-thinking-body');
    const contentEl = msgEl.querySelector('.message-content');
    const statusEl = msgEl.querySelector('.msg-status');

    let thinkingText = '';
    let contentText = '';

    try {
        // Step 1: Create run via backend (persists conversation + user message)
        const chatResp = await fetch('/api/agent/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                conversation_id: conversationId || 0,
            }),
        });
        if (!chatResp.ok) {
            const err = await chatResp.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${chatResp.status}`);
        }
        const runData = await chatResp.json();
        conversationId = runData.conversation_id;
        currentRunId = runData.run_id;

        // Add revert button to user message now that we have the ID
        if (userMsgEl && runData.user_message_id) {
            const body = userMsgEl.querySelector('.message-body');
            if (body) {
                const btn = document.createElement('button');
                btn.className = 'msg-revert-btn';
                btn.title = '回退到此轮之前';
                btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>';
                btn.onclick = (e) => { e.stopPropagation(); revertToTurn(runData.user_message_id, btn); };
                body.appendChild(btn);
            }
        }

        // Step 2: Connect to SSE event stream
        const streamResp = await fetch(`/api/agent/runs/${runData.run_id}/stream`);
        if (!streamResp.ok) throw new Error(`Stream HTTP ${streamResp.status}`);

        const reader = streamResp.body.getReader();
        const decoder = new TextDecoder();
        currentReader = reader;
        let buffer = '';

        function pump() {
            reader.read().then(({ done, value }) => {
                if (done) {
                    finishStream(contentText, msgEl);
                    loadConversations();
                    return;
                }

                buffer += decoder.decode(value, { stream: true });
                const { events, remaining } = parseSSE(buffer);
                buffer = remaining;

                for (const evt of events) {
                    const data = evt.data || {};
                    switch (evt.type || data.type) {
                        case 'thinking':
                            if (data.content) {
                                thinkingContainer.style.display = 'block';
                                thinkingText += data.content;
                                thinkingContent.textContent = thinkingText;
                                thinkingContent.scrollTop = thinkingContent.scrollHeight;
                            }
                            break;

                        case 'thinking_done':
                            thinkingContainer.classList.add('collapsed');
                            break;

                        case 'text':
                        case 'content':
                            contentText += (data.content || '');
                            contentEl.innerHTML = renderMarkdown(contentText);
                            break;

                        case 'tool_call':
                            statusEl.style.display = 'block';
                            statusEl.innerHTML = `<span class="msg-tool-badge">⚡ ${escapeHtml(data.tool || data.content || '工具调用')}</span>`;
                            break;

                        case 'tool_result':
                            statusEl.innerHTML = `<span class="msg-tool-badge done">✓ ${escapeHtml(data.tool || data.content || '完成')}</span>`;
                            break;

                        case 'status':
                            statusEl.style.display = 'block';
                            statusEl.innerHTML = `<span class="msg-tool-badge">${escapeHtml(data.label || data.content || '处理中')}</span>`;
                            break;

                        case 'confirm_needed':
                            showConfirmDialog(data);
                            break;

                        case 'done':
                            if (!contentText && data.content) {
                                contentText = data.content;
                                contentEl.innerHTML = renderMarkdown(contentText);
                            }
                            finishStream(contentText, msgEl);
                            loadConversations();
                            return;

                        case 'error':
                            contentEl.innerHTML += `<span class="msg-error">❌ ${escapeHtml(data.content || '未知错误')}</span>`;
                            finishStream(contentText, msgEl);
                            loadConversations();
                            return;

                        case 'cancelled':
                            contentEl.innerHTML += `<span class="msg-error">⏹ ${escapeHtml(data.content || '已停止')}</span>`;
                            finishStream(contentText, msgEl);
                            return;
                    }
                }

                scrollToBottom();
                if (isStreaming) pump();
            }).catch(err => {
                console.error('Stream read error:', err);
                contentEl.innerHTML += `<br><span class="msg-error">连接中断: ${err.message}</span>`;
                finishStream(contentText, msgEl);
            });
        }

        pump();
    } catch (err) {
        console.error('Agent run error:', err);
        contentEl.innerHTML = `<span class="msg-error">请求失败: ${escapeHtml(err.message)}</span>`;
        finishStream('', msgEl);
    }
}

function showConfirmDialog(data) {
    const panel = $('chat-confirm');
    if (!panel) return;
    window._pendingCallId = data.call_id || '';
    const body = $('confirm-body');
    if (body) {
        body.innerHTML = `
            <p><strong>工具请求确认</strong></p>
            <p>工具: <code>${escapeHtml(data.tool || '')}</code></p>
            <pre style="max-height:200px;overflow:auto;font-size:12px;background:rgba(0,0,0,.03);padding:8px;border-radius:3px">${escapeHtml(JSON.stringify(data.args || data.arguments || {}, null, 2))}</pre>
        `;
    }
    panel.style.display = 'flex';
}

async function handleConfirm(approved) {
    const panel = $('chat-confirm');
    if (panel) panel.style.display = 'none';
    const callId = window._pendingCallId || '';
    if (!callId) { console.error('No pending call_id for confirm'); return; }
    try {
        const resp = await fetch('/api/agent/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                conversation_id: conversationId || 0,
                call_id: callId,
                approved,
            }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            console.error('confirm failed:', err.detail || resp.statusText);
        }
    } catch(e) { console.error('confirm error:', e); }
    window._pendingCallId = '';
}

/* ====== SSE Parser ====== */
function parseSSE(buffer) {
    const events = [];
    const blocks = buffer.split('\n\n');
    let remaining = '';

    for (let i = 0; i < blocks.length; i++) {
        const block = blocks[i];
        // Last block might be incomplete
        if (i === blocks.length - 1 && !buffer.endsWith('\n\n')) {
            remaining = block;
            break;
        }
        if (!block.trim()) continue;

        let eventType = null;
        let dataStr = null;

        for (const line of block.split('\n')) {
            if (line.startsWith('event: ')) {
                eventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
                dataStr = line.slice(6);
            } else if (line.startsWith('id: ') || line.startsWith(':')) {
                // id field or comment/heartbeat — skip
            }
        }

        if (dataStr !== null) {
            try {
                const data = JSON.parse(dataStr);
                // Type comes from 'event:' line or from inside JSON data
                const type = eventType || data.type || 'unknown';
                events.push({ type, data });
            } catch (e) {
                // Malformed JSON — skip this block
            }
        }
    }

    return { events, remaining };
}

/* ====== DOM Helpers ====== */
function appendUserMessage(text, opts = {}) {
    const container = $('chat-messages');
    const div = document.createElement('div');
    div.className = 'message user';
    const revertBtn = (opts.canRevert && opts.id)
        ? `<button class="msg-revert-btn" onclick="event.stopPropagation();revertToTurn(${opts.id}, this)" data-text="${escapeHtml(text)}" title="回退到此轮之前"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg></button>`
        : '';
    div.innerHTML = `
        <div class="message-avatar user-avatar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        </div>
        <div class="message-body">
            <div class="message-content">${escapeHtml(text)}</div>
            ${revertBtn}
        </div>
    `;
    container.appendChild(div);
    scrollToBottom();
    return div;
}

function createAssistantBubble() {
    const container = $('chat-messages');
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = `
        <div class="message-avatar assistant-avatar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        </div>
        <div class="message-body">
            <div class="msg-thinking" style="display:none">
                <div class="msg-thinking-header" onclick="this.parentElement.classList.toggle('collapsed')">
                    <span class="msg-thinking-icon">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>
                    </span>
                    <span class="msg-thinking-label">思考过程</span>
                    <span class="msg-thinking-toggle">▼</span>
                </div>
                <div class="msg-thinking-body"></div>
            </div>
            <div class="msg-status" style="display:none"></div>
            <div class="message-content"><span class="typing-indicator"><span></span><span></span><span></span></span></div>
        </div>
    `;
    container.appendChild(div);
    scrollToBottom();
    return div;
}

function finishStream(text, el) {
    isStreaming = false;
    currentReader = null;
    toggleStreamUI(false);

    // Remove typing indicator if content is empty
    const contentEl = el.querySelector('.message-content');
    const indicator = contentEl.querySelector('.typing-indicator');
    if (indicator) indicator.remove();

    // Hide status after a moment
    const statusEl = el.querySelector('.msg-status');
    if (statusEl) setTimeout(() => { statusEl.style.display = 'none'; }, 3000);

    // Save to history
    if (text) {
        messages.push({ role: 'assistant', content: text });
    }

    // Highlight code blocks
    if (typeof hljs !== 'undefined') {
        el.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
    }
}

function toggleStreamUI(streaming) {
    const sendBtn = $('send-btn');
    const stopBtn = $('stop-btn');
    if (sendBtn) sendBtn.style.display = streaming ? 'none' : 'flex';
    if (stopBtn) stopBtn.style.display = streaming ? 'flex' : 'none';
}

async function stopGeneration() {
    if (currentReader) {
        currentReader.cancel();
        currentReader = null;
    }
    // Cancel backend run
    if (currentRunId) {
        try {
            await fetch(`/api/agent/runs/${currentRunId}/cancel`, { method: 'POST' });
        } catch(e) { console.error('cancel error:', e); }
        currentRunId = null;
    }
    isStreaming = false;
    toggleStreamUI(false);
}

function scrollToBottom() {
    const container = $('chat-messages');
    if (container) container.scrollTop = container.scrollHeight;
}

/* ====== Markdown Renderer ====== */
function renderMarkdown(text) {
    if (!text) return '';
    // Use marked.js if available
    if (typeof marked !== 'undefined') {
        marked.setOptions({
            highlight: function(code, lang) {
                if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                return code;
            },
            breaks: true
        });
        return marked.parse(text);
    }
    // Fallback: basic markdown
    let html = escapeHtml(text);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/\n/g, '<br>');
    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/* ====== History Panel ====== */
function toggleHistory() {
    const panel = $('history-panel');
    if (panel) panel.classList.toggle('open');
}

async function loadConversations() {
    try {
        const resp = await fetch('/api/agent/conversations');
        if (!resp.ok) return;
        const data = await resp.json();
        const list = $('history-list');
        if (!list) return;
        list.innerHTML = '';
        const convs = data.conversations || [];
        if (!convs.length) {
            list.innerHTML = '<div class="history-empty">暂无历史会话</div>';
            return;
        }
        for (const conv of convs) {
            const item = document.createElement('div');
            item.className = 'history-item' + (conv.id === conversationId ? ' active' : '');
            const isRunning = conv.run_status === 'running' || conv.run_status === 'queued';
            item.innerHTML = `
                <div class="history-item-copy">
                    <div class="history-item-title">${isRunning ? '<span class="history-running-dot"></span>' : ''}${escapeHtml(conv.title || '新对话')}</div>
                </div>
                <button class="history-delete-btn" onclick="event.stopPropagation();deleteConversation(${conv.id}, this)" title="删除">&times;</button>
            `;
            item.onclick = () => loadConversation(conv.id, { closeHistory: true });
            list.appendChild(item);
        }
    } catch(e) { console.error('loadConversations error:', e); }
}

async function loadConversation(id, opts = {}) {
    conversationId = id;
    messages = [];
    currentRunId = null;
    const container = $('chat-messages');
    container.innerHTML = '';

    try {
        const resp = await fetch(`/api/agent/conversations/${id}/messages`);
        if (!resp.ok) return;
        const data = await resp.json();
        for (const msg of (data.messages || [])) {
            if (msg.role === 'user') {
                appendUserMessage(msg.content, { id: msg.id, canRevert: !!msg.can_revert });
                messages.push({ role: 'user', content: msg.content });
            } else if (msg.role === 'assistant') {
                appendAssistantMessage(msg.content);
                messages.push({ role: 'assistant', content: msg.content });
            }
        }
    } catch(e) { console.error('loadConversation error:', e); }

    // Check for active run and reconnect
    try {
        const runResp = await fetch(`/api/agent/conversations/${id}/active-run`);
        if (runResp.ok) {
            const runData = await runResp.json();
            if (runData.run) {
                currentRunId = runData.run.id;
                reconnectToRun(runData.run.id);
            }
        }
    } catch(e) { /* no active run */ }

    loadConversations();
    if (opts.closeHistory) toggleHistory();
}

async function reconnectToRun(runId) {
    isStreaming = true;
    toggleStreamUI(true);
    const msgEl = createAssistantBubble();
    const thinkingContainer = msgEl.querySelector('.msg-thinking');
    const thinkingContent = msgEl.querySelector('.msg-thinking-body');
    const contentEl = msgEl.querySelector('.message-content');
    const statusEl = msgEl.querySelector('.msg-status');
    let thinkingText = '';
    let contentText = '';

    try {
        const streamResp = await fetch(`/api/agent/runs/${runId}/stream`);
        if (!streamResp.ok) { finishStream('', msgEl); return; }
        const reader = streamResp.body.getReader();
        const decoder = new TextDecoder();
        currentReader = reader;
        let buffer = '';

        function pump() {
            reader.read().then(({ done, value }) => {
                if (done) { finishStream(contentText, msgEl); loadConversations(); return; }
                buffer += decoder.decode(value, { stream: true });
                const { events, remaining } = parseSSE(buffer);
                buffer = remaining;
                for (const evt of events) {
                    const data = evt.data || {};
                    switch (evt.type || data.type) {
                        case 'thinking':
                            if (data.content) { thinkingContainer.style.display = 'block'; thinkingText += data.content; thinkingContent.textContent = thinkingText; }
                            break;
                        case 'thinking_done': thinkingContainer.classList.add('collapsed'); break;
                        case 'text': case 'content':
                            contentText += (data.content || ''); contentEl.innerHTML = renderMarkdown(contentText); break;
                        case 'tool_call': statusEl.style.display = 'block'; statusEl.innerHTML = `<span class="msg-tool-badge">⚡ ${escapeHtml(data.tool || data.content || '工具调用')}</span>`; break;
                        case 'tool_result': statusEl.innerHTML = `<span class="msg-tool-badge done">✓ ${escapeHtml(data.tool || data.content || '完成')}</span>`; break;
                        case 'done': if (!contentText && data.content) { contentText = data.content; contentEl.innerHTML = renderMarkdown(contentText); } finishStream(contentText, msgEl); loadConversations(); return;
                        case 'error': contentEl.innerHTML += `<span class="msg-error">❌ ${escapeHtml(data.content || '')}</span>`; finishStream(contentText, msgEl); return;
                        case 'cancelled': contentEl.innerHTML += `<span class="msg-error">⏹ ${escapeHtml(data.content || '已停止')}</span>`; finishStream(contentText, msgEl); return;
                    }
                }
                scrollToBottom();
                if (isStreaming) pump();
            }).catch(err => { finishStream(contentText, msgEl); });
        }
        pump();
    } catch(e) { finishStream('', msgEl); }
}

function appendAssistantMessage(text) {
    const container = $('chat-messages');
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = `
        <div class="message-avatar assistant-avatar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        </div>
        <div class="message-body">
            <div class="message-content">${renderMarkdown(text)}</div>
        </div>
    `;
    container.appendChild(div);
    if (typeof hljs !== 'undefined') {
        div.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
    }
}

async function newConversation() {
    conversationId = 0;
    currentRunId = null;
    messages = [];
    const container = $('chat-messages');
    container.innerHTML = welcomeHtmlCache || '';
    loadConversations();
}

function deleteConversation(id, btn) {
    // Double-click to delete: first click shows trash icon, second click deletes
    if (btn && !btn.dataset.armed) {
        btn.dataset.armed = '1';
        btn.innerHTML = '🗑';
        btn.title = '再次点击确认删除';
        setTimeout(() => {
            if (btn) { btn.dataset.armed = ''; btn.innerHTML = '&times;'; btn.title = '删除'; }
        }, 3000);
        return;
    }
    fetch(`/api/agent/conversations/${id}`, { method: 'DELETE' })
        .then(() => { if (id === conversationId) newConversation(); else loadConversations(); })
        .catch(e => console.error(e));
}

async function revertToTurn(userMessageId, btnEl) {
    if (!conversationId) return;
    if (isStreaming) {
        alert('Agent 正在运行，请先停止后再回退。');
        return;
    }
    // Find the original text from the message
    const msgBody = btnEl ? btnEl.closest('.message-body') : null;
    const originalText = msgBody ? msgBody.querySelector('.message-content').textContent : '';

    try {
        const resp = await fetch('/api/agent/revert', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                conversation_id: conversationId,
                user_message_id: userMessageId,
            }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert('回退失败: ' + (err.detail || resp.statusText));
            return;
        }
        // Reload messages
        await loadConversation(conversationId);
        // Fill input with original text for easy re-editing
        if (originalText) {
            const input = $('chat-input');
            if (input) { input.value = originalText; input.focus(); autoResize(); }
        }
    } catch(e) {
        console.error('revert error:', e);
        alert('回退请求失败: ' + e.message);
    }
}

function exportChat() {
    const lines = messages.map(m => `${m.role === 'user' ? '用户' : 'Agent'}: ${m.content}`).join('\n\n');
    const blob = new Blob([lines], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `chat-${Date.now()}.txt`;
    a.click();
}

/* ====== Workspace Settings ====== */
async function openWorkspaceSettings() {
    $('workspace-modal').style.display = 'flex';
    // 从设置加载当前值
    try {
        const resp = await fetch('/api/settings');
        if (!resp.ok) return;
        const data = await resp.json();
        const agentLlm = data.agent_llm || {};
        const perm = data.agent_permission || {};
        const modelInput = $('agent-model');
        const roundsInput = $('max-rounds');
        const wsAccess = $('workspace-full-access');
        if (modelInput) modelInput.value = agentLlm.model || '';
        if (roundsInput) roundsInput.value = agentLlm.max_tool_rounds || 10;
        if (wsAccess) wsAccess.checked = !!perm.full_access;
    } catch(e) { console.error('加载工作空间设置失败:', e); }
}
function closeWorkspaceSettings() {
    $('workspace-modal').style.display = 'none';
}

async function fetchWorkspaceModels(btn) {
    btn.disabled = true;
    try {
        // 从当前设置获取 agent_llm 的 base_url 和 api_key
        const settingsResp = await fetch('/api/settings');
        if (!settingsResp.ok) { btn.disabled = false; return; }
        const settings = await settingsResp.json();
        const agentLlm = settings.agent_llm || {};
        const baseUrl = agentLlm.base_url || '';
        const apiKey = agentLlm.api_key || '';
        
        const resp = await fetch('/api/settings/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                base_url: baseUrl,
                api_key: apiKey,
                section: 'agent_llm'
            })
        });
        if (!resp.ok) { btn.disabled = false; return; }
        const data = await resp.json();
        if (data.error) {
            console.warn('获取模型列表:', data.error);
        }
        const datalist = $('workspace-agent-models');
        if (datalist) {
            datalist.innerHTML = (data.models || []).map(m => `<option value="${m}">`).join('');
        }
    } catch(e) { console.error(e); }
    btn.disabled = false;
}

async function saveWorkspaceSettings() {
    const modelInput = $('agent-model');
    const roundsInput = $('max-rounds');
    const wsAccess = $('workspace-full-access');
    
    const payload = {};
    
    // 保存模型和轮数
    const agentLlm = {};
    if (modelInput && modelInput.value) agentLlm.model = modelInput.value;
    if (roundsInput && roundsInput.value) agentLlm.max_tool_rounds = parseInt(roundsInput.value) || 10;
    if (Object.keys(agentLlm).length) payload.agent_llm = agentLlm;
    
    // 保存完全访问
    if (wsAccess) {
        payload.agent_permission = { full_access: wsAccess.checked };
        fullAccess = wsAccess.checked;
        const inputToggle = $('agent-full-access');
        if (inputToggle) inputToggle.checked = wsAccess.checked;
    }
    
    try {
        const resp = await fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (wsAccess && wsAccess.checked && resp.ok) {
            // 工作空间设置中开启完全访问后，关闭可能存在的确认框
            const panel = $('chat-confirm');
            if (panel) panel.style.display = 'none';
            window._pendingCallId = '';
        }
    } catch(e) { console.error('保存设置失败:', e); }

    closeWorkspaceSettings();
}

/* ====== Full Access Toggle ====== */
function setAgentFullAccess(checked) {
    fullAccess = checked;
    // 同步到设置
    fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_permission: { full_access: checked } })
    }).then(resp => {
        if (checked && resp.ok) {
            // 开启完全访问后，后端会自动批准当前待确认的调用；关闭确认框
            const panel = $('chat-confirm');
            if (panel) panel.style.display = 'none';
            window._pendingCallId = '';
        }
    }).catch(e => console.error('同步完全访问设置失败:', e));
    // 同步两个开关
    const inputToggle = $('agent-full-access');
    const wsToggle = $('workspace-full-access');
    if (inputToggle) inputToggle.checked = checked;
    if (wsToggle) wsToggle.checked = checked;
}

async function loadFullAccessState() {
    try {
        const resp = await fetch('/api/settings');
        if (!resp.ok) return;
        const data = await resp.json();
        const fa = data.agent_permission && data.agent_permission.full_access;
        fullAccess = !!fa;
        const inputToggle = $('agent-full-access');
        const wsToggle = $('workspace-full-access');
        if (inputToggle) inputToggle.checked = fullAccess;
        if (wsToggle) wsToggle.checked = fullAccess;
    } catch(e) { /* ignore */ }
}

/* ====== File Upload (stub) ====== */
function handleFileUpload(event) {
    const files = event.target.files;
    const list = $('file-list');
    if (!list) return;
    list.innerHTML = '';
    for (const f of files) {
        const tag = document.createElement('span');
        tag.className = 'file-tag';
        tag.textContent = f.name;
        list.appendChild(tag);
    }
}

