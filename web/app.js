// ============================================================
// mini-agent WebUI 前端逻辑
// ============================================================

const $ = (id) => document.getElementById(id);
const messagesEl = () => $('messages');
let sending = false;
let stopped = false;                    // 用户主动停止标志
let currentAbortController = null;      // 当前流式请求的 AbortController
let pendingFiles = [];                  // 待上传的文件列表 [{file, file_id?, status: 'pending'|'uploading'|'done'|'error'}]

// 发送 / 停止按钮图标
const SEND_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
const STOP_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';

// ------------------------------------------------------------
// 设置抽屉
// ------------------------------------------------------------
function openSettings() {
    $('settings-overlay').hidden = false;
    $('settings-drawer').hidden = false;
    loadSettings();
    document.addEventListener('keydown', onEscClose);
}
function closeSettings() {
    $('settings-overlay').hidden = true;
    $('settings-drawer').hidden = true;
    document.removeEventListener('keydown', onEscClose);
}
function onEscClose(e) {
    if (e.key === 'Escape') closeSettings();
}

// ------------------------------------------------------------
// 对话
// ------------------------------------------------------------
function useExample(btn) {
    const input = $('chat-input');
    input.value = btn.textContent;
    autoResize(input);
    updateSendBtn();
    input.focus();
}

// ------------------------------------------------------------
// 文件上传
// ------------------------------------------------------------
function onAttachClick() {
    $('file-input').click();
}

async function onFileSelected(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';  // 清空，允许重复选同一文件
    for (const file of files) {
        pendingFiles.push({ file, status: 'pending' });
    }
    renderFileChips();
    // 逐个上传
    for (const item of pendingFiles) {
        if (item.status !== 'pending') continue;
        item.status = 'uploading';
        renderFileChips();
        try {
            const formData = new FormData();
            formData.append('file', item.file);
            const resp = await fetch('/api/upload', { method: 'POST', body: formData });
            const data = await resp.json();
            if (data.ok) {
                item.file_id = data.file_id;
                item.status = 'done';
            } else {
                item.status = 'error';
                item.error = data.error || '上传失败';
            }
        } catch (e) {
            item.status = 'error';
            item.error = e.message;
        }
        renderFileChips();
    }
    updateSendBtn();
}

function removeFile(index) {
    pendingFiles.splice(index, 1);
    renderFileChips();
    updateSendBtn();
}

