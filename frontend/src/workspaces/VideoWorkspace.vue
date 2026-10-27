<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useCoreStore } from '../stores/core.js'
import { useUiStore } from '../stores/ui.js'
import { useAuthStore } from '../stores/auth.js'
import { api, streamSSE, streamGetSSE } from '../lib/api.js'
import { renderMarkdown, stripFrontmatter } from '../lib/markdown.js'
import ModelSelect from '../components/ModelSelect.vue'
import { Plus, Search, Menu, Copy, Download, ExternalLink, Check, Pencil, Trash2, ArrowUp, ArrowLeft } from 'lucide-vue-next'

const core = useCoreStore()
const ui = useUiStore()
const auth = useAuthStore()

const PLATFORM_BADGES = { douyin: '抖音', bilibili: 'B站', zhihu: '知乎', xiaohongshu: '小红书' }
const STAGE_ORDER = ['parse', 'fetch', 'transcribe', 'generate', 'save']
const STAGE_LABELS = { parse: '解析链接', fetch: '抓取内容', transcribe: '音频转写', generate: 'AI 生成笔记', save: '保存笔记' }
const ACTIVE_TASK_KEY = 'zhiji.activeTaskId'

const view = ref('home')
const platform = ref(null)
const conversationId = ref(null)
const activeConv = ref(null)
const activeMessages = ref([])
const note = ref(null)
const genSteps = ref([])
const rawText = ref('')
const generating = ref(false)
const activeTaskId = ref(null)
const activeTaskDone = ref(false)
const busy = ref(false)
const genError = ref(null)
const warnings = ref([])
const keyword = ref('')
const urlInput = ref('')
const composerInput = ref('')
const scrollRef = ref(null)
const renamingId = ref(null)
const renameValue = ref('')

const selectedPlatform = computed(() => core.platforms.find((p) => p.key === platform.value) || null)
const noModel = computed(() => !core.hasModel)
const filteredConversations = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return core.conversations.filter((c) => !kw || c.title.toLowerCase().includes(kw))
})

