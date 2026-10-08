const $ = (id) => document.getElementById(id);
const state = {
  view: 'home',
  platforms: [],
  platform: null,
  conversationId: null,
  activeConv: null,
  activeMessages: [],
  note: null,
  genSteps: [],
  generating: false,
  model: null,
  models: [],
  conversations: [],
  settings: null,
  busy: false,
  settingsTab: 'models',
  editingModelName: null,
};
const PLATFORM_BADGES = { douyin: '抖音', bilibili: 'B站', zhihu: '知乎', xiaohongshu: '小红书' };

async function init() {
  bindUI();
  await Promise.all([loadModels(), loadSettings(), loadPlatforms()]);
  await loadConversations();
  checkHealth();
  showHome();
}

async function checkHealth() {
  try {
    await api('/api/health');
    $('health').textContent = '已连接';
  } catch (err) {
    $('health').textContent = '连接失败';
  }
}

function bindUI() {
  $('btn-new').addEventListener('click', showHome);
  $('btn-settings').addEventListener('click', openSettings);
  $('btn-close-settings').addEventListener('click', closeSettings);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('settings-page').classList.contains('hidden')) closeSettings();
  });
  $('btn-menu').addEventListener('click', () => $('sidebar').classList.toggle('open'));
  $('conv-search').addEventListener('input', renderConversations);
  $('btn-send').addEventListener('click', sendMessage);
  $('input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  $('input').addEventListener('input', autosizeInput);
  $('model-select').addEventListener('change', onModelChange);
  document.querySelectorAll('.settings-nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.settingsTab = btn.dataset.tab;
      document.querySelectorAll('.settings-nav-item').forEach((b) => b.classList.toggle('active', b === btn));
      renderSettings();
    });
  });
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data.detail || data.error || {};
    const msg = detail.message || detail.title || data.detail || '请求失败';
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return res.json();
}

async function streamSSE(path, body, onEvent) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data.detail || data.error || {};
    throw new Error(detail.message || '请求失败');
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop();
    for (const part of parts) {
      const line = part.trim();
      if (line.startsWith('data: ')) {
        try { onEvent(JSON.parse(line.slice(6))); } catch (e) { /* 忽略坏帧 */ }
      }
    }
  }
}

function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.style.display = 'block';
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.style.display = 'none'; }, 2800);
}

function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function refreshIcons() {
  if (window.lucide) { window.lucide.createIcons(); }
}

function scrollToBottom() {
  const el = $('scroll');
  el.scrollTop = el.scrollHeight;
}

function autosizeInput() {
  const el = $('input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}

function setBusy(flag, hint) {
  state.busy = flag;
  $('btn-send').disabled = flag;
  $('composer-hint').textContent = hint || (flag ? '正在生成回答' : '');
}

async function onModelChange(e) {
  state.model = e.target.value;
  $('model-mini-text').textContent = state.model || '未配置模型';
  if (state.conversationId) {
    try {
      await api('/api/conversations/' + state.conversationId, {
        method: 'PATCH', body: JSON.stringify({ model_name: state.model })
      });
      await loadConversations();
      refreshHeader();
    } catch (err) { toast(err.message); }
  }
}

/* ================= 首页：平台选择 ================= */

async function loadPlatforms() {
  try {
    const data = await api('/api/platforms');
    state.platforms = data.platforms || [];
  } catch (err) {
    state.platforms = [];
  }
}

function showHome() {
  state.view = 'home';
  state.conversationId = null;
  state.activeConv = null;
  state.activeMessages = [];
  state.note = null;
  state.genSteps = [];
  state.generating = false;
  $('composer-wrap').classList.add('hidden');
  $('conv-title').textContent = '新建笔记';
  $('conv-meta').textContent = '';
  renderConversations();
  renderHome();
  $('sidebar').classList.remove('open');
}

function renderHome() {
  const wrap = $('wrap');
  const cards = state.platforms.map((p) => {
    const disabled = !p.enabled;
    const active = state.platform === p.key;
    const cookieWarn = (p.key === 'douyin' && p.cookie_configured === false)
      ? '<div class="cookie-warn">未配置 Cookie，可能抓取失败（设置 → Cookie）</div>' : '';
    return '<button class="platform-card' + (active ? ' active' : '') + (disabled ? ' disabled' : '') +
      '" data-platform="' + p.key + '" type="button"' + (disabled ? ' disabled' : '') + '>' +
      '<div class="platform-name">' + escapeHtml(p.name) + '</div>' +
      '<div class="platform-desc">' + escapeHtml(p.desc) + '</div>' +
      cookieWarn + '</button>';
  }).join('');
  const selected = state.platforms.find((p) => p.key === state.platform);
  const noModel = !state.models.some((m) => m.enabled);
  wrap.innerHTML =
    '<div class="home-hero">' +
    '<h2 class="home-title">把视频 / 图文变成结构化笔记</h2>' +
    '<div class="home-sub">选择平台，粘贴链接，剩下的交给知记</div>' +
    '</div>' +
    '<div class="platform-grid">' + cards + '</div>' +
    '<div class="url-bar">' +
    '<input id="url-input" placeholder="' + (selected ? escapeHtml(selected.placeholder) : '先选择一个平台') + '"' +
    (selected ? '' : ' disabled') + ' autocomplete="off">' +
    '<button id="btn-generate" type="button"' + (selected && !noModel ? '' : ' disabled') + '>生成笔记</button>' +
    '</div>' +
    '<div class="home-tip">' +
    (noModel ? '还没有可用模型，请先到「设置 → 模型」中添加' :
      (selected ? '当前使用模型：' + escapeHtml(state.model || '默认模型') + '，可在右上角切换' : '点击上方卡片选择内容平台')) +
    '</div>';
  wrap.querySelectorAll('.platform-card:not(.disabled)').forEach((card) => {
    card.addEventListener('click', () => {
      state.platform = card.dataset.platform;
      renderHome();
      $('url-input').focus();
    });
  });
  const btn = $('btn-generate');
  if (btn) btn.addEventListener('click', startGeneration);
  const urlInput = $('url-input');
  if (urlInput) {
    urlInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); startGeneration(); }
    });
  }
  refreshIcons();
}