function renderFileChips() {
    const container = $('file-chips');
    container.innerHTML = '';
    pendingFiles.forEach((item, i) => {
        const chip = document.createElement('div');
        chip.className = 'file-chip ' + item.status;
        const sizeStr = formatFileSize(item.file.size);
        let icon = '';
        let label = item.file.name;
        let badge = '';
        if (item.status === 'uploading') {
            badge = '<span class="file-chip-badge">上传中…</span>';
        } else if (item.status === 'done') {
            badge = `<span class="file-chip-badge done">${sizeStr}</span>`;
        } else if (item.status === 'error') {
            badge = `<span class="file-chip-badge error">${escapeHtml(item.error || '失败')}</span>`;
        }
        chip.innerHTML = `
            <svg class="file-chip-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span class="file-chip-name">${escapeHtml(label)}</span>
            ${badge}
            <button class="file-chip-remove" onclick="removeFile(${i})" title="移除">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>`;
        container.appendChild(chip);
    });
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

function getUploadedFileIds() {
    return pendingFiles.filter(f => f.status === 'done' && f.file_id).map(f => f.file_id);
}

function allFilesUploaded() {
    return pendingFiles.every(f => f.status === 'done' || f.status === 'error');
}

async function sendChat() {
    if (sending) return;
    const input = $('chat-input');
    const text = input.value.trim();
    // 有文本或有已上传文件均可发送
    const fileIds = getUploadedFileIds();
    if (!text && fileIds.length === 0) return;
    // 等待所有文件上传完成
    if (!allFilesUploaded()) {
        showError('文件正在上传中，请稍候…');
        return;
    }

    sending = true;
    stopped = false;
    currentAbortController = new AbortController();
    setSending(true);
    input.value = '';
    autoResize(input);
    updateSendBtn();

    // 移除欢迎屏
    const welcome = messagesEl().querySelector('.welcome');
    if (welcome) welcome.remove();

    // 收集文件信息用于展示
    const attachedFiles = pendingFiles
        .filter(f => f.status === 'done' && f.file_id)
        .map(f => ({ name: f.file.name, size: f.file.size, file_id: f.file_id }));
    // 清空待发文件
    pendingFiles = [];
    renderFileChips();

    appendUserMessage(text, attachedFiles);
    setStatus('思考中', true);
    hideError();

    // 流式渲染上下文：维护当前思考块 / 回复气泡 / 工具块的 DOM 指针
    const ctx = {
        reasoningEl: null,
        reasoningBody: null,
        reasoningHasContent: false,
        replyRow: null,
        replyBubble: null,
        replyRaw: '',
        replyHasContent: false,
    };

    try {
        const resp = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, file_ids: fileIds }),
            signal: currentAbortController.signal,
        });

        if (resp.status === 409) {
            const data = await resp.json();
            showError(data.error || '当前已有对话在处理中');
            return;
        }
        if (!resp.ok || !resp.body) {
            let msg = '请求失败: ' + resp.status;
            try {
                const data = await resp.json();
                msg = data.error || data.detail || msg;
            } catch (_) { /* ignore */ }
            showError(msg);
            return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            // SSE 事件以空行分隔
            let sep;
            while ((sep = buffer.indexOf('\n\n')) !== -1) {
                const rawEvent = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                // 提取 data: 行（可能跨多行）
                const dataLine = rawEvent
                    .split('\n')
                    .filter((l) => l.startsWith('data:'))
                    .map((l) => l.slice(5).replace(/^ /, ''))
                    .join('');
                if (!dataLine) continue;
                let evt;
                try {
                    evt = JSON.parse(dataLine);
                } catch (_) {
                    continue;
                }
                handleStreamEvent(evt, ctx);
            }
        }
    } catch (e) {
        // 用户主动停止导致的 AbortError 不报错
        if (e.name === 'AbortError' || stopped) {
            // 静默处理
        } else {
            showError('请求失败: ' + e.message);
        }
    } finally {
        // 收尾：折叠思考块、移除流式状态
        finalizeReasoning(ctx);
        finalizeReply(ctx);
        // 用户主动停止时追加提示
        if (stopped && !ctx.replyHasContent) {
            appendAgentMessage('（已停止）');
        }
        setStatus('', false);
        sending = false;
        stopped = false;
        currentAbortController = null;
        setSending(false);
        $('chat-input').focus();
    }
}

// ------------------------------------------------------------
// 停止对话
// ------------------------------------------------------------
async function stopChat() {
    if (!sending || stopped) return;
    stopped = true;
    setStatus('正在停止…', true);

    // 1. 通知后端停止（设标志 + 终止子进程）
    try {
        await fetch('/api/chat/stop', { method: 'POST' });
    } catch (e) { /* ignore */ }

    // 2. 中止前端流式读取
    if (currentAbortController) {
        currentAbortController.abort();
    }
}

// 发送按钮点击分发：发送中 → 停止，空闲 → 发送
function onSendClick() {
    if (sending) {
        stopChat();
    } else {
        sendChat();
    }
}

// ------------------------------------------------------------
// 流式事件分发
// ------------------------------------------------------------
function handleStreamEvent(evt, ctx) {
    switch (evt.type) {
        case 'reasoning_delta':
            ensureReasoningBlock(ctx);
            ctx.reasoningBody.textContent += evt.content;
            ctx.reasoningHasContent = true;
            scrollToBottom();
            break;
        case 'reply_delta':
            ensureReplyBubble(ctx);
            ctx.replyRaw += evt.content;
            ctx.replyBubble.innerHTML = renderMarkdown(ctx.replyRaw);
            ctx.replyHasContent = true;
            scrollToBottom();
            break;
        case 'tool_call':
            // 工具调用开始前，定稿当前回复气泡和思考块
            finalizeReasoning(ctx);
            finalizeReply(ctx);
            appendToolCallBlock(evt.id, evt.name, evt.args);
            break;
        case 'tool_result':
            appendToolResult(evt.id, evt.result);
            break;
        case 'file':
            finalizeReasoning(ctx);
            finalizeReply(ctx);
            appendFileCard(evt);
            break;
        case 'done':
            finalizeReasoning(ctx);
            finalizeReply(ctx);
            if (evt.error) {
                showError(evt.error);
            } else if (evt.reply && !ctx.replyHasContent) {
                // finish 工具返回的 summary，且前端未流式渲染过 → 直接显示
                appendAgentMessage(evt.reply);
            } else if (!evt.reply && !ctx.replyHasContent) {
                appendAgentMessage('（未能生成回复）');
            }
            break;
    }
}