function toast(msg) { ui.showToast(msg) }
function scrollToBottom() { nextTick(() => { const el = scrollRef.value; if (el) el.scrollTop = el.scrollHeight }) }
function autosize() { nextTick(() => { const el = document.getElementById('composer-input'); if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px' } }) }

function healthWarn(p) {
  const st = (p.health && p.health.state) || ''
  if (st === 'expired') return '登录态已失效，请到「设置 → Cookie」重新保存'
  if (st === 'error') return '上次凭据检测异常，可到「设置 → Cookie」重新检测'
  if (p.key === 'douyin' && p.cookie_configured === false) return '未配置 Cookie，可能抓取失败（设置 → Cookie）'
  return ''
}

function showHome() {
  view.value = 'home'
  conversationId.value = null
  activeConv.value = null
  activeMessages.value = []
  note.value = null
  genSteps.value = []
  generating.value = false
  ui.drawerOpen = false
}

const convTitle = computed(() => {
  if (view.value === 'home') return '新建笔记'
  const c = core.conversations.find((x) => x.id === conversationId.value) || activeConv.value
  return c ? c.title : '会话'
})
const convMeta = computed(() => {
  if (view.value === 'home') return ''
  const c = core.conversations.find((x) => x.id === conversationId.value) || activeConv.value
  return c ? (c.model_name || '默认模型') : ''
})

function isTerminal(status) { return status === 'succeeded' || status === 'failed' || status === 'cancelled' }
function normalizeTaskError(error) {
  if (!error) return { message: '生成失败' }
  if (typeof error === 'string') return { message: error }
  return error
}

async function startGeneration() {
  const url = urlInput.value.trim()
  if (!url) { toast('请先粘贴链接'); return }
  view.value = 'conv'
  generating.value = true
  activeTaskDone.value = false
  genSteps.value = []
  rawText.value = ''
  note.value = null
  activeMessages.value = []
  genError.value = null
  try {
    const created = await api('/api/note/generate', { method: 'POST', body: JSON.stringify({ url, model: core.model }) })
    const task = created.task
    activeTaskId.value = task.id
    localStorage.setItem(ACTIVE_TASK_KEY, task.id)
    await subscribeTaskEvents(task.id)
  } catch (err) {
    onGenerationError(normalizeTaskError(err.message ? { message: err.message } : err))
  } finally {
    generating.value = false
  }
}

async function restoreActiveTask() {
  const taskId = localStorage.getItem(ACTIVE_TASK_KEY)
  if (!taskId) return
  try {
    const data = await api('/api/tasks/' + encodeURIComponent(taskId))
    const task = data.task
    view.value = 'conv'
    generating.value = true
    activeTaskDone.value = false
    genSteps.value = []
    rawText.value = ''
    note.value = null
    activeMessages.value = []
    activeTaskId.value = taskId
    consumeTaskSnapshot(task)
    if (!isTerminal(task.status)) await subscribeTaskEvents(taskId)
  } catch (err) {
    localStorage.removeItem(ACTIVE_TASK_KEY)
  }
}

async function subscribeTaskEvents(taskId) {
  const data = await api('/api/tasks/' + encodeURIComponent(taskId))
  consumeTaskSnapshot(data.task)
  if (isTerminal(data.task.status)) return
  await streamGetSSE('/api/tasks/' + encodeURIComponent(taskId) + '/events', (ev) => {
    if (ev.task) { consumeTaskSnapshot(ev.task); return }
    if (activeTaskDone.value || activeTaskId.value !== taskId) return
    if (ev.stage) upsertStep(ev.stage, ev.message, ev.status)
    else if (ev.delta) { rawText.value += ev.delta; scrollToBottom() }
  })
}

function upsertStep(stage, message, status) {
  let step = genSteps.value.find((s) => s.stage === stage)
  if (!step) {
    step = { stage, status: 'pending', message: STAGE_LABELS[stage] || stage }
    genSteps.value.push(step)
    genSteps.value.sort((a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage))
  }
  step.status = status === 'ok' ? 'done' : (status === 'error' ? 'error' : 'running')
  if (message) step.message = message
}

function consumeTaskSnapshot(task) {
  if (!task || activeTaskId.value !== task.id) return
  if (task.status === 'succeeded') {
    if (activeTaskDone.value) return
    if (task.result && task.result.done) onGenerationDone(task.result)
    else onGenerationError({ message: '任务已完成，但没有返回笔记结果' })
  } else if (task.status === 'failed') {
    if (!activeTaskDone.value) onGenerationError(normalizeTaskError(task.error))
  } else if (task.status === 'cancelled') {
    if (!activeTaskDone.value) onGenerationError({ message: '任务已取消', hint: '可以返回首页重新提交' })
  }
}

function onGenerationError(error) {
  activeTaskDone.value = true
  localStorage.removeItem(ACTIVE_TASK_KEY)
  genError.value = normalizeTaskError(error)
  const running = genSteps.value.find((s) => s.status === 'running')
  if (running) running.status = 'error'
  generating.value = false
}

function onGenerationDone(ev) {
  generating.value = false
  activeTaskDone.value = true
  localStorage.removeItem(ACTIVE_TASK_KEY)
  const conv = ev.conversation
  conversationId.value = conv.id
  activeConv.value = conv
  activeMessages.value = []
  note.value = ev.note
  note.value.platform = note.value.platform || conv.platform
  note.value.source_url = note.value.source_url || conv.source_url
  warnings.value = ev.warnings || []
  core.loadConversations()
  toast('笔记已生成，可在下方继续提问或要求修改')
}

function copyNote() { navigator.clipboard.writeText(note.value.markdown).then(() => toast('已复制 Markdown')) }
function downloadNote() {
  const blob = new Blob([note.value.markdown], { type: 'text/markdown;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = (note.value.title || '笔记').replace(/[\\/:*?"<>|]/g, '_') + '.md'
  a.click()
  URL.revokeObjectURL(a.href)
}

async function applyAsNote(content) {
  if (!note.value || !note.value.path) { toast('该会话没有关联的笔记文件'); return }
  if (!window.confirm('用这条回复覆盖当前笔记文件？')) return
  try {
    await api('/api/note/save', { method: 'POST', body: JSON.stringify({ path: note.value.path, content, conversation_id: conversationId.value }) })
    note.value.markdown = stripFrontmatter(content)
    toast('已设为当前笔记')
  } catch (err) { toast(err.message) }
}

function copyMessage(content) { navigator.clipboard.writeText(content).then(() => toast('已复制')) }

async function openConversation(id) {
  try {
    const data = await api('/api/conversations/' + id)
    let conversation = data.conversation
    const convModelOk = conversation.model_name && core.models.some((m) => m.enabled && m.name === conversation.model_name)
    if (convModelOk) core.model = conversation.model_name
    else if (core.model && core.models.some((m) => m.enabled && m.name === core.model)) {
      if (conversation.model_name !== core.model) {
        const updated = await api('/api/conversations/' + id, { method: 'PATCH', body: JSON.stringify({ model_name: core.model }) })
        conversation = updated.conversation
      }
    }
    view.value = 'conv'
    generating.value = false
    conversationId.value = conversation.id
    activeConv.value = conversation
    activeMessages.value = (conversation.messages || []).map((m) => ({ role: m.role, content: m.content }))
    note.value = conversation.note_content
      ? { title: conversation.title, markdown: conversation.note_content, path: conversation.note_path, platform: conversation.platform, source_url: conversation.source_url }
      : null
    genSteps.value = []
    genError.value = null
    ui.drawerOpen = false
    scrollToBottom()
  } catch (err) { toast(err.message) }
}

async function onModelChange(newModel) {
  core.model = newModel
  if (conversationId.value) {
    try {
      await api('/api/conversations/' + conversationId.value, { method: 'PATCH', body: JSON.stringify({ model_name: newModel }) })
      await core.loadConversations()
    } catch (err) { toast(err.message) }
  }
}

async function sendMessage() {
  if (busy.value || generating.value) return
  const text = composerInput.value.trim()
  if (!text) return
  if (!conversationId.value) { toast('请先生成笔记或选择一个会话'); return }
  composerInput.value = ''
  autosize()
  activeMessages.value.push({ role: 'user', content: text })
  const placeholder = { role: 'assistant', content: '' }
  activeMessages.value.push(placeholder)
  busy.value = true
  let collected = ''
  try {
    await streamSSE('/api/chat/stream', { conversation_id: conversationId.value, message: text, model_name: core.model }, (ev) => {
      if (ev.delta) { collected += ev.delta; placeholder.content = collected; scrollToBottom() }
      else if (ev.error) { placeholder.content = collected + '\n\n> ' + (ev.error.message || '生成失败') }
      else if (ev.done && ev.conversation) { activeConv.value = ev.conversation }
    })
    if (collected) placeholder.content = collected
    await core.loadConversations()
  } catch (err) {
    placeholder.content = (collected ? collected + '\n\n' : '') + '> ' + (err.message || '生成失败')
  } finally {
    busy.value = false
  }
}

function startRename(c) {
  renamingId.value = c.id
  renameValue.value = c.title
  nextTick(() => { const el = document.getElementById('rename-input-' + c.id); if (el) { el.focus(); el.select() } })
}
async function finishRename(c, commit) {
  if (renamingId.value !== c.id) return
  const value = renameValue.value.trim()
  renamingId.value = null
  if (commit && value && value !== c.title) {
    try {
      await api('/api/conversations/' + c.id, { method: 'PATCH', body: JSON.stringify({ title: value }) })
      await core.loadConversations()
    } catch (err) { toast(err.message) }
  }
}

async function removeConversation(id) {
  if (!window.confirm('删除这个会话？')) return
  try {
    await api('/api/conversations/' + id, { method: 'DELETE' })
    core.conversations = core.conversations.filter((c) => c.id !== id)
    if (conversationId.value === id) showHome()
  } catch (err) { toast(err.message) }
}

onMounted(() => { restoreActiveTask().catch(() => {}) })
</script>

<template>
  <div class="ws">
    <aside class="conv-drawer" :class="{ open: ui.drawerOpen }">
      <div class="brand">
        <div class="brand-mark">知</div>
        <div>
          <div class="brand-name">知记 zhiji</div>
          <div class="brand-sub">{{ auth.health }}</div>
        </div>
      </div>
      <button class="new-chat" type="button" @click="showHome"><Plus :size="16" /><span>新建笔记</span></button>
      <div class="search"><Search :size="16" /><input v-model="keyword" placeholder="搜索会话" autocomplete="off"></div>
      <nav class="conv-list">
        <div v-if="!filteredConversations.length" class="search-empty">{{ keyword ? '没有匹配的会话' : '还没有会话，点击上方「新建笔记」开始' }}</div>
        <div v-for="c in filteredConversations" :key="c.id" class="conv-item" :class="{ active: c.id === conversationId }" @click="openConversation(c.id)">
          <span v-if="c.platform" class="conv-badge">{{ PLATFORM_BADGES[c.platform] || c.platform }}</span>
          <span v-else-if="!c.has_note" class="conv-badge plain">聊</span>
          <input v-if="renamingId === c.id" :id="'rename-input-' + c.id" v-model="renameValue" class="rename-input" @click.stop @keydown.enter.prevent="finishRename(c, true)" @keydown.escape="finishRename(c, false)" @blur="finishRename(c, true)">
          <span v-else class="conv-title">{{ c.title }}</span>
          <span class="conv-count">{{ c.message_count }}</span>
          <span class="conv-actions" v-if="renamingId !== c.id">
            <button class="icon-btn" type="button" aria-label="重命名" @click.stop="startRename(c)"><Pencil :size="14" /></button>
            <button class="icon-btn danger" type="button" aria-label="删除" @click.stop="removeConversation(c.id)"><Trash2 :size="14" /></button>
          </span>
        </div>
      </nav>
      <div class="drawer-foot">
        <div class="user-mini" v-if="auth.currentUser"><span>{{ auth.currentUser.username }}</span></div>
        <div class="model-mini"><span>{{ core.model || '未配置模型' }}</span></div>
      </div>
    </aside>

    <div class="drawer-scrim" v-if="ui.drawerOpen" @click="ui.drawerOpen = false"></div>
    <main class="ws-main">
      <header class="ws-topbar">
        <button class="icon-btn" type="button" aria-label="返回首页" @click="ui.goHome()"><ArrowLeft :size="18" /></button>
        <button class="icon-btn" type="button" aria-label="会话列表 (Ctrl+K)" @click="ui.toggleDrawer()"><Menu :size="18" /></button>
        <div class="ws-title" style="min-width:0">
          <h1>{{ generating ? '正在生成笔记' : convTitle }}</h1>
          <div class="top-meta">{{ convMeta }}</div>
        </div>
        <div class="top-right"><ModelSelect @change="onModelChange" /></div>
      </header>

      <section class="scroll-area" ref="scrollRef">
        <div class="wrap">
          <template v-if="view === 'home'">
            <div class="home-hero">
              <h2 class="home-title">把视频 / 图文变成结构化笔记</h2>
              <div class="home-sub">选择平台，粘贴链接，剩下的交给知记</div>
            </div>
            <div class="platform-grid">
              <button v-for="p in core.platforms" :key="p.key" class="platform-card" :class="{ active: platform === p.key, disabled: !p.enabled }" type="button" :disabled="!p.enabled" @click="platform = p.key">
                <div class="platform-name">{{ p.name }}</div>
                <div class="platform-desc">{{ p.desc }}</div>
                <div v-if="healthWarn(p)" class="cookie-warn">{{ healthWarn(p) }}</div>
              </button>
            </div>
            <div class="url-bar">
              <input v-model="urlInput" :placeholder="selectedPlatform ? selectedPlatform.placeholder : '先选择一个平台'" :disabled="!selectedPlatform" autocomplete="off" @keydown.enter.prevent="startGeneration">
              <button type="button" :disabled="!selectedPlatform || noModel" @click="startGeneration">生成笔记</button>
            </div>
            <div class="home-tip">
              <span v-if="noModel">还没有可用模型，请先到「设置 → 模型」中添加</span>
              <span v-else-if="selectedPlatform">当前使用模型：{{ core.model || '默认模型' }}，可在右上角切换</span>
              <span v-else>点击上方卡片选择内容平台</span>
            </div>
          </template>

          <template v-else-if="generating">
            <div class="gen-panel">
              <div class="gen-panel-head">
                <span v-if="genError" style="color:var(--danger)">✗</span>
                <span v-else class="spin">◌</span>
                <span>{{ genError ? '生成失败' : '正在处理链接' }}</span>
              </div>
              <div class="gen-steps">
                <div v-for="s in genSteps" :key="s.stage" class="step" :class="s.status">
                  <span class="step-icon">
                    <template v-if="s.status === 'done'">✓</template>
                    <template v-else-if="s.status === 'error'">✗</template>
                    <template v-else-if="s.status === 'running'"><span class="spin">◌</span></template>
                    <template v-else>○</template>
                  </span>
                  <span class="step-msg">{{ s.message }}</span>
                </div>
              </div>
              <details class="gen-raw"><summary>生成过程（LLM 原始输出）</summary><pre>{{ rawText }}</pre></details>
              <div v-if="genError" class="gen-error">
                {{ genError.message || '生成失败' }}<template v-if="genError.hint"> 提示：{{ genError.hint }}</template>
                <div style="margin-top:10px"><button class="btn" type="button" @click="showHome">返回首页重新输入</button></div>
              </div>
            </div>
          </template>

          <template v-else>
            <div v-if="note" class="note-card">
              <div class="note-head">
                <span v-if="PLATFORM_BADGES[note.platform] || note.platform" class="badge">{{ PLATFORM_BADGES[note.platform] || note.platform }}</span>
                <div class="note-title">{{ note.title || '未命名笔记' }}</div>
                <div class="note-actions">
                  <button class="btn" type="button" @click="copyNote"><Copy :size="14" />复制 Markdown</button>
                  <button class="btn" type="button" @click="downloadNote"><Download :size="14" />下载 .md</button>
                  <a v-if="note.source_url" class="btn" :href="note.source_url" target="_blank" rel="noopener"><ExternalLink :size="14" />原文</a>
                </div>
              </div>
              <div v-if="warnings.length" class="warnings"><div v-for="(w, i) in warnings" :key="i">{{ w }}</div></div>
              <div class="note-body md" v-html="renderMarkdown(note.markdown)"></div>
              <div v-if="note.path" class="note-path">笔记文件：{{ note.path }}<template v-if="note.transcript_path"><br>转写文本：{{ note.transcript_path }}</template></div>
            </div>
            <div v-if="note" class="section-label">基于笔记继续对话</div>
            <div id="msg-slot">
              <div v-for="(m, i) in activeMessages" :key="i" class="msg">
                <div class="avatar" :class="m.role">{{ m.role === 'user' ? '我' : '知' }}</div>
                <div class="bubble" :class="m.role">
                  <div v-if="m.role === 'user'" class="md" style="white-space:pre-wrap">{{ m.content }}</div>
                  <div v-else class="md" v-html="renderMarkdown(m.content)"></div>
                  <div v-if="m.role === 'assistant' && m.content" class="msg-actions">
                    <button class="btn" type="button" @click="copyMessage(m.content)"><Copy :size="14" />复制</button>
                    <button v-if="note && note.path" class="btn" type="button" @click="applyAsNote(m.content)"><Check :size="14" />设为当前笔记</button>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>
      </section>

      <div class="composer-wrap" v-if="view === 'conv' && !generating">
        <div class="composer">
          <textarea id="composer-input" v-model="composerInput" :placeholder="note ? '针对笔记提问，或要求修改（例如：把核心观点压缩成三条）' : '输入消息'" rows="1" @keydown.enter.exact.prevent="sendMessage" @input="autosize"></textarea>
          <button class="send-btn" type="button" :disabled="busy" @click="sendMessage"><ArrowUp :size="18" /></button>
        </div>
      </div>
    </main>
  </div>
</template>