/* ================= 笔记生成（SSE） ================= */

const STAGE_ORDER = ['parse', 'fetch', 'transcribe', 'generate', 'save'];
const STAGE_LABELS = { parse: '解析链接', fetch: '抓取内容', transcribe: '音频转写', generate: 'AI 生成笔记', save: '保存笔记' };

async function startGeneration() {
  const url = $('url-input').value.trim();
  if (!url) { toast('请先粘贴链接'); return; }
  state.view = 'conv';
  state.generating = true;
  state.genSteps = [];
  state.note = null;
  state.activeMessages = [];
  $('composer-wrap').classList.add('hidden');
  $('conv-title').textContent = '正在生成笔记';
  $('conv-meta').textContent = url;
  renderGenPanel();

  let rawText = '';
  try {
    await streamSSE('/api/note/generate/stream', { url, model: state.model }, (ev) => {
      if (ev.stage) {
        upsertStep(ev.stage, ev.message, ev.status);
      } else if (ev.delta) {
        rawText += ev.delta;
        const pre = $('gen-raw-text');
        if (pre) { pre.textContent = rawText; pre.scrollTop = pre.scrollHeight; }
      } else if (ev.done) {
        onGenerationDone(ev);
      } else if (ev.error) {
        onGenerationError(ev.error);
      }
    });
  } catch (err) {
    onGenerationError({ message: err.message });
  } finally {
    state.generating = false;
    if (state.conversationId) {
      $('composer-wrap').classList.remove('hidden');
      $('composer-hint').textContent = '';
    }
  }
}

function upsertStep(stage, message, status) {
  let step = state.genSteps.find((s) => s.stage === stage);
  if (!step) {
    step = { stage, status: 'pending', message: STAGE_LABELS[stage] || stage };
    state.genSteps.push(step);
    state.genSteps.sort((a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage));
  }
  step.status = status === 'ok' ? 'done' : 'running';
  if (message) step.message = message;
  renderGenSteps();
}

function renderGenPanel(error) {
  $('wrap').innerHTML =
    '<div class="gen-panel">' +
    '<div class="gen-panel-head"><span class="spin">◌</span><span>正在处理链接</span></div>' +
    '<div class="gen-steps" id="gen-steps"></div>' +
    '<details class="gen-raw"><summary>生成过程（LLM 原始输出）</summary>' +
    '<pre id="gen-raw-text"></pre></details>' +
    '<div id="gen-error"></div>' +
    '</div>' +
    '<div id="note-slot"></div>' +
    '<div id="msg-slot"></div>';
  renderGenSteps();
  if (error) showGenError(error);
  scrollToBottom();
}

function renderGenSteps() {
  const box = $('gen-steps');
  if (!box) return;
  box.innerHTML = state.genSteps.map((s) => {
    const icon = s.status === 'done' ? '✓' : s.status === 'error' ? '✗' : s.status === 'running' ? '<span class="spin">◌</span>' : '○';
    return '<div class="step ' + s.status + '"><span class="step-icon">' + icon + '</span>' +
      '<span class="step-msg">' + escapeHtml(s.message) + '</span></div>';
  }).join('');
  scrollToBottom();
}

function showGenError(error) {
  const box = $('gen-error');
  if (box) {
    const hint = error.hint ? '\n提示：' + error.hint : '';
    box.innerHTML = '<div class="gen-error">' + escapeHtml((error.message || '生成失败') + hint) + '</div>';
  }
  const running = state.genSteps.find((s) => s.status === 'running');
  if (running) running.status = 'error';
  renderGenSteps();
  const head = document.querySelector('.gen-panel-head');
  if (head) head.innerHTML = '<span style="color:var(--danger)">✗</span><span>生成失败</span>';
}