// ------------------------------------------------------------
// 流式 DOM 构造辅助
// ------------------------------------------------------------
function ensureReasoningBlock(ctx) {
    if (ctx.reasoningEl) return;
    const el = document.createElement('div');
    el.className = 'step-block step-reasoning streaming';
    el.innerHTML =
        `<div class="step-head" onclick="toggleStep(this)">
            <span class="step-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.663 17h4.673M12 3v1m6.364 1.636-.707.707M21 12h-1M4 12H3m3.343-5.657-.707-.707m2.828 9.9a5 5 0 1 1 7.072 0l-.548.547A3.374 3.374 0 0 0 14 18.469V19a2 2 0 1 1-4 0v-.531c0-.895-.356-1.754-.988-2.386z"/></svg>
            </span>
            <span class="step-title">思考过程</span>
            <span class="step-status">思考中…</span>
            <span class="step-toggle" hidden>点击展开</span>
        </div>
        <div class="step-body"></div>`;
    messagesEl().appendChild(el);
    ctx.reasoningEl = el;
    ctx.reasoningBody = el.querySelector('.step-body');
    scrollToBottom();
}

function finalizeReasoning(ctx) {
    if (!ctx.reasoningEl) return;
    ctx.reasoningEl.classList.remove('streaming');
    const status = ctx.reasoningEl.querySelector('.step-status');
    if (status) status.remove();
    const toggle = ctx.reasoningEl.querySelector('.step-toggle');
    if (toggle) toggle.hidden = false;
    // 思考完成后自动折叠（内容多时不占屏），用户可点开查看
    if (ctx.reasoningHasContent) {
        ctx.reasoningEl.classList.add('collapsed');
    }
    ctx.reasoningEl = null;
    ctx.reasoningBody = null;
}

function ensureReplyBubble(ctx) {
    if (ctx.replyRow) return;
    const row = document.createElement('div');
    row.className = 'msg-row agent streaming';
    row.innerHTML =
        `<div class="msg-avatar">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>
        </div>
        <div class="msg-bubble-wrap">
            <div class="msg-name">mini-agent</div>
            <div class="msg-bubble markdown-body"></div>
        </div>`;
    messagesEl().appendChild(row);
    ctx.replyRow = row;
    ctx.replyBubble = row.querySelector('.msg-bubble');
    scrollToBottom();
}

function finalizeReply(ctx) {
    if (!ctx.replyRow) return;
    ctx.replyRow.classList.remove('streaming');
    // 定稿后对代码块统一高亮（流式过程未高亮）
    if (ctx.replyBubble) highlightCode(ctx.replyBubble);
    ctx.replyRow = null;
    ctx.replyBubble = null;
}

function appendToolCallBlock(id, name, args) {
    const el = document.createElement('div');
    // 创建时展开（让用户看到执行中的参数），执行完成后由 appendToolResult 折叠
    el.className = 'step-block step-tool tool-running';
    el.dataset.toolId = id || '';
    el.innerHTML =
        `<div class="step-head" onclick="toggleStep(this)">
            <span class="step-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
            </span>
            <span class="step-title">工具调用 · ${escapeHtml(name)}</span>
            <span class="step-status">执行中…</span>
            <span class="step-toggle">点击折叠</span>
        </div>
        <div class="step-body">
            <div class="step-row">
                <span class="step-tag">参数</span>
                <span class="step-value tool-args">${escapeHtml(formatArgsCompact(args))}</span>
            </div>
            <div class="step-row">
                <span class="step-tag">结果</span>
                <span class="step-value tool-result">执行中…</span>
            </div>
        </div>`;
    messagesEl().appendChild(el);
    scrollToBottom();
}

function appendToolResult(id, result) {
    // 工具按顺序执行，取最后一个 "执行中" 的工具块填充结果
    const blocks = messagesEl().querySelectorAll('.step-tool.tool-running');
    const block = blocks[blocks.length - 1];
    if (!block) return;
    block.classList.remove('tool-running');
    // 填充结果
    const resultEl = block.querySelector('.tool-result');
    if (resultEl) resultEl.textContent = formatResultCompact(result);
    // 状态改为已完成
    const status = block.querySelector('.step-status');
    if (status) {
        status.textContent = '已完成';
        status.classList.add('done');
    }
    const toggle = block.querySelector('.step-toggle');
    if (toggle) toggle.textContent = '点击展开';
    // 执行完成后自动折叠
    block.classList.add('collapsed');
    scrollToBottom();
}

// 把工具参数格式化为一行紧凑文本（避免 JSON 多行占空间）
function formatArgsCompact(args) {
    if (args == null) return '(无)';
    if (typeof args === 'string') return args;
    const parts = [];
    for (const [k, v] of Object.entries(args)) {
        let sv = typeof v === 'string' ? v : JSON.stringify(v);
        if (sv.length > 120) sv = sv.slice(0, 120) + '…';
        parts.push(`${k}: ${sv}`);
    }
    return parts.join('   ·   ') || '(无)';
}

// 把工具结果格式化为一行紧凑文本，提取关键信息
function formatResultCompact(result) {
    if (!result) return '(无输出)';
    try {
        const obj = JSON.parse(result);
        if (obj.success === false) {
            const reason = obj.stderr || obj.error || '未知错误';
            return '失败 · ' + (reason.length > 120 ? reason.slice(0, 120) + '…' : reason);
        }
        if (obj.summary) return obj.summary;
        if (obj.stdout) {
            const s = obj.stdout.trim();
            return s.length > 120 ? s.slice(0, 120) + '…' : s;
        }
        if (obj.stderr) return obj.stderr;
        return JSON.stringify(obj);
    } catch {
        return result.length > 120 ? result.slice(0, 120) + '…' : result;
    }
}

function setSending(v) {
    const btn = $('send-btn');
    const input = $('chat-input');
    const attachBtn = $('attach-btn');
    if (v) {
        // 发送中：按钮变停止按钮（不禁用，可点击停止）
        btn.innerHTML = STOP_ICON;
        btn.classList.add('stop-mode');
        btn.disabled = false;
        input.disabled = true;
        if (attachBtn) attachBtn.disabled = true;
    } else {
        // 空闲：恢复发送按钮
        btn.innerHTML = SEND_ICON;
        btn.classList.remove('stop-mode');
        input.disabled = false;
        if (attachBtn) attachBtn.disabled = false;
        updateSendBtn();
    }
}

function appendUserMessage(text, files) {
    const row = document.createElement('div');
    row.className = 'msg-row user';
    let filesHtml = '';
    if (files && files.length > 0) {
        filesHtml = '<div class="msg-attachments">';
        for (const f of files) {
            filesHtml += `
                <div class="msg-attachment">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    <span class="msg-attachment-name">${escapeHtml(f.name)}</span>
                    <span class="msg-attachment-size">${formatFileSize(f.size)}</span>
                </div>`;
        }
        filesHtml += '</div>';
    }
    const textHtml = text ? `<div class="msg-bubble">${escapeHtml(text)}</div>` : '';
    row.innerHTML =
        `<div class="msg-avatar">你</div>
         <div class="msg-bubble-wrap">
            <div class="msg-name">你</div>
            ${textHtml}
            ${filesHtml}
         </div>`;
    messagesEl().appendChild(row);
    scrollToBottom();
}

function appendAgentMessage(text) {
    const row = document.createElement('div');
    row.className = 'msg-row agent';
    row.innerHTML =
        `<div class="msg-avatar">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>
        </div>
         <div class="msg-bubble-wrap">
            <div class="msg-name">mini-agent</div>
            <div class="msg-bubble markdown-body">${renderMarkdown(text)}</div>
         </div>`;
    highlightCode(row.querySelector('.msg-bubble'));
    messagesEl().appendChild(row);
    scrollToBottom();
}

function appendFileCard(evt) {
    const row = document.createElement('div');
    row.className = 'msg-row agent';
    const sizeStr = formatFileSize(evt.size || 0);
    const desc = evt.description ? `<div class="file-card-desc">${escapeHtml(evt.description)}</div>` : '';
    row.innerHTML =
        `<div class="msg-avatar">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>
        </div>
         <div class="msg-bubble-wrap">
            <div class="msg-name">mini-agent</div>
            <a class="file-card" href="/api/files/${encodeURIComponent(evt.file_id)}" download="${escapeHtml(evt.filename)}">
                <div class="file-card-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 12 15 15"/></svg>
                </div>
                <div class="file-card-info">
                    <div class="file-card-name">${escapeHtml(evt.filename)}</div>
                    ${desc}
                    <div class="file-card-meta">${sizeStr} · 点击下载</div>
                </div>
                <div class="file-card-download">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                </div>
            </a>
         </div>`;
    messagesEl().appendChild(row);
    scrollToBottom();
}