function onGenerationError(error) {
  showGenError(error);
  state.generating = false;
  $('conv-title').textContent = '生成失败';
  const box = $('gen-error');
  if (box) {
    const backBtn = document.createElement('button');
    backBtn.className = 'btn';
    backBtn.style.marginTop = '10px';
    backBtn.textContent = '返回首页重新输入';
    backBtn.addEventListener('click', showHome);
    box.appendChild(backBtn);
  }
}

function onGenerationDone(ev) {
  state.generating = false;
  const conv = ev.conversation;
  state.conversationId = conv.id;
  state.activeConv = conv;
  state.activeMessages = [];
  state.note = ev.note;
  state.note.platform = conv.platform;
  state.note.source_url = conv.source_url;
  const head = document.querySelector('.gen-panel-head');
  if (head) head.innerHTML = '<span style="color:var(--accent)">✓</span><span>笔记已生成</span>';
  renderNoteCard(ev.warnings || []);
  refreshHeader();
  loadConversations();
  toast('笔记已生成，可在下方继续提问或要求修改');
}

/* ================= 笔记卡片 ================= */

function renderNoteCard(warnings) {
  const slot = $('note-slot');
  if (!slot || !state.note) return;
  const badge = PLATFORM_BADGES[state.note.platform] || state.note.platform || '';
  const warns = (warnings && warnings.length)
    ? '<div class="warnings">' + warnings.map(escapeHtml).join('<br>') + '</div>' : '';
  slot.innerHTML =
    '<div class="note-card">' +
    '<div class="note-head">' +
    (badge ? '<span class="badge">' + escapeHtml(badge) + '</span>' : '') +
    '<div class="note-title">' + escapeHtml(state.note.title || '未命名笔记') + '</div>' +
    '<div class="note-actions">' +
    '<button class="btn" id="btn-note-copy" type="button"><i data-lucide="copy"></i>复制 Markdown</button>' +
    '<button class="btn" id="btn-note-dl" type="button"><i data-lucide="download"></i>下载 .md</button>' +
    (state.note.source_url ? '<a class="btn" href="' + escapeHtml(state.note.source_url) + '" target="_blank" rel="noopener"><i data-lucide="external-link"></i>原文</a>' : '') +
    '</div></div>' +
    warns +
    '<div class="note-body md">' + renderMarkdown(state.note.markdown) + '</div>' +
    (state.note.path ? '<div class="note-path">笔记文件：' + escapeHtml(state.note.path) +
      (state.note.transcript_path ? '<br>转写文本：' + escapeHtml(state.note.transcript_path) : '') + '</div>' : '') +
    '</div>' +
    '<div class="section-label">基于笔记继续对话</div>';
  $('btn-note-copy').addEventListener('click', () => {
    navigator.clipboard.writeText(state.note.markdown).then(() => toast('已复制 Markdown'));
  });
  $('btn-note-dl').addEventListener('click', () => downloadText(
    (state.note.title || '笔记') + '.md', state.note.markdown));
  refreshIcons();
  scrollToBottom();
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename.replace(/[\\/:*?"<>|]/g, '_');
  a.click();
  URL.revokeObjectURL(a.href);
}

async function applyAsNote(content, btn) {
  if (!state.note) return;
  if (!state.note.path) { toast('该会话没有关联的笔记文件'); return; }
  if (!window.confirm('用这条回复覆盖当前笔记文件？')) return;
  try {
    btn.disabled = true;
    await api('/api/note/save', {
      method: 'POST',
      body: JSON.stringify({
        path: state.note.path,
        content,
        conversation_id: state.conversationId,
      }),
    });
    state.note.markdown = stripFrontmatter(content);
    renderNoteCard([]);
    toast('已设为当前笔记');
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

function stripFrontmatter(markdown) {
  const text = String(markdown || '').replace(/^\s+/, '');
  if (text.startsWith('---')) {
    const end = text.indexOf('\n---', 3);
    if (end !== -1) return text.slice(end + 4).replace(/^\n+/, '');
  }
  return markdown;
}

/* ================= 会话视图 + 对话 ================= */

async function openConversation(id) {
  try {
    const data = await api('/api/conversations/' + id);
    let conversation = data.conversation;
    const convModelOk = conversation.model_name && state.models.some(
      (m) => m.enabled && m.name === conversation.model_name);
    if (convModelOk) {
      state.model = conversation.model_name;
    } else if (state.model && state.models.some((m) => m.enabled && m.name === state.model)) {
      if (conversation.model_name !== state.model) {
        const updated = await api('/api/conversations/' + id, {
          method: 'PATCH', body: JSON.stringify({ model_name: state.model })
        });
        conversation = updated.conversation;
      }
    }
    state.view = 'conv';
    state.conversationId = conversation.id;
    state.activeConv = conversation;
    state.activeMessages = (conversation.messages || []).map((m) => ({ role: m.role, content: m.content }));
    state.note = conversation.note_content
      ? { title: conversation.title, markdown: conversation.note_content, path: conversation.note_path, platform: conversation.platform, source_url: conversation.source_url }
      : null;
    state.genSteps = [];
    renderModelSelect();
    renderConvView();
    renderConversations();
    refreshHeader();
    $('sidebar').classList.remove('open');
  } catch (err) { toast(err.message); }
}

function renderConvView() {
  const wrap = $('wrap');
  wrap.innerHTML = '<div id="note-slot"></div><div id="msg-slot"></div>';
  if (state.note) renderNoteCard([]);
  renderMessages();
  $('composer-wrap').classList.remove('hidden');
  $('composer-hint').textContent = '';
  $('input').placeholder = state.note
    ? '针对笔记提问，或要求修改（例如：把核心观点压缩成三条）'
    : '输入消息';
  scrollToBottom();
}

function refreshHeader() {
  const conv = state.conversations.find((c) => c.id === state.conversationId) || state.activeConv;
  if (state.view === 'home') {
    $('conv-title').textContent = '新建笔记';
    $('conv-meta').textContent = '';
    return;
  }
  $('conv-title').textContent = conv ? conv.title : '会话';
  $('conv-meta').textContent = conv ? (conv.model_name || '默认模型') : '';
}

function renderMessages() {
  const slot = $('msg-slot');
  if (!slot) return;
  slot.innerHTML = '';
  state.activeMessages.forEach((m) => appendMessage(slot, m.role, m.content));
  refreshIcons();
  scrollToBottom();
}

function appendMessage(slot, role, content) {
  const div = document.createElement('div');
  div.className = 'msg';
  div.innerHTML =
    '<div class="avatar ' + role + '">' + (role === 'user' ? '我' : '知') + '</div>' +
    '<div class="bubble ' + role + '"><div class="md">' +
    (role === 'user' ? escapeHtml(content).replace(/\n/g, '<br>') : renderMarkdown(content)) +
    '</div></div>';
  slot.appendChild(div);
  if (role === 'assistant' && content) {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    const copyBtn = document.createElement('button');
    copyBtn.className = 'btn';
    copyBtn.innerHTML = '<i data-lucide="copy"></i>复制';
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(content).then(() => toast('已复制'));
    });
    actions.appendChild(copyBtn);
    if (state.note && state.note.path) {
      const applyBtn = document.createElement('button');
      applyBtn.className = 'btn';
      applyBtn.innerHTML = '<i data-lucide="check"></i>设为当前笔记';
      applyBtn.addEventListener('click', () => applyAsNote(content, applyBtn));
      actions.appendChild(applyBtn);
    }
    div.querySelector('.bubble').appendChild(actions);
  }
  return div;
}

async function sendMessage() {
  if (state.busy || state.generating) return;
  const text = $('input').value.trim();
  if (!text) return;
  if (!state.conversationId) { toast('请先生成笔记或选择一个会话'); return; }
  $('input').value = '';
  autosizeInput();
  const slot = $('msg-slot');
  state.activeMessages.push({ role: 'user', content: text });
  appendMessage(slot, 'user', text);
  const bubble = appendMessage(slot, 'assistant', '');
  const mdEl = bubble.querySelector('.md');
  setBusy(true);
  let collected = '';
  try {
    await streamSSE('/api/chat/stream', {
      conversation_id: state.conversationId,
      message: text,
      model_name: state.model,
    }, (ev) => {
      if (ev.delta) {
        collected += ev.delta;
        mdEl.innerHTML = renderMarkdown(collected);
        scrollToBottom();
      } else if (ev.error) {
        mdEl.innerHTML += '<p style="color:#ffb4ab">' + escapeHtml(ev.error.message || '生成失败') + '</p>';
      } else if (ev.done && ev.conversation) {
        state.activeConv = ev.conversation;
      }
    });
    if (collected) {
      state.activeMessages.push({ role: 'assistant', content: collected });
      renderMessages();
    }
  } catch (err) {
    mdEl.innerHTML += '<p style="color:#ffb4ab">' + escapeHtml(err.message) + '</p>';
  } finally {
    setBusy(false);
    refreshIcons();
    loadConversations();
    refreshHeader();
  }
}

/* ================= Markdown 渲染 ================= */

function mdInline(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return out;
}

function renderMarkdown(src) {
  const lines = String(src || '').replace(/\r\n/g, '\n').split('\n');
  const html = [];
  let inCode = false;
  let codeBuf = [];
  let listType = null;
  let para = [];
  const flushPara = () => {
    if (para.length) { html.push('<p>' + para.map(mdInline).join('<br>') + '</p>'); para = []; }
  };
  const closeList = () => {
    if (listType) { html.push('</' + listType + '>'); listType = null; }
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (/^```/.test(line.trim())) {
      if (inCode) {
        html.push('<pre><code>' + escapeHtml(codeBuf.join('\n')) + '</code></pre>');
        codeBuf = []; inCode = false;
      } else {
        flushPara(); closeList(); inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }
    const trimmed = line.trim();
    if (!trimmed) { flushPara(); closeList(); continue; }
    const head = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (head) {
      flushPara(); closeList();
      const level = head[1].length;
      html.push('<h' + level + '>' + mdInline(head[2]) + '</h' + level + '>');
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) { flushPara(); closeList(); html.push('<hr>'); continue; }
    if (trimmed.startsWith('>')) {
      flushPara(); closeList();
      html.push('<blockquote>' + mdInline(trimmed.replace(/^>\s?/, '')) + '</blockquote>');
      continue;
    }
    const ul = trimmed.match(/^[-*+]\s+(.*)$/);
    if (ul) {
      flushPara();
      if (listType !== 'ul') { closeList(); html.push('<ul>'); listType = 'ul'; }
      html.push('<li>' + mdInline(ul[1]) + '</li>');
      continue;
    }
    const ol = trimmed.match(/^\d+[.)]\s+(.*)$/);
    if (ol) {
      flushPara();
      if (listType !== 'ol') { closeList(); html.push('<ol>'); listType = 'ol'; }
      html.push('<li>' + mdInline(ol[1]) + '</li>');
      continue;
    }
    closeList();
    para.push(trimmed);
  }
  if (inCode) html.push('<pre><code>' + escapeHtml(codeBuf.join('\n')) + '</code></pre>');
  flushPara(); closeList();
  return html.join('\n');
}

/* ================= 会话列表 ================= */

async function loadConversations() {
  try {
    const data = await api('/api/conversations');
    state.conversations = data.conversations || [];
    renderConversations();
  } catch (err) { /* 忽略 */ }
}

function renderConversations() {
  const list = $('conv-list');
  const keyword = ($('conv-search').value || '').trim().toLowerCase();
  const items = state.conversations.filter((c) => !keyword || c.title.toLowerCase().includes(keyword));
  if (!items.length) {
    list.innerHTML = '<div class="search-empty">' + (keyword ? '没有匹配的会话' : '还没有会话，点击上方「新建笔记」开始') + '</div>';
    refreshIcons();
    return;
  }
  list.innerHTML = '';
  items.forEach((c) => {
    const item = document.createElement('div');
    item.className = 'conv-item' + (c.id === state.conversationId ? ' active' : '');
    const badge = c.platform
      ? '<span class="conv-badge">' + escapeHtml(PLATFORM_BADGES[c.platform] || c.platform) + '</span>'
      : (c.has_note ? '' : '<span class="conv-badge plain">聊</span>');
    item.innerHTML = badge +
      '<span class="conv-title">' + escapeHtml(c.title) + '</span>' +
      '<span class="conv-count">' + c.message_count + '</span>' +
      '<span class="conv-actions">' +
      '<button class="icon-btn" data-act="rename" type="button" aria-label="重命名"><i data-lucide="pencil"></i></button>' +
      '<button class="icon-btn danger" data-act="delete" type="button" aria-label="删除"><i data-lucide="trash-2"></i></button>' +
      '</span>';
    item.addEventListener('click', () => openConversation(c.id));
    item.querySelector('[data-act="rename"]').addEventListener('click', (e) => {
      e.stopPropagation(); startRename(item, c);
    });
    item.querySelector('[data-act="delete"]').addEventListener('click', (e) => {
      e.stopPropagation(); removeConversation(c.id);
    });
    list.appendChild(item);
  });
  refreshIcons();
}

function startRename(item, conv) {
  const titleEl = item.querySelector('.conv-title');
  const input = document.createElement('input');
  input.className = 'rename-input';
  input.value = conv.title;
  titleEl.replaceWith(input);
  input.focus();
  input.select();
  const finish = async (commit) => {
    const value = input.value.trim();
    if (commit && value && value !== conv.title) {
      try {
        await api('/api/conversations/' + conv.id, { method: 'PATCH', body: JSON.stringify({ title: value }) });
        await loadConversations();
        refreshHeader();
      } catch (err) { toast(err.message); }
    } else {
      renderConversations();
    }
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

async function removeConversation(id) {
  if (!window.confirm('删除这个会话？')) return;
  try {
    await api('/api/conversations/' + id, { method: 'DELETE' });
    state.conversations = state.conversations.filter((c) => c.id !== id);
    if (state.conversationId === id) {
      showHome();
      return;
    }
    renderConversations();
  } catch (err) { toast(err.message); }
}

/* ================= 模型与设置 ================= */

async function loadModels() {
  try {
    const data = await api('/api/models');
    state.models = data.models || [];
    if (!state.model || !state.models.some((m) => m.enabled && m.name === state.model)) {
      const def = state.models.find((m) => m.default && m.enabled) || state.models.find((m) => m.enabled);
      state.model = def ? def.name : null;
    }
    renderModelSelect();
    $('model-mini-text').textContent = state.model || '未配置模型';
  } catch (err) { /* 忽略 */ }
}

function renderModelSelect() {
  const select = $('model-select');
  const enabled = state.models.filter((m) => m.enabled);
  if (!enabled.length) {
    select.innerHTML = '<option value="">未配置模型</option>';
    return;
  }
  select.innerHTML = enabled.map((m) =>
    '<option value="' + escapeHtml(m.name) + '"' + (m.name === state.model ? ' selected' : '') + '>' +
    escapeHtml(m.name) + (m.default ? '（默认）' : '') + '</option>').join('');
}

async function loadSettings() {
  try {
    state.settings = await api('/api/settings');
  } catch (err) { /* 忽略 */ }
}

const SETTINGS_TABS = {
  models: { title: '模型', sub: '管理 LLM 模型：添加、编辑、连通测试与默认模型' },
  path: { title: '保存路径', sub: '设置笔记与转写缓存的保存位置' },
  general: { title: '通用', sub: '音频转写参数与内容平台开关' },
  cookies: { title: 'Cookie', sub: '管理各内容平台的登录态（Cookie）' },
};

function openSettings() {
  $('settings-page').classList.remove('hidden');
  renderSettings();
}

function closeSettings() {
  $('settings-page').classList.add('hidden');
}

function renderSettings() {
  const body = $('settings-body');
  const tab = SETTINGS_TABS[state.settingsTab] || SETTINGS_TABS.models;
  $('settings-title').textContent = tab.title;
  $('settings-sub').textContent = tab.sub;
  body.innerHTML = '<div class="settings-body-inner" id="settings-inner"></div>';
  const inner = $('settings-inner');
  if (state.settingsTab === 'models') {
    renderModelPanel(inner);
  } else if (state.settingsTab === 'path') {
    renderPathPanel(inner);
  } else if (state.settingsTab === 'general') {
    renderGeneralPanel(inner);
  } else {
    renderCookiesPanel(inner);
  }
  refreshIcons();
}

function renderModelPanel(body) {
  const rows = state.models.map((m) =>
    '<div class="model-row">' +
    '<div class="model-row-top"><span class="model-row-name">' + escapeHtml(m.name) + '</span>' +
    (m.default ? '<span class="badge">默认</span>' : '') +
    (m.enabled ? '' : '<span class="badge" style="background:var(--surface-2);color:var(--muted)">停用</span>') +
    '</div>' +
    '<div class="model-row-meta">' + escapeHtml(m.model) + ' ｜ ' + escapeHtml(m.base_url) + ' ｜ Key: ' + escapeHtml(m.api_key || '未设置') + '</div>' +
    '<div class="model-row-actions">' +
    '<button class="btn small" onclick="editModel(\'' + escapeHtml(m.name) + '\')" type="button">编辑</button>' +
    '<button class="btn small" onclick="testModel(\'' + escapeHtml(m.name) + '\')" type="button">测试</button>' +
    (m.default ? '' : '<button class="btn small" onclick="setDefault(\'' + escapeHtml(m.name) + '\')" type="button">设为默认</button>') +
    '<button class="btn small danger" onclick="removeModel(\'' + escapeHtml(m.name) + '\')" type="button">删除</button>' +
    '</div></div>').join('');
  body.innerHTML =
    (rows || '<div class="search-empty">还没有模型，请在下方添加</div>') +
    '<hr style="border-color:var(--border);margin:12px 0">' +
    '<h3 style="margin:0 0 8px;font-size:14px">' + (state.editingModelName ? '编辑模型' : '添加模型') + '</h3>' +
    '<div class="form-field"><label>名称</label><input id="m-name" placeholder="例如 DeepSeek"></div>' +
    '<div class="form-field"><label>API Key' + (state.editingModelName ? '（留空则不修改）' : '') + '</label><input id="m-key" type="password" placeholder="sk-..."></div>' +
    '<div class="form-field"><label>Base URL</label><input id="m-base" placeholder="https://api.deepseek.com/v1"></div>' +
    '<div class="form-row">' +
    '<div class="form-field"><label>模型 ID</label><input id="m-model" placeholder="deepseek-chat"></div>' +
    '<div class="form-field"><label>备注</label><input id="m-note" placeholder="可选"></div>' +
    '</div>' +
    '<label class="check-line"><input type="checkbox" id="m-default"> 设为默认模型</label>' +
    '<div style="display:flex;gap:8px;margin-top:8px">' +
    '<button class="btn primary" id="btn-save-model" type="button">保存模型</button>' +
    (state.editingModelName ? '<button class="btn" id="btn-cancel-edit" type="button">取消编辑</button>' : '') +
    '</div>';
  $('btn-save-model').addEventListener('click', saveModel);
  const cancel = $('btn-cancel-edit');
  if (cancel) cancel.addEventListener('click', () => { state.editingModelName = null; renderSettings(); });
  if (state.editingModelName) fillModelForm(state.editingModelName);
}

function fillModelForm(name) {
  const m = state.models.find((x) => x.name === name);
  if (!m) return;
  $('m-name').value = m.name;
  $('m-key').value = '';
  $('m-base').value = m.base_url;
  $('m-model').value = m.model;
  $('m-note').value = m.note || '';
  $('m-default').checked = !!m.default;
}

async function saveModel() {
  const payload = {
    name: $('m-name').value.trim(),
    base_url: $('m-base').value.trim(),
    model: $('m-model').value.trim(),
    note: $('m-note').value.trim(),
    enabled: true,
    default: $('m-default').checked
  };
  if (!payload.name || !payload.base_url || !payload.model) { toast('名称 / Base URL / 模型 ID 必填'); return; }
  const key = $('m-key').value.trim();
  if (key || !state.editingModelName) payload.api_key = key;
  try {
    if (!state.editingModelName) {
      const data = await api('/api/models', { method: 'POST', body: JSON.stringify(payload) });
      if (!state.model) state.model = data.model.name;
    } else {
      await api('/api/models/' + encodeURIComponent(state.editingModelName), { method: 'PUT', body: JSON.stringify(payload) });
    }
    toast('模型已保存');
    state.editingModelName = null;
    await loadModels();
    renderSettings();
  } catch (err) { toast(err.message); }
}

function editModel(name) {
  state.editingModelName = name;
  renderSettings();
}

async function testModel(name) {
  toast('正在测试连通性');
  try {
    await api('/api/models/' + encodeURIComponent(name) + '/test', { method: 'POST' });
    toast('连通正常');
  } catch (err) { toast(err.message); }
}

async function setDefault(name) {
  try {
    await api('/api/models/' + encodeURIComponent(name) + '/set-default', { method: 'POST' });
    toast('默认模型已切换');
    if (!state.model) state.model = name;
    await loadModels();
    renderSettings();
  } catch (err) { toast(err.message); }
}

async function removeModel(name) {
  if (!window.confirm('确定删除模型 ' + name + '？')) return;
  try {
    await api('/api/models/' + encodeURIComponent(name), { method: 'DELETE' });
    if (state.model === name) state.model = null;
    await loadModels();
    renderSettings();
  } catch (err) { toast(err.message); }
}

function renderPathPanel(body) {
  const s = state.settings || {};
  body.innerHTML =
    '<div class="form-field"><label>笔记输出目录</label><input id="s-output" value="' + escapeHtml(s.output_dir || '') + '"></div>' +
    '<div class="cookie-hint">笔记 Markdown 与转写缓存都会保存在该目录下，可使用绝对路径。</div>' +
    '<button class="btn primary" id="btn-save-path" type="button" style="margin-top:12px">保存</button>';
  $('btn-save-path').addEventListener('click', async () => {
    try {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify({ output_dir: $('s-output').value.trim() }) });
      toast('保存路径已更新');
      await loadSettings();
    } catch (err) { toast(err.message); }
  });
}

function renderGeneralPanel(body) {
  const s = state.settings || { whisper_model: 'small', whisper_device: 'cpu', keep_audio: false, platforms: {} };
  const platforms = s.platforms || {};
  const labels = { bilibili: 'B 站', zhihu: '知乎', douyin: '抖音', xiaohongshu: '小红书' };
  const whisperOptions = ['tiny', 'small', 'medium', 'large-v3'].map((v) =>
    '<option value="' + v + '"' + (s.whisper_model === v ? ' selected' : '') + '>' + v + '</option>').join('');
  const deviceOptions = ['cpu', 'cuda'].map((v) =>
    '<option value="' + v + '"' + (s.whisper_device === v ? ' selected' : '') + '>' + v + '</option>').join('');

  body.innerHTML =
    '<h3 style="margin:0 0 10px;font-size:14px">音频转写</h3>' +
    '<div class="form-row">' +
    '<div class="form-field"><label>Whisper 模型</label><select id="s-whisper">' + whisperOptions + '</select></div>' +
    '<div class="form-field"><label>Whisper 设备</label><select id="s-device">' + deviceOptions + '</select></div>' +
    '</div>' +
    '<label class="check-line"><input type="checkbox" id="s-keep"' + (s.keep_audio ? ' checked' : '') + '> 保留转写后的音频文件</label>' +
    '<h3 style="margin:14px 0 6px;font-size:14px">内容平台</h3>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">' +
    Object.keys(labels).map((k) =>
      '<label class="check-line"><input type="checkbox" id="plat-' + k + '"' + (platforms[k] ? ' checked' : '') + '> ' + labels[k] + '</label>').join('') +
    '</div>' +
    '<div class="cookie-hint">小红书部分笔记需要登录 Cookie；未配置时会先尝试公开页面。</div>' +
    '<button class="btn primary" id="btn-save-general" type="button" style="margin-top:12px">保存设置</button>';
  $('btn-save-general').addEventListener('click', saveGeneralSettings);
}

const COOKIE_PLATFORMS = [
  { key: 'douyin', name: '抖音', required: '需包含 sessionid', placeholder: '粘贴抖音 Cookie（需包含 sessionid）' },
  { key: 'bilibili', name: 'B 站', required: '需包含 SESSDATA，配置后优先使用官方字幕', placeholder: '粘贴 B 站 Cookie（需包含 SESSDATA）' },
  { key: 'zhihu', name: '知乎', required: '需包含 d_c0', placeholder: '粘贴知乎 Cookie（需包含 d_c0）' },
  { key: 'xiaohongshu', name: '小红书', required: '需包含 web_session', placeholder: '粘贴小红书 Cookie（需包含 web_session）' },
];

async function renderCookiesPanel(body) {
  body.innerHTML = '<div class="search-empty">加载中…</div>';
  const parts = [];
  for (const p of COOKIE_PLATFORMS) {
    let inner = '';
    try {
      const data = await api('/api/cookies/' + p.key);
      if (data.configured) {
        inner = '<div class="check-line" style="color:var(--accent);margin:0 0 8px">已配置（' + data.cookie_length + ' 字节）</div>' +
          '<button class="btn small" id="btn-' + p.key + '-cookie-delete" type="button">清除 Cookie</button>';
      } else {
        inner = '<div class="form-field" style="margin-bottom:8px">' +
          '<textarea id="s-' + p.key + '-cookie" rows="3" placeholder="' + p.placeholder + '"></textarea></div>' +
          '<button class="btn small" id="btn-' + p.key + '-cookie-save" type="button">保存 Cookie</button>';
      }
    } catch (e) {
      inner = '<div class="cookie-hint">状态查询失败，请稍后重试</div>';
    }
    parts.push(
      '<div class="cookie-card">' +
      '<div class="cookie-card-title">' + p.name + '<span class="cookie-card-req">' + p.required + '</span></div>' +
      inner +
      '</div>');
  }
  parts.push(
    '<div class="cookie-howto">' +
    '<h3>如何获取 Cookie？</h3>' +
    '<ol>' +
    '<li>在浏览器中打开对应平台网站并登录账号；</li>' +
    '<li>按 F12 打开开发者工具，切换到「网络 / Network」面板；</li>' +
    '<li>刷新页面，任选一个发往该平台域名的请求；</li>' +
    '<li>在「请求标头 / Request Headers」中找到 Cookie 一行，整行复制；</li>' +
    '<li>粘贴到上方对应平台的输入框并保存。</li>' +
    '</ol>' +
    '<div class="cookie-hint">Cookie 仅保存在本机 data/ 目录，不会上传。失效后重新复制保存即可。</div>' +
    '</div>');
  body.innerHTML = parts.join('');

  for (const p of COOKIE_PLATFORMS) {
    const btnSave = $('btn-' + p.key + '-cookie-save');
    if (btnSave) {
      btnSave.addEventListener('click', async () => {
        const cookie = $('s-' + p.key + '-cookie').value.trim();
        if (!cookie) { toast('请输入 Cookie'); return; }
        try {
          await api('/api/cookies/' + p.key, { method: 'POST', body: JSON.stringify({ cookie }) });
          toast(p.name + ' Cookie 已保存');
          loadPlatforms();
          renderCookiesPanel(body);
        } catch (e) { toast(e.message || '保存失败'); }
      });
    }
    const btnDelete = $('btn-' + p.key + '-cookie-delete');
    if (btnDelete) {
      btnDelete.addEventListener('click', async () => {
        try {
          await api('/api/cookies/' + p.key, { method: 'DELETE' });
          toast(p.name + ' Cookie 已清除');
          loadPlatforms();
          renderCookiesPanel(body);
        } catch (e) { toast('清除失败'); }
      });
    }
  }
}

async function saveGeneralSettings() {
  const payload = {
    whisper_model: $('s-whisper').value,
    whisper_device: $('s-device').value,
    keep_audio: $('s-keep').checked,
    platforms: {}
  };
  ['bilibili', 'zhihu', 'douyin', 'xiaohongshu'].forEach((key) => {
    payload.platforms[key] = $('plat-' + key).checked;
  });
  try {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
    toast('设置已保存');
    await loadSettings();
    await loadPlatforms();
  } catch (err) { toast(err.message); }
}

init();