function appendStep(step) {
    const el = document.createElement('div');
    if (step.type === 'reasoning') {
        el.className = 'step-block step-reasoning collapsed';
        el.innerHTML =
            `<div class="step-head" onclick="toggleStep(this)">
                <span class="step-icon">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.663 17h4.673M12 3v1m6.364 1.636-.707.707M21 12h-1M4 12H3m3.343-5.657-.707-.707m2.828 9.9a5 5 0 1 1 7.072 0l-.548.547A3.374 3.374 0 0 0 14 18.469V19a2 2 0 1 1-4 0v-.531c0-.895-.356-1.754-.988-2.386z"/></svg>
                </span>
                <span class="step-title">思考过程</span>
                <span class="step-toggle">点击展开</span>
            </div>
            <div class="step-body">${escapeHtml(step.content)}</div>`;
    } else if (step.type === 'tool_call') {
        el.className = 'step-block step-tool collapsed';
        const argsStr = JSON.stringify(step.args, null, 2);
        const resultStr = step.result || '';
        el.innerHTML =
            `<div class="step-head" onclick="toggleStep(this)">
                <span class="step-icon">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                </span>
                <span class="step-title">工具调用 · ${escapeHtml(step.name)}</span>
                <span class="step-toggle">点击展开</span>
            </div>
            <div class="step-body">
                <div class="step-label">参数</div>
                <div>${escapeHtml(argsStr)}</div>
                <div class="step-label">结果</div>
                <div>${escapeHtml(resultStr)}</div>
            </div>`;
    }
    messagesEl().appendChild(el);
    scrollToBottom();
}

function toggleStep(headerEl) {
    const block = headerEl.parentElement;
    block.classList.toggle('collapsed');
    const toggle = headerEl.querySelector('.step-toggle');
    toggle.textContent = block.classList.contains('collapsed') ? '点击展开' : '点击折叠';
}

async function resetChat() {
    if (!confirm('确定清空当前对话？')) return;
    try {
        await fetch('/api/reset', { method: 'POST' });
        messagesEl().innerHTML = renderWelcome();
    } catch (e) {
        showError('清空失败: ' + e.message);
    }
}

function renderWelcome() {
    return `
        <div class="welcome">
            <div class="welcome-icon">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>
            </div>
            <h1>开始对话</h1>
            <p>mini-agent 是一个自主 Agent，能调用工具、自主多步推理。输入消息开始。</p>
            <div class="welcome-examples">
                <button class="example-chip" onclick="useExample(this)">帮我看看当前目录有哪些文件</button>
                <button class="example-chip" onclick="useExample(this)">现在几点了？</button>
                <button class="example-chip" onclick="useExample(this)">查看 Python 版本</button>
            </div>
        </div>`;
}

// ------------------------------------------------------------
// 状态 / 错误
// ------------------------------------------------------------
function setStatus(text, show) {
    $('status-bar').hidden = !show;
    if (show) $('status-text').textContent = text;
}
function showError(msg) {
    const bar = $('error-bar');
    bar.textContent = msg;
    bar.hidden = false;
}
function hideError() {
    $('error-bar').hidden = true;
}

// ------------------------------------------------------------
// 设置表单
// ------------------------------------------------------------
// 提示词存储位置提示：文件引用模式 → 显示来源 md 文件路径；内联模式 → 提示将存入 config.yaml
function setPromptHint(elId, file) {
    const el = $(elId);
    if (!el) return;
    if (file) {
        el.innerHTML = '存储于 <code>' + escapeHtml(file) + '</code>，保存时写回此文件';
    } else {
        el.textContent = '内联模式，保存时写入 config.yaml';
    }
}

async function loadSettings() {
    try {
        const resp = await fetch('/api/config');
        if (!resp.ok) {
            const err = await resp.json();
            showToast(err.detail || '加载失败', 'error');
            return;
        }
        const cfg = await resp.json();
        $('cfg-base-url').value = cfg.api?.base_url || '';
        $('cfg-api-key').value = cfg.api?.api_key || '';
        $('cfg-model').value = cfg.api?.model || '';
        $('cfg-max-steps').value = cfg.agent?.max_steps ?? '';
        $('cfg-temperature').value = cfg.agent?.temperature ?? '';
        $('cfg-max-tokens').value = cfg.agent?.max_tokens ?? '';
        $('cfg-reasoning-effort').value = cfg.agent?.reasoning_effort || 'none';
        $('cfg-system-prompt').value = cfg.agent?.system_prompt || '';
        $('cfg-user-prompt').value = cfg.agent?.user_prompt || '';
        // skills_dir：后端返回列表，前端用多行文本展示（每行一个目录）
        const sdList = cfg.agent?.skills_dir;
        $('cfg-skills-dir').value = Array.isArray(sdList) ? sdList.join('\n') : (sdList || '');
        // 提示词存储位置提示：文件引用模式显示来源文件，内联模式提示将存入 config.yaml
        setPromptHint('cfg-system-prompt-hint', cfg.agent?.system_prompt_file);
        setPromptHint('cfg-user-prompt-hint', cfg.agent?.user_prompt_file);
    } catch (e) {
        showToast('加载失败: ' + e.message, 'error');
    }
}

async function saveSettings(event) {
    event.preventDefault();
    const saveBtn = event.target.querySelector('button[type="submit"]');
    const originalHTML = saveBtn.innerHTML;
    saveBtn.disabled = true;
    saveBtn.classList.add('loading');
    saveBtn.innerHTML = '<span class="btn-spinner"></span> 保存中…';

    const data = {
        api: {
            base_url: $('cfg-base-url').value.trim(),
            api_key: $('cfg-api-key').value,
            model: $('cfg-model').value.trim(),
        },
        agent: {
            max_steps: parseInt($('cfg-max-steps').value, 10) || 10,
            temperature: parseFloat($('cfg-temperature').value) || 0.7,
            max_tokens: parseInt($('cfg-max-tokens').value, 10) || 4096,
            reasoning_effort: $('cfg-reasoning-effort').value,
            system_prompt: $('cfg-system-prompt').value,
            user_prompt: $('cfg-user-prompt').value,
            // skills_dir：前端多行文本 → 列表（去空行、去首尾空白）
            skills_dir: $('cfg-skills-dir').value.split('\n').map(s => s.trim()).filter(Boolean),
        },
    };
    try {
        const resp = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!resp.ok) {
            const err = await resp.json();
            showToast(err.detail || '保存失败', 'error');
            return;
        }
        showToast('保存成功，已即时生效', 'success');
        setTimeout(() => closeSettings(), 800);
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    } finally {
        saveBtn.disabled = false;
        saveBtn.classList.remove('loading');
        saveBtn.innerHTML = originalHTML;
    }
}

function toggleKeyVisible() {
    const input = $('cfg-api-key');
    const btn = $('key-toggle-btn');
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = '隐藏';
    } else {
        input.type = 'password';
        btn.textContent = '显示';
    }
}

function showToast(msg, type) {
    const toast = $('settings-toast');
    toast.textContent = msg;
    toast.className = 'toast ' + type;
    toast.hidden = false;
    setTimeout(() => { toast.hidden = true; }, 2500);
}

// ------------------------------------------------------------
// Markdown 渲染
// ------------------------------------------------------------
// 配置 marked：GFM + 单换行转 <br>（更贴合聊天场景）
if (typeof marked !== 'undefined') {
    marked.setOptions({ gfm: true, breaks: true });
}

// 把 markdown 文本渲染为安全 HTML；CDN 失败时回退为纯文本
function renderMarkdown(text) {
    if (!text) return '';
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
        return escapeHtml(text).replace(/\n/g, '<br>');
    }
    let html;
    try {
        html = marked.parse(text);
    } catch (_) {
        return escapeHtml(text).replace(/\n/g, '<br>');
    }
    try {
        html = DOMPurify.sanitize(html, { ADD_ATTR: ['target'] });
    } catch (_) { /* 极端情况保留原始解析结果 */ }
    return html;
}

// 对容器内所有代码块做高亮；流式中不调用，定稿后调用一次
function highlightCode(container) {
    if (!container || typeof hljs === 'undefined') return;
    container.querySelectorAll('pre code').forEach((block) => {
        try { hljs.highlightElement(block); } catch (_) { /* ignore */ }
    });
}

// ------------------------------------------------------------
// 工具函数
// ------------------------------------------------------------
function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function scrollToBottom() {
    const el = messagesEl();
    el.scrollTop = el.scrollHeight;
}

function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 160) + 'px';
}

function updateSendBtn() {
    // 发送中按钮状态由 setSending 管理，不干预
    if (sending) return;
    const input = $('chat-input');
    const hasText = input.value.trim().length > 0;
    const hasFiles = getUploadedFileIds().length > 0;
    $('send-btn').disabled = !hasText && !hasFiles;
}

// ------------------------------------------------------------
// 事件绑定
// ------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    const input = $('chat-input');
    input.addEventListener('input', () => { autoResize(input); updateSendBtn(); });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChat();
        }
    });
    input.focus();
});
