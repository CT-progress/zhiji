<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  ArrowLeft, Check, CircleStop, Copy, Download, FileText, Library, LoaderCircle,
  MessageSquareText, NotebookPen, PenLine, Plus, RotateCcw, Save, Search, Sparkles,
  Trash2, X,
} from 'lucide-vue-next'
import { api, streamSSE } from '../lib/api.js'
import { useUiStore } from '../stores/ui.js'

const ui = useUiStore()
const documents = ref([])
const sources = ref([])
const stats = ref({ documents: 0, characters: 0, updated_today: 0, sources: 0 })
const loading = ref(true)
const documentLoading = ref(false)
const selectedId = ref(null)
const activeDoc = ref(null)
const searchQuery = ref('')
const searchInput = ref(null)
const titleInput = ref(null)
const contentInput = ref(null)
const mobilePane = ref('library')
const dirty = ref(false)
const saving = ref(false)
const lastSavedAt = ref(null)
const deleteArmed = ref(false)
const replaceWholeArmed = ref(false)
const deleteArmedTimer = ref(null)
const replaceWholeTimer = ref(null)

const draft = reactive({ title: '', content: '', format: 'markdown' })
const selection = reactive({ start: 0, end: 0, text: '' })
const createOpen = ref(false)
const createType = ref('blank')
const createTitle = ref('')
const createSourceId = ref(null)
const createSearch = ref('')
const creating = ref(false)
const sourcesLoading = ref(false)
const sourcesLoaded = ref(false)

const actionMeta = {
  summarize: {
    index: '01', label: '总结', short: '提炼结论与待确认问题',
    instruction: '整理这份文档的核心结论、结构化要点和需要进一步确认的问题。',
  },
  ask: {
    index: '02', label: '问答', short: '只依据当前正文回答',
    instruction: '基于当前文档回答你的问题；模型会区分文档事实与推断，并直接给出答案。',
  },
  continue: {
    index: '03', label: '续写', short: '顺着当前语气继续写',
    instruction: '顺着正文当前的思路和语气自然续写 2–4 段，不重复已有内容。',
  },
  polish: {
    index: '04', label: '润色', short: '优先处理当前选区',
    instruction: '保持事实、观点和段落结构不变，改善表达、衔接与节奏。',
  },
}
const assist = reactive({
  action: 'summarize',
  instruction: '',
  output: '',
  model: '',
  error: '',
  running: false,
  stopped: false,
  sourceContent: '',
  selectionStart: 0,
  selectionEnd: 0,
  selectionText: '',
  requestedAt: '',
})

let saveTimer = null
let savePromise = null
let editVersion = 0
let loadVersion = 0
let assistController = null
let assistRunId = 0

const filteredDocuments = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return documents.value
  return documents.value.filter((entry) =>
    [entry.title, entry.excerpt, entry.source_type].some((value) =>
      String(value || '').toLowerCase().includes(query),
    ),
  )
})

const filteredSources = computed(() => {
  const query = createSearch.value.trim().toLowerCase()
  const pool = sources.value.filter((source) =>
    createType.value === 'blank' || source.type === createType.value,
  )
  if (!query) return pool
  return pool.filter((source) =>
    [source.title, source.excerpt, source.label].some((value) =>
      String(value || '').toLowerCase().includes(query),
    ),
  )
})

const selectedSummary = computed(
  () => documents.value.find((entry) => entry.id === selectedId.value) || null,
)
const selectedNumber = computed(() => {
  const index = documents.value.findIndex((entry) => entry.id === selectedId.value)
  return String(Math.max(1, index + 1)).padStart(2, '0')
})
const characterCount = computed(() => Array.from(draft.content || '').length)
const lineCount = computed(() => (draft.content ? draft.content.split('\n').length : 1))
const pageCount = computed(() => Math.max(1, Math.ceil(characterCount.value / 1200)))
const currentAction = computed(() => actionMeta[assist.action])
const saveState = computed(() => {
  if (saving.value) return { text: '正在写入档案', cls: 'saving' }
  if (dirty.value) return { text: '有未保存修改', cls: 'dirty' }
  if (activeDoc.value) return { text: '已保存到本地', cls: 'saved' }
  return { text: '等待编辑', cls: 'idle' }
})
const activeSourceLabel = computed(() => {
  const sourceType = activeDoc.value?.source_type
  if (sourceType === 'note') return '取自笔记'
  if (sourceType === 'knowledge') return '取自知识库'
  return '独立文档'
})

function formatNumber(value) {
  return new Intl.NumberFormat('zh-CN').format(Number(value || 0))
}

function updatedLabel(value) {
  if (!value) return '尚未保存'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '刚刚更新'
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return `今天 ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  }
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

function docNumber(entry) {
  const index = documents.value.findIndex((item) => item.id === entry.id)
  return String(Math.max(1, index + 1)).padStart(2, '0')
}

function sourceMark(entry) {
  if (entry.source_type === 'note') return 'NOTE'
  if (entry.source_type === 'knowledge') return 'KB'
  return 'DRAFT'
}

function summaryFromDoc(doc) {
  const { content, ...summary } = doc
  return summary
}

function replaceSummary(doc) {
  const summary = summaryFromDoc(doc)
  const index = documents.value.findIndex((entry) => entry.id === doc.id)
  if (index >= 0) documents.value[index] = summary
  else documents.value.unshift(summary)
  documents.value.sort((a, b) =>
    String(b.updated_at || '').localeCompare(String(a.updated_at || '')),
  )
}

function applyDocument(doc) {
  activeDoc.value = doc
  selectedId.value = doc.id
  Object.assign(draft, {
    title: doc.title || '',
    content: doc.content || '',
    format: doc.format || 'markdown',
  })
  dirty.value = false
  lastSavedAt.value = doc.updated_at || null
  assist.output = ''
  assist.error = ''
  assist.model = ''
  assist.stopped = false
  assist.selectionStart = 0
  assist.selectionEnd = 0
  assist.selectionText = ''
  selection.start = 0
  selection.end = 0
  selection.text = ''
  editVersion += 1
  clearDeleteArm()
  clearReplaceWholeArm()
  nextTick(() => {
    resizeEditor()
    captureSelection()
  })
}

function resetEditor() {
  activeDoc.value = null
  selectedId.value = null
  Object.assign(draft, { title: '', content: '', format: 'markdown' })
  dirty.value = false
  lastSavedAt.value = null
  assist.output = ''
  assist.error = ''
}

async function refreshStats() {
  try {
    const data = await api('/api/docs/stats')
    stats.value = data.stats || stats.value
  } catch {
    // 首页与工作区均可独立工作，统计刷新失败不阻断编辑。
  }
}

async function loadAll() {
  loading.value = true
  try {
    const [listData, statsData] = await Promise.all([
      api('/api/docs'),
      api('/api/docs/stats'),
    ])
    documents.value = listData.documents || []
    stats.value = statsData.stats || stats.value
    const first = documents.value[0]
    if (first) await openDocument(first, { force: true, openMobile: false })
    mobilePane.value = 'library'
  } catch (error) {
    ui.showToast('载入文档失败：' + error.message)
  } finally {
    loading.value = false
  }
}

async function openDocument(entry, { force = false, openMobile = true } = {}) {
  if (!entry) return
  if (!force && selectedId.value === entry.id) {
    if (openMobile) mobilePane.value = 'editor'
    return
  }
  if (!(await flushSave())) return
  const token = ++loadVersion
  documentLoading.value = true
  try {
    const data = await api(`/api/docs/${encodeURIComponent(entry.id)}`)
    if (token !== loadVersion) return
    applyDocument(data.document)
    if (openMobile) mobilePane.value = 'editor'
  } catch (error) {
    ui.showToast('打开文档失败：' + error.message)
  } finally {
    if (token === loadVersion) documentLoading.value = false
  }
}

function queueSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveTimer = null
    saveCurrent({ silent: true })
  }, 700)
}

function markDirty() {
  if (!selectedId.value) return
  dirty.value = true
  editVersion += 1
  clearDeleteArm()
  queueSave()
}

async function saveCurrent({ silent = false, keepalive = false } = {}) {
  if (!selectedId.value || !dirty.value) return true
  if (savePromise) {
    await savePromise
    if (dirty.value) return saveCurrent({ silent, keepalive })
    return true
  }
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }

  const docId = selectedId.value
  const version = editVersion
  const payload = {
    title: draft.title.trim() || '无标题文档',
    content: draft.content,
    format: draft.format,
  }
  saving.value = true
  savePromise = (async () => {
    try {
      const data = await api(`/api/docs/${encodeURIComponent(docId)}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
        keepalive,
      })
      replaceSummary(data.document)
      if (selectedId.value === docId) {
        activeDoc.value = data.document
        lastSavedAt.value = data.document.updated_at
        if (version === editVersion) {
          dirty.value = false
          draft.title = data.document.title || ''
          if (!silent) ui.showToast('文档已保存')
        }
        refreshStats()
      }
      return true
    } catch (error) {
      ui.showToast('保存失败：' + error.message)
      return false
    } finally {
      saving.value = false
      savePromise = null
      if (dirty.value && selectedId.value === docId) queueSave()
    }
  })()
  return savePromise
}

async function flushSave() {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  if (savePromise) await savePromise
  for (let attempt = 0; attempt < 3 && dirty.value; attempt += 1) {
    await saveCurrent({ silent: true })
  }
  return !dirty.value
}
function resizeEditor() {
  const element = contentInput.value
  if (!element) return
  element.style.height = 'auto'
  element.style.height = `${Math.max(440, element.scrollHeight)}px`
}

function captureSelection() {
  const element = contentInput.value
  if (!element) return
  selection.start = element.selectionStart || 0
  selection.end = element.selectionEnd || 0
  selection.text = draft.content.slice(selection.start, selection.end)
}

function focusEditor(position) {
  nextTick(() => {
    const element = contentInput.value
    if (!element) return
    element.focus()
    const caret = Math.max(0, Math.min(Number(position || 0), draft.content.length))
    element.setSelectionRange(caret, caret)
    captureSelection()
    resizeEditor()
  })
}

function applyContent(nextContent, caret) {
  draft.content = nextContent
  markDirty()
  nextTick(() => {
    resizeEditor()
    focusEditor(caret)
  })
}

function clearDeleteArm() {
  if (deleteArmedTimer.value) clearTimeout(deleteArmedTimer.value)
  deleteArmedTimer.value = null
  deleteArmed.value = false
}

function clearReplaceWholeArm() {
  if (replaceWholeTimer.value) clearTimeout(replaceWholeTimer.value)
  replaceWholeTimer.value = null
  replaceWholeArmed.value = false
}

async function removeCurrentDocument() {
  if (!selectedId.value) return
  if (!deleteArmed.value) {
    deleteArmed.value = true
    deleteArmedTimer.value = setTimeout(clearDeleteArm, 3200)
    return
  }
  if (!(await flushSave())) return
  const docId = selectedId.value
  try {
    await api(`/api/docs/${encodeURIComponent(docId)}`, { method: 'DELETE' })
    documents.value = documents.value.filter((entry) => entry.id !== docId)
    clearDeleteArm()
    resetEditor()
    const next = documents.value[0]
    if (next) await openDocument(next, { force: true, openMobile: false })
    mobilePane.value = 'library'
    await refreshStats()
    ui.showToast('文档已删除')
  } catch (error) {
    ui.showToast('删除失败：' + error.message)
  }
}

async function exportCurrentDocument() {
  if (!selectedId.value) return
  if (!(await flushSave())) return
  const anchor = window.document.createElement('a')
  anchor.href = `/api/docs/${encodeURIComponent(selectedId.value)}/export`
  anchor.download = ''
  window.document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  ui.showToast(draft.format === 'markdown' ? '已导出 Markdown' : '已导出纯文本')
}

async function loadSources() {
  if (sourcesLoaded.value || sourcesLoading.value) return
  sourcesLoading.value = true
  try {
    const data = await api('/api/docs/sources')
    sources.value = data.sources || []
    sourcesLoaded.value = true
  } catch (error) {
    ui.showToast('载入素材失败：' + error.message)
  } finally {
    sourcesLoading.value = false
  }
}

async function openCreate(type = 'blank') {
  if (!(await flushSave())) return
  createType.value = type
  createTitle.value = ''
  createSourceId.value = null
  createSearch.value = ''
  createOpen.value = true
  if (type !== 'blank') loadSources()
}

function setCreateType(type) {
  createType.value = type
  createSourceId.value = null
  createSearch.value = ''
  if (type !== 'blank') loadSources()
}

function chooseSource(source) {
  createSourceId.value = source.id
  if (!createTitle.value.trim()) createTitle.value = source.title || ''
}

async function createDocument() {
  if (creating.value) return
  if (createType.value !== 'blank' && !createSourceId.value) {
    ui.showToast('请选择一份素材')
    return
  }
  creating.value = true
  try {
    const payload = {
      title: createTitle.value.trim() || undefined,
      format: 'markdown',
      source_type: createType.value,
      source_id: createType.value === 'blank' ? null : createSourceId.value,
    }
    const data = await api('/api/docs', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    replaceSummary(data.document)
    selectedId.value = data.document.id
    applyDocument(data.document)
    createOpen.value = false
    mobilePane.value = 'editor'
    await refreshStats()
    await nextTick()
    titleInput.value?.focus()
    titleInput.value?.select()
    ui.showToast(createType.value === 'blank' ? '已新建空白文档' : '素材已进入写作台')
  } catch (error) {
    ui.showToast('新建失败：' + error.message)
  } finally {
    creating.value = false
  }
}

function setAction(action) {
  assist.action = action
  assist.error = ''
  if (action !== 'ask') assist.instruction = assist.instruction.trim()
}

async function runAssist(action = assist.action) {
  if (!selectedId.value) {
    ui.showToast('请先新建或打开一篇文档')
    return
  }
  if (!draft.content.trim()) {
    ui.showToast('文档还是空的，请先写一点内容')
    return
  }
  if (assist.running) stopAssist()
  if (action === 'ask' && !assist.instruction.trim()) {
    ui.showToast('请先输入要问文档的问题')
    return
  }

  selectAction(action)
  captureSelection()
  const sourceContent = draft.content
  const initialSelection = selection.text.trim() ? { ...selection } : null
  assist.sourceContent = sourceContent
  assist.selectionStart = initialSelection?.start || 0
  assist.selectionEnd = initialSelection?.end || 0
  assist.selectionText = initialSelection?.text || ''
  const runId = ++assistRunId
  assist.output = ''
  assist.model = ''
  assist.error = ''
  assist.stopped = false
  assist.running = true
  assist.requestedAt = new Date().toISOString()

  const controller = new AbortController()
  assistController = controller
  const payload = {
    action,
    instruction: assist.instruction.trim(),
    content: sourceContent,
    selection: action === 'polish' ? (initialSelection?.text || '') : '',
  }

  try {
    await streamSSE(
      `/api/docs/${encodeURIComponent(selectedId.value)}/assist/stream`,
      payload,
      (event) => {
        if (runId !== assistRunId) return
        if (event.meta) assist.model = event.meta.model || ''
        if (typeof event.delta === 'string') assist.output += event.delta
        if (event.error) assist.error = event.error.message || '模型调用失败'
      },
      { signal: controller.signal },
    )
  } catch (error) {
    if (runId !== assistRunId) return
    if (error?.name === 'AbortError' || controller.signal.aborted) {
      assist.stopped = true
    } else {
      assist.error = error?.message || '生成失败，请重试'
    }
  } finally {
    if (runId !== assistRunId) return
    if (assistController === controller) assistController = null
    assist.running = false
  }
}

function selectAction(action) {
  assist.action = action
}

function stopAssist() {
  if (!assist.running) return
  assistController?.abort()
  assist.stopped = true
}

function insertAssistOutput() {
  if (!assist.output.trim()) return
  const caret = contentInput.value?.selectionStart ?? selection.start
  const next = draft.content.slice(0, caret) + assist.output + draft.content.slice(caret)
  applyContent(next, caret + assist.output.length)
  ui.showToast('已插入光标位置')
}

function replaceAssistSelection() {
  if (!assist.output.trim()) return
  if (draft.content !== assist.sourceContent) {
    ui.showToast('正文在生成期间已变化，请复制结果或重新生成')
    return
  }
  if (!assist.selectionText || assist.selectionEnd <= assist.selectionStart) {
    ui.showToast('生成前没有选中文本，请改为插入或替换全文')
    return
  }
  const next =
    draft.content.slice(0, assist.selectionStart) +
    assist.output +
    draft.content.slice(assist.selectionEnd)
  applyContent(next, assist.selectionStart + assist.output.length)
  ui.showToast('已替换生成前的选区')
}

function replaceAssistWhole() {
  if (!assist.output.trim()) return
  if (draft.content !== assist.sourceContent) {
    ui.showToast('正文在生成期间已变化，请复制结果或重新生成')
    return
  }
  if (!replaceWholeArmed.value) {
    replaceWholeArmed.value = true
    replaceWholeTimer.value = setTimeout(clearReplaceWholeArm, 3200)
    return
  }
  clearReplaceWholeArm()
  applyContent(assist.output, assist.output.length)
  ui.showToast('已用生成结果替换全文')
}

async function copyAssistOutput() {
  if (!assist.output) return
  try {
    await navigator.clipboard.writeText(assist.output)
    ui.showToast('已复制生成结果')
  } catch {
    const fallback = window.document.createElement('textarea')
    fallback.value = assist.output
    fallback.style.position = 'fixed'
    fallback.style.left = '-9999px'
    window.document.body.appendChild(fallback)
    fallback.select()
    const copied = window.document.execCommand('copy')
    fallback.remove()
    ui.showToast(copied ? '已复制生成结果' : '复制失败，请手动选择')
  }
}

function isTypingTarget(target) {
  return target instanceof HTMLElement &&
    (target.matches('input, textarea, select') || target.isContentEditable)
}

function onKeydown(event) {
  if (createOpen.value && event.key === 'Escape') {
    event.preventDefault()
    event.stopImmediatePropagation()
    createOpen.value = false
    return
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault()
    saveCurrent()
    return
  }
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault()
    runAssist(assist.action)
    return
  }
  if (event.altKey && ['1', '2', '3'].includes(event.key)) {
    event.preventDefault()
    mobilePane.value = ['library', 'editor', 'assist'][Number(event.key) - 1]
    return
  }
  if (event.key === '/' && !isTypingTarget(event.target)) {
    event.preventDefault()
    mobilePane.value = 'library'
    nextTick(() => searchInput.value?.focus())
  }
}

function onBeforeUnload() {
  if (dirty.value && selectedId.value) saveCurrent({ silent: true, keepalive: true })
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown, true)
  window.addEventListener('beforeunload', onBeforeUnload)
  loadAll()
})

onBeforeUnmount(() => {
  assistController?.abort()
  if (saveTimer) clearTimeout(saveTimer)
  if (deleteArmedTimer.value) clearTimeout(deleteArmedTimer.value)
  if (replaceWholeTimer.value) clearTimeout(replaceWholeTimer.value)
  window.removeEventListener('keydown', onKeydown, true)
  window.removeEventListener('beforeunload', onBeforeUnload)
  if (dirty.value && selectedId.value) saveCurrent({ silent: true, keepalive: true })
})
</script>
<template>
  <div class="docs-ws" :data-pane="mobilePane">
    <header class="docs-topbar">
      <button class="docs-back" type="button" aria-label="返回首页" @click="ui.goHome()">
        <ArrowLeft :size="17" />
      </button>
      <div class="docs-title-group">
        <span class="docs-eyebrow">WORKSPACE 04 / WRITING DESK</span>
        <h1>{{ activeDoc?.title || '文档助手' }}</h1>
      </div>
      <div class="docs-top-metrics" aria-label="文档统计">
        <span><b>{{ stats.documents }}</b> 文档</span>
        <span><b>{{ formatNumber(stats.characters) }}</b> 字符</span>
        <span><b>{{ stats.updated_today }}</b> 今日更新</span>
      </div>
      <div class="docs-top-actions">
        <span class="docs-save-state" :class="saveState.cls"><i></i>{{ saveState.text }}</span>
        <button class="docs-icon-btn" type="button" title="保存 (Ctrl+S)" :disabled="!activeDoc || saving" @click="saveCurrent()">
          <Save :size="16" />
        </button>
        <button class="docs-icon-btn" type="button" title="导出文档" :disabled="!activeDoc" @click="exportCurrentDocument">
          <Download :size="16" />
        </button>
        <button class="docs-icon-btn danger" :class="{ armed: deleteArmed }" type="button" :disabled="!activeDoc" :title="deleteArmed ? '再次点击确认删除' : '删除文档'" @click="removeCurrentDocument">
          <Trash2 :size="16" />
          <span v-if="deleteArmed">再次确认</span>
        </button>
      </div>
    </header>

    <nav class="docs-mobile-nav" aria-label="文档工作区页面">
      <button type="button" :class="{ active: mobilePane === 'library' }" @click="mobilePane = 'library'">
        <span>01</span>文档 <small>{{ documents.length }}</small>
      </button>
      <button type="button" :class="{ active: mobilePane === 'editor' }" @click="mobilePane = 'editor'">
        <span>02</span>编辑
      </button>
      <button type="button" :class="{ active: mobilePane === 'assist' }" @click="mobilePane = 'assist'">
        <span>03</span>AI
      </button>
    </nav>

    <div class="docs-body">
      <aside class="docs-register">
        <div class="docs-pane-head">
          <div>
            <span class="docs-kicker">DOCUMENT REGISTER</span>
            <h2>写作目录</h2>
          </div>
          <button class="docs-new-btn" type="button" title="新建文档" @click="openCreate('blank')">
            <Plus :size="16" />
          </button>
        </div>

        <label class="docs-search">
          <Search :size="14" />
          <input ref="searchInput" v-model="searchQuery" type="search" maxlength="120" placeholder="搜索标题或正文摘要" aria-label="搜索文档">
          <kbd>/</kbd>
        </label>

        <div class="docs-register-list">
          <div v-if="loading" class="docs-list-state"><LoaderCircle class="spin" :size="16" />正在打开档案柜</div>
          <button
            v-for="entry in filteredDocuments"
            :key="entry.id"
            class="docs-entry"
            :class="{ active: selectedId === entry.id }"
            type="button"
            @click="openDocument(entry)"
          >
            <span class="docs-entry-number">{{ docNumber(entry) }}</span>
            <span class="docs-entry-body">
              <span class="docs-entry-line">
                <strong>{{ entry.title || '无标题文档' }}</strong>
                <small>{{ sourceMark(entry) }}</small>
              </span>
              <span class="docs-entry-excerpt">{{ entry.excerpt || '空白文档，等待第一句话。' }}</span>
              <span class="docs-entry-meta">
                <span>{{ formatNumber(entry.characters) }} 字</span>
                <span>{{ updatedLabel(entry.updated_at) }}</span>
              </span>
            </span>
          </button>
          <div v-if="!loading && !filteredDocuments.length" class="docs-list-empty">
            <FileText :size="22" />
            <strong>{{ searchQuery ? '没有匹配的文档' : '档案柜还是空的' }}</strong>
            <span>{{ searchQuery ? '换一个关键词，或清空搜索。' : '从空白页或既有素材开始。' }}</span>
            <button v-if="!searchQuery" type="button" @click="openCreate('blank')">建立第一篇文档</button>
          </div>
        </div>

        <div class="docs-register-foot">
          <div class="docs-register-rule"><span>ARCHIVE STATUS</span><i></i></div>
          <dl>
            <div><dt>文档</dt><dd>{{ stats.documents }}</dd></div>
            <div><dt>取材</dt><dd>{{ stats.sources }}</dd></div>
            <div><dt>字符</dt><dd>{{ formatNumber(stats.characters) }}</dd></div>
            <div><dt>今日</dt><dd>{{ stats.updated_today }}</dd></div>
          </dl>
          <div class="docs-source-shortcuts">
            <button type="button" @click="openCreate('note')"><NotebookPen :size="13" />从笔记取材</button>
            <button type="button" @click="openCreate('knowledge')"><Library :size="13" />从知识库取材</button>
          </div>
        </div>
      </aside>

      <main class="docs-editor-pane">
        <div v-if="documentLoading" class="docs-editor-loading">
          <LoaderCircle class="spin" :size="20" />
          <span>正在铺开稿纸</span>
        </div>

        <div v-else-if="activeDoc" class="docs-editor-shell">
          <div class="docs-editor-caption">
            <div>
              <span class="docs-kicker">DRAFT {{ selectedNumber }} / {{ activeSourceLabel }}</span>
              <span class="docs-format-pill">{{ draft.format === 'markdown' ? 'MARKDOWN' : 'PLAIN TEXT' }}</span>
            </div>
            <div class="docs-editor-caption-meta">
              <span>PAGE {{ String(pageCount).padStart(2, '0') }}</span>
              <span>{{ lineCount }} 行</span>
              <span>{{ formatNumber(characterCount) }} 字符</span>
            </div>
          </div>

          <div class="docs-paper-scroll">
            <article class="docs-paper">
              <span class="docs-paper-cut" aria-hidden="true"></span>
              <header class="docs-paper-head">
                <span>ZHIJI / WRITING ARCHIVE</span>
                <span>{{ updatedLabel(lastSavedAt) }}</span>
              </header>

              <input
                ref="titleInput"
                v-model="draft.title"
                class="docs-paper-title"
                type="text"
                maxlength="240"
                placeholder="无标题文档"
                aria-label="文档标题"
                @input="markDirty"
              >

              <div class="docs-paper-meta">
                <span>{{ sourceMark(activeDoc) }} · {{ activeSourceLabel }}</span>
                <span>{{ new Date().getFullYear() }} / LOCAL DRAFT</span>
              </div>

              <textarea
                ref="contentInput"
                v-model="draft.content"
                class="docs-paper-content"
                placeholder="从这里开始写。可以让左侧素材先落下来，也可以直接写下第一句话……"
                spellcheck="false"
                aria-label="文档正文"
                @input="markDirty(); resizeEditor()"
                @select="captureSelection"
                @keyup="captureSelection"
                @mouseup="captureSelection"
                @blur="captureSelection"
              ></textarea>

              <footer class="docs-paper-foot">
                <span>ZHIJI WORKBENCH · DOCUMENT {{ selectedNumber }}</span>
                <span>{{ String(pageCount).padStart(2, '0') }}</span>
              </footer>
            </article>
          </div>

          <footer class="docs-editor-status">
            <span class="docs-status-left"><i :class="saveState.cls"></i>{{ saveState.text }}</span>
            <span class="docs-status-spacer"></span>
            <span>Ctrl+S 保存</span>
            <span>Ctrl+Enter 发起 AI</span>
            <span>{{ updatedLabel(lastSavedAt) }}</span>
          </footer>
        </div>

        <div v-else class="docs-editor-empty">
          <div class="docs-empty-sheet" aria-hidden="true">
            <span>01</span>
            <PenLine :size="28" />
            <i></i><i></i><i></i>
          </div>
          <span class="docs-kicker">NO DRAFT SELECTED</span>
          <h2>先铺开一张写作台</h2>
          <p>空白文档适合从零起草；笔记和知识库素材会带着原文直接进入稿纸。</p>
          <div class="docs-empty-actions">
            <button type="button" class="primary" @click="openCreate('blank')"><Plus :size="15" />空白文档</button>
            <button type="button" @click="openCreate('note')"><NotebookPen :size="15" />从笔记取材</button>
            <button type="button" @click="openCreate('knowledge')"><Library :size="15" />从知识库取材</button>
          </div>
        </div>
      </main>
      <aside class="docs-assist-rail">
        <div class="docs-assist-head">
          <div>
            <span class="docs-kicker">CO-WRITING CONSOLE</span>
            <h2>协作操作轨</h2>
          </div>
          <span class="docs-ai-live" :class="{ busy: assist.running }"><i></i>{{ assist.running ? 'STREAMING' : 'READY' }}</span>
        </div>

        <div class="docs-action-grid">
          <button
            v-for="(meta, key) in actionMeta"
            :key="key"
            type="button"
            :class="{ active: assist.action === key }"
            :disabled="!activeDoc"
            @click="setAction(key)"
          >
            <span>{{ meta.index }}</span>
            <strong>{{ meta.label }}</strong>
            <small>{{ meta.short }}</small>
          </button>
        </div>

        <div class="docs-assist-brief">
          <div class="docs-brief-label">
            <Sparkles :size="13" />
            <span>{{ currentAction.label }}任务</span>
            <small>{{ currentAction.index }}</small>
          </div>
          <p>{{ currentAction.instruction }}</p>

          <label v-if="assist.action === 'ask'" class="docs-instruction">
            <span>要问文档的问题 <b>必填</b></span>
            <textarea v-model="assist.instruction" rows="3" maxlength="4000" placeholder="例如：这份方案最关键的三个风险是什么？"></textarea>
          </label>
          <label v-else-if="assist.action === 'continue'" class="docs-instruction">
            <span>续写方向 <b>可选</b></span>
            <textarea v-model="assist.instruction" rows="2" maxlength="4000" placeholder="例如：继续写到下一阶段，但不要给出最终结论。"></textarea>
          </label>
          <label v-else-if="assist.action === 'polish'" class="docs-instruction">
            <span>润色要求 <b>可选</b></span>
            <textarea v-model="assist.instruction" rows="2" maxlength="4000" placeholder="例如：更克制，删掉重复表达。"></textarea>
            <small class="docs-selection-hint">
              <template v-if="selection.text">{{ selection.text.length }} 字选区将被优先润色</template>
              <template v-else>未选中文本时，将润色当前全文</template>
            </small>
          </label>
          <div v-else class="docs-summary-brief">
            <span>输出结构</span>
            <div><b>01</b>一句话结论</div>
            <div><b>02</b>3–6 个要点</div>
            <div><b>03</b>待确认问题</div>
          </div>
        </div>

        <div class="docs-assist-output">
          <div class="docs-output-head">
            <span><MessageSquareText :size="13" />生成结果</span>
            <span v-if="assist.model" class="docs-model-name">{{ assist.model }}</span>
            <span v-else class="docs-model-name">DRAFT-AWARE</span>
          </div>

          <div v-if="assist.running && !assist.output" class="docs-thinking">
            <span><i></i><i></i><i></i></span>
            <strong>正在读取正文并组织结果</strong>
            <small>当前未落盘内容也会一并送入模型</small>
          </div>
          <div v-else-if="assist.output" class="docs-output-body">
            <pre>{{ assist.output }}</pre>
            <span v-if="assist.running" class="docs-cursor"></span>
          </div>
          <div v-else-if="assist.error" class="docs-output-error">
            <strong>生成中断</strong>
            <p>{{ assist.error }}</p>
            <button type="button" @click="runAssist(assist.action)">重试</button>
          </div>
          <div v-else class="docs-output-empty">
            <Sparkles :size="20" />
            <strong>选择一项协作动作</strong>
            <p>总结、问答、续写或润色。结果会停在操作台，由你决定是否写回正文。</p>
          </div>

          <div v-if="assist.stopped && !assist.running" class="docs-stopped-note">已停止接收新的生成内容</div>
        </div>

        <div class="docs-assist-actions">
          <button v-if="assist.running" class="docs-run stop" type="button" @click="stopAssist">
            <CircleStop :size="15" />停止生成
          </button>
          <button v-else class="docs-run" type="button" :disabled="!activeDoc" @click="runAssist(assist.action)">
            <Sparkles :size="15" />执行 {{ currentAction.label }}
            <kbd>Ctrl ↵</kbd>
          </button>

          <div v-if="assist.output && !assist.running" class="docs-output-actions">
            <button type="button" @click="insertAssistOutput"><Plus :size="12" />插入光标</button>
            <button type="button" :disabled="!assist.selectionText" @click="replaceAssistSelection"><Check :size="12" />替换选区</button>
            <button type="button" :class="{ armed: replaceWholeArmed }" @click="replaceAssistWhole"><RotateCcw :size="12" />{{ replaceWholeArmed ? '再次确认' : '替换全文' }}</button>
            <button type="button" @click="copyAssistOutput"><Copy :size="12" />复制</button>
            <button type="button" @click="runAssist(assist.action)"><RotateCcw :size="12" />重试</button>
          </div>
        </div>

        <footer class="docs-assist-foot">
          <span>{{ activeSourceLabel }}</span>
          <span>{{ formatNumber(stats.sources) }} 次取材</span>
          <span>Alt+3 打开</span>
        </footer>
      </aside>
    </div>

    <div v-if="createOpen" class="docs-modal-layer" @click.self="createOpen = false">
      <section class="docs-modal" role="dialog" aria-modal="true" aria-labelledby="docs-create-title">
        <header class="docs-modal-head">
          <div>
            <span class="docs-kicker">NEW WRITING TASK</span>
            <h2 id="docs-create-title">选择写作起点</h2>
          </div>
          <button type="button" aria-label="关闭" @click="createOpen = false"><X :size="17" /></button>
        </header>

        <div class="docs-create-types">
          <button type="button" :class="{ active: createType === 'blank' }" @click="setCreateType('blank')">
            <FileText :size="18" /><strong>空白文档</strong><small>从第一句话开始</small>
          </button>
          <button type="button" :class="{ active: createType === 'note' }" @click="setCreateType('note')">
            <NotebookPen :size="18" /><strong>取自笔记</strong><small>带着已有想法起草</small>
          </button>
          <button type="button" :class="{ active: createType === 'knowledge' }" @click="setCreateType('knowledge')">
            <Library :size="18" /><strong>取自知识库</strong><small>把资料铺进稿纸</small>
          </button>
        </div>

        <label class="docs-create-title">
          <span>文档标题 <small>留空会沿用素材标题</small></span>
          <input v-model="createTitle" type="text" maxlength="240" placeholder="给它一个暂定名字">
        </label>

        <template v-if="createType !== 'blank'">
          <label class="docs-source-search">
            <Search :size="14" />
            <input v-model="createSearch" type="search" maxlength="120" :placeholder="createType === 'note' ? '搜索笔记' : '搜索知识库资料'">
          </label>

          <div class="docs-source-list">
            <button
              v-for="source in filteredSources"
              :key="source.type + source.id"
              type="button"
              :class="{ active: createSourceId === source.id }"
              @click="chooseSource(source)"
            >
              <span class="docs-source-type">{{ source.type === 'note' ? 'NOTE' : 'KB' }}</span>
              <span class="docs-source-copy">
                <strong>{{ source.title || '无标题素材' }}</strong>
                <small>{{ source.excerpt || '空素材' }}</small>
              </span>
              <span class="docs-source-check"><Check v-if="createSourceId === source.id" :size="13" /></span>
            </button>
            <div v-if="sourcesLoading" class="docs-source-state"><LoaderCircle class="spin" :size="15" />正在读取素材索引</div>
            <div v-else-if="!filteredSources.length" class="docs-source-state">
              没有可用{{ createType === 'note' ? '笔记' : '知识库资料' }}，先去对应工作区建立素材。
            </div>
          </div>
        </template>

        <footer class="docs-modal-foot">
          <span>{{ createType === 'blank' ? '将建立一份独立 Markdown 文档' : '原文会复制到新文档，不影响素材本身' }}</span>
          <button type="button" @click="createOpen = false">取消</button>
          <button type="button" class="primary" :disabled="creating || (createType !== 'blank' && !createSourceId)" @click="createDocument">
            <LoaderCircle v-if="creating" class="spin" :size="14" />
            <Plus v-else :size="14" />{{ creating ? '正在建立' : '铺开稿纸' }}
          </button>
        </footer>
      </section>
    </div>
  </div>
</template>
<style scoped>
.docs-ws {
  --docs-ink: #292721;
  --docs-paper: #e8e1d4;
  --docs-paper-deep: #d8cebd;
  --docs-rule: rgba(62, 55, 43, 0.12);
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background:
    radial-gradient(circle at 72% 18%, rgba(91, 141, 239, 0.045), transparent 28%),
    #151616;
}

.docs-topbar {
  flex: none;
  height: 58px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-soft);
  background: rgba(18, 19, 19, 0.96);
  position: relative;
  z-index: 4;
}
.docs-back {
  width: 31px;
  height: 31px;
  flex: none;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--muted);
  transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
}
.docs-back:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
.docs-title-group { min-width: 0; display: flex; flex-direction: column; justify-content: center; }
.docs-eyebrow,
.docs-kicker {
  color: var(--faint);
  font: 9px/1.3 Consolas, "Courier New", monospace;
  letter-spacing: 0.13em;
  text-transform: uppercase;
}
.docs-title-group h1 {
  margin: 2px 0 0;
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  line-height: 1.2;
  font-weight: 650;
  color: var(--text);
}
.docs-top-metrics {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-left: 18px;
  padding-left: 18px;
  border-left: 1px solid var(--border-soft);
  color: var(--faint);
  font-size: 10px;
}
.docs-top-metrics span { display: flex; align-items: baseline; gap: 4px; white-space: nowrap; }
.docs-top-metrics b { color: var(--muted); font: 11px/1 Consolas, "Courier New", monospace; font-weight: 500; }
.docs-top-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.docs-save-state {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 6px;
  color: var(--faint);
  font-size: 10px;
  white-space: nowrap;
}
.docs-save-state i { width: 5px; height: 5px; border-radius: 50%; background: var(--faint); }
.docs-save-state.saved i { background: var(--accent); }
.docs-save-state.saving i { background: var(--warn); animation: docs-pulse 1s infinite; }
.docs-save-state.dirty i { background: var(--warn); }
.docs-icon-btn {
  min-width: 31px;
  height: 31px;
  padding: 0 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--muted);
  background: transparent;
  font-size: 10px;
  transition: background var(--t-fast), color var(--t-fast), border-color var(--t-fast);
}
.docs-icon-btn:hover:not(:disabled) { background: var(--surface); color: var(--text); border-color: #474747; }
.docs-icon-btn:disabled { opacity: 0.35; cursor: default; }
.docs-icon-btn.danger:hover:not(:disabled),
.docs-icon-btn.danger.armed { border-color: rgba(239, 68, 68, 0.6); color: #ff7777; background: rgba(239, 68, 68, 0.09); }
.docs-mobile-nav { display: none; }

.docs-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 246px minmax(0, 1fr) 312px;
  gap: 10px;
  padding: 10px 12px calc(var(--dock-h) + 12px);
  overflow: hidden;
}

.docs-register,
.docs-editor-pane,
.docs-assist-rail {
  min-width: 0;
  min-height: 0;
  border: 1px solid var(--border-soft);
  background: var(--panel);
  overflow: hidden;
}
.docs-register { display: flex; flex-direction: column; border-radius: 6px; }
.docs-pane-head,
.docs-assist-head {
  flex: none;
  min-height: 63px;
  padding: 13px 13px 11px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border-bottom: 1px solid var(--border-soft);
}
.docs-pane-head h2,
.docs-assist-head h2 { margin: 3px 0 0; font-size: 14px; font-weight: 650; line-height: 1.2; }
.docs-new-btn {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--accent);
  background: var(--accent-soft);
  transition: background var(--t-fast), border-color var(--t-fast);
}
.docs-new-btn:hover { background: rgba(16, 163, 127, 0.22); border-color: var(--accent); }
.docs-search,
.docs-source-search {
  flex: none;
  margin: 10px;
  height: 34px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 9px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: #191a1a;
  color: var(--faint);
}
.docs-search:focus-within,
.docs-source-search:focus-within { border-color: #45675d; color: var(--accent); }
.docs-search input,
.docs-source-search input { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; font-size: 11px; color: var(--text); }
.docs-search input::placeholder,
.docs-source-search input::placeholder { color: var(--faint); }
.docs-search kbd,
.docs-run kbd {
  padding: 1px 4px;
  border: 1px solid var(--border);
  border-radius: 3px;
  color: var(--faint);
  font: 9px/1.2 Consolas, "Courier New", monospace;
}
.docs-register-list { flex: 1; min-height: 0; overflow: auto; padding: 0 6px 8px; }
.docs-entry {
  width: 100%;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: 6px;
  padding: 11px 7px 11px 5px;
  text-align: left;
  border-left: 2px solid transparent;
  border-bottom: 1px solid var(--border-soft);
  color: var(--muted);
  transition: background var(--t-fast), border-color var(--t-fast), color var(--t-fast);
}
.docs-entry:hover { background: #1c1d1d; color: var(--text); }
.docs-entry.active { border-left-color: var(--accent); background: linear-gradient(90deg, rgba(16, 163, 127, 0.1), transparent 78%); color: var(--text); }
.docs-entry-number { padding-top: 1px; color: var(--faint); font: 10px/1.4 Consolas, "Courier New", monospace; }
.docs-entry.active .docs-entry-number { color: var(--accent); }
.docs-entry-body { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.docs-entry-line { display: flex; align-items: center; gap: 6px; min-width: 0; }
.docs-entry-line strong { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; font-weight: 600; }
.docs-entry-line small { color: var(--faint); font: 7px/1 Consolas, "Courier New", monospace; letter-spacing: 0.08em; }
.docs-entry.active .docs-entry-line small { color: var(--info); }
.docs-entry-excerpt { display: -webkit-box; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; color: var(--faint); font-size: 10px; line-height: 1.5; }
.docs-entry-meta { display: flex; justify-content: space-between; gap: 5px; color: #595959; font: 8px/1.3 Consolas, "Courier New", monospace; }
.docs-list-state,
.docs-source-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 28px 10px;
  color: var(--faint);
  font-size: 10px;
  text-align: center;
}
.docs-list-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
  padding: 44px 12px;
  color: var(--faint);
  text-align: center;
}
.docs-list-empty strong { color: var(--muted); font-size: 12px; }
.docs-list-empty span { max-width: 150px; font-size: 10px; line-height: 1.55; }
.docs-list-empty button { margin-top: 4px; color: var(--accent); font-size: 10px; }
.docs-register-foot { flex: none; padding: 10px; border-top: 1px solid var(--border-soft); background: #131414; }
.docs-register-rule { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; color: var(--faint); font: 8px/1 Consolas, "Courier New", monospace; letter-spacing: 0.1em; }
.docs-register-rule i { flex: 1; height: 1px; background: var(--border-soft); }
.docs-register-foot dl { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin: 0 0 9px; }
.docs-register-foot dl div { padding: 5px 4px; border: 1px solid var(--border-soft); border-radius: 3px; text-align: center; }
.docs-register-foot dt { color: var(--faint); font-size: 8px; }
.docs-register-foot dd { margin: 2px 0 0; color: var(--muted); font: 10px/1.2 Consolas, "Courier New", monospace; }
.docs-source-shortcuts { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
.docs-source-shortcuts button { display: flex; align-items: center; justify-content: center; gap: 4px; min-height: 28px; border: 1px solid var(--border-soft); border-radius: 3px; color: var(--faint); font-size: 8px; }
.docs-source-shortcuts button:hover { color: var(--accent); border-color: #3e5f56; background: var(--accent-soft); }

.docs-editor-pane { position: relative; display: flex; flex-direction: column; background: #191a19; border-radius: 6px; }
.docs-editor-loading { flex: 1; display: flex; align-items: center; justify-content: center; gap: 9px; color: var(--faint); font-size: 11px; }
.docs-editor-shell { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.docs-editor-caption {
  flex: none;
  height: 42px;
  padding: 0 15px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-bottom: 1px solid var(--border-soft);
  background: #161717;
}
.docs-editor-caption > div { display: flex; align-items: center; gap: 8px; min-width: 0; }
.docs-format-pill { padding: 2px 5px; border: 1px solid var(--border); border-radius: 3px; color: var(--faint); font: 7px/1.2 Consolas, "Courier New", monospace; letter-spacing: 0.07em; }
.docs-editor-caption-meta { color: var(--faint); font: 8px/1 Consolas, "Courier New", monospace; white-space: nowrap; }
.docs-editor-caption-meta span + span { padding-left: 8px; border-left: 1px solid var(--border-soft); }
.docs-paper-scroll { flex: 1; min-height: 0; overflow: auto; padding: 14px; background: radial-gradient(circle at 50% 0, rgba(232, 225, 212, 0.06), transparent 45%), #191a19; }
.docs-paper {
  position: relative;
  width: min(820px, 100%);
  min-height: calc(100% - 2px);
  margin: 0 auto;
  padding: 32px 54px 28px 66px;
  color: var(--docs-ink);
  background-color: var(--docs-paper);
  background-image:
    linear-gradient(90deg, transparent 0, transparent 43px, rgba(168, 77, 65, 0.16) 43px, rgba(168, 77, 65, 0.16) 44px, transparent 44px),
    repeating-linear-gradient(0deg, rgba(74, 64, 47, 0.045) 0, rgba(74, 64, 47, 0.045) 1px, transparent 1px, transparent 30px);
  box-shadow: 0 18px 45px rgba(0, 0, 0, 0.3), inset 0 0 0 1px rgba(80, 68, 47, 0.15);
  clip-path: polygon(0 0, calc(100% - 18px) 0, 100% 18px, 100% 100%, 0 100%);
  display: flex;
  flex-direction: column;
}
.docs-paper::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 15% 22%, rgba(111, 91, 57, 0.04), transparent 30%),
    radial-gradient(circle at 84% 75%, rgba(111, 91, 57, 0.045), transparent 35%);
}
.docs-paper-cut {
  position: absolute;
  top: 0;
  right: 0;
  width: 18px;
  height: 18px;
  background: linear-gradient(225deg, #191a19 50%, rgba(102, 88, 63, 0.28) 51%);
}
.docs-paper-head {
  position: relative;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(72, 63, 49, 0.22);
  color: rgba(63, 56, 43, 0.53);
  font: 8px/1.3 Consolas, "Courier New", monospace;
  letter-spacing: 0.1em;
}
.docs-paper-title {
  position: relative;
  width: 100%;
  margin: 24px 0 8px;
  padding: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--docs-ink);
  font: 600 30px/1.35 "Iowan Old Style", "Source Han Serif SC", "Noto Serif SC", "Songti SC", serif;
  letter-spacing: 0.01em;
}
.docs-paper-title::placeholder { color: rgba(55, 49, 39, 0.32); }
.docs-paper-meta { position: relative; display: flex; justify-content: space-between; gap: 12px; padding-bottom: 13px; border-bottom: 1px solid rgba(72, 63, 49, 0.16); color: rgba(63, 56, 43, 0.48); font: 8px/1.3 Consolas, "Courier New", monospace; letter-spacing: 0.04em; }
.docs-paper-content {
  position: relative;
  width: 100%;
  min-height: 440px;
  flex: 1;
  margin: 20px 0 0;
  padding: 0;
  border: 0;
  outline: 0;
  resize: none;
  overflow: hidden;
  background: transparent;
  color: #343129;
  font: 15px/2.05 "Iowan Old Style", "Source Han Serif SC", "Noto Serif SC", "Songti SC", serif;
  caret-color: #27715f;
  field-sizing: content;
}
.docs-paper-content::placeholder { color: rgba(58, 52, 42, 0.36); }
.docs-paper-content::selection { background: rgba(16, 163, 127, 0.2); }
.docs-paper-foot { position: relative; margin-top: 22px; padding-top: 8px; border-top: 1px solid rgba(72, 63, 49, 0.18); display: flex; justify-content: space-between; color: rgba(63, 56, 43, 0.45); font: 7px/1.2 Consolas, "Courier New", monospace; letter-spacing: 0.09em; }
.docs-editor-status {
  flex: none;
  height: 30px;
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 0 12px;
  border-top: 1px solid var(--border-soft);
  background: #141515;
  color: var(--faint);
  font: 8px/1 Consolas, "Courier New", monospace;
}
.docs-status-left { display: flex; align-items: center; gap: 5px; }
.docs-status-left i { width: 5px; height: 5px; border-radius: 50%; background: var(--faint); }
.docs-status-left i.saved { background: var(--accent); }
.docs-status-left i.dirty,
.docs-status-left i.saving { background: var(--warn); }
.docs-status-spacer { flex: 1; }
.docs-editor-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 28px; text-align: center; }
.docs-empty-sheet {
  width: 150px;
  height: 184px;
  margin-bottom: 24px;
  padding: 18px;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  color: rgba(48, 43, 34, 0.6);
  background: var(--docs-paper);
  box-shadow: 9px 10px 0 #202120, 10px 11px 0 var(--border-soft);
  transform: rotate(-2.5deg);
}
.docs-empty-sheet > span { align-self: flex-start; margin-bottom: 30px; color: rgba(48, 43, 34, 0.35); font: 8px/1 Consolas, "Courier New", monospace; }
.docs-empty-sheet i { width: 100%; height: 1px; margin-top: 13px; background: rgba(48, 43, 34, 0.13); }
.docs-editor-empty h2 { margin: 8px 0 6px; font-size: 18px; font-weight: 600; }
.docs-editor-empty p { max-width: 400px; margin: 0; color: var(--faint); font-size: 11px; line-height: 1.7; }
.docs-empty-actions { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px; margin-top: 20px; }
.docs-empty-actions button {
  min-height: 33px;
  padding: 0 11px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--muted);
  font-size: 10px;
}
.docs-empty-actions button:hover { border-color: var(--accent); color: var(--accent); }
.docs-empty-actions button.primary { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.docs-assist-rail { display: flex; flex-direction: column; border-radius: 6px; background: #141515; }
.docs-assist-head { min-height: 63px; }
.docs-ai-live { display: flex; align-items: center; gap: 5px; color: var(--accent); font: 8px/1 Consolas, "Courier New", monospace; letter-spacing: 0.06em; }
.docs-ai-live i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; box-shadow: 0 0 8px currentColor; }
.docs-ai-live.busy { color: var(--warn); }
.docs-ai-live.busy i { animation: docs-pulse 0.9s infinite; }
.docs-action-grid {
  flex: none;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  padding: 9px;
  border-bottom: 1px solid var(--border-soft);
}
.docs-action-grid button {
  min-width: 0;
  min-height: 62px;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  grid-template-rows: auto auto;
  column-gap: 5px;
  align-content: center;
  padding: 8px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  text-align: left;
  color: var(--muted);
  transition: background var(--t-fast), border-color var(--t-fast), color var(--t-fast);
}
.docs-action-grid button:hover:not(:disabled) { background: #1b1c1c; border-color: #444; }
.docs-action-grid button.active { border-color: rgba(16, 163, 127, 0.65); background: rgba(16, 163, 127, 0.09); color: var(--text); }
.docs-action-grid button:disabled { opacity: 0.38; cursor: default; }
.docs-action-grid button > span { grid-row: 1 / 3; color: var(--faint); font: 8px/1.2 Consolas, "Courier New", monospace; }
.docs-action-grid button.active > span { color: var(--accent); }
.docs-action-grid strong { font-size: 11px; font-weight: 600; }
.docs-action-grid small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--faint); font-size: 8px; }
.docs-assist-brief { flex: none; padding: 11px; border-bottom: 1px solid var(--border-soft); }
.docs-brief-label { display: flex; align-items: center; gap: 5px; color: var(--info); font-size: 9px; }
.docs-brief-label span { flex: 1; }
.docs-brief-label small { color: var(--faint); font: 8px/1 Consolas, "Courier New", monospace; }
.docs-assist-brief > p { margin: 7px 0 0; color: var(--faint); font-size: 9px; line-height: 1.55; }
.docs-instruction { display: flex; flex-direction: column; gap: 5px; margin-top: 9px; }
.docs-instruction > span { display: flex; justify-content: space-between; color: var(--muted); font-size: 9px; }
.docs-instruction > span b { color: var(--faint); font-size: 8px; font-weight: 400; }
.docs-instruction textarea {
  width: 100%;
  min-height: 52px;
  resize: vertical;
  padding: 7px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  outline: 0;
  background: #191a1a;
  color: var(--text);
  font-size: 10px;
  line-height: 1.55;
}
.docs-instruction textarea:focus { border-color: #45675d; }
.docs-instruction textarea::placeholder { color: #515151; }
.docs-selection-hint { color: var(--faint); font-size: 8px; }
.docs-summary-brief { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 4px; margin-top: 9px; }
.docs-summary-brief > span { grid-column: 1 / -1; color: var(--faint); font-size: 8px; }
.docs-summary-brief div { padding: 6px; border: 1px solid var(--border-soft); border-radius: 3px; color: var(--muted); font-size: 8px; }
.docs-summary-brief b { display: block; margin-bottom: 2px; color: var(--faint); font: 7px/1 Consolas, "Courier New", monospace; }
.docs-assist-output {
  flex: 1;
  min-height: 150px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background:
    linear-gradient(rgba(255, 255, 255, 0.018) 1px, transparent 1px),
    #111212;
  background-size: 100% 24px;
}
.docs-output-head {
  flex: none;
  height: 28px;
  padding: 0 10px;
  display: flex;
  align-items: center;
  gap: 5px;
  border-bottom: 1px solid var(--border-soft);
  color: var(--faint);
  font-size: 8px;
}
.docs-output-head > span:first-child { display: flex; align-items: center; gap: 5px; flex: 1; }
.docs-model-name { max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #626262; font: 7px/1 Consolas, "Courier New", monospace; text-transform: uppercase; }
.docs-thinking { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; padding: 18px; text-align: center; color: var(--faint); }
.docs-thinking > span { display: flex; gap: 3px; margin-bottom: 3px; }
.docs-thinking i { width: 4px; height: 14px; background: var(--accent); animation: docs-wave 1s infinite ease-in-out; }
.docs-thinking i:nth-child(2) { animation-delay: 0.12s; }
.docs-thinking i:nth-child(3) { animation-delay: 0.24s; }
.docs-thinking strong { color: var(--muted); font-size: 10px; font-weight: 500; }
.docs-thinking small { font-size: 8px; }
.docs-output-body { flex: 1; overflow: auto; padding: 11px; position: relative; }
.docs-output-body pre { margin: 0; color: #c6c6c3; font: 10px/1.75 "Iowan Old Style", "Source Han Serif SC", "Noto Serif SC", "Songti SC", serif; white-space: pre-wrap; word-break: break-word; }
.docs-cursor { display: inline-block; width: 5px; height: 12px; vertical-align: -2px; background: var(--accent); animation: docs-blink 0.85s steps(1) infinite; }
.docs-output-empty { flex: 1; padding: 22px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 7px; text-align: center; color: var(--faint); }
.docs-output-empty svg { color: #3f5c54; }
.docs-output-empty strong { color: var(--muted); font-size: 10px; font-weight: 500; }
.docs-output-empty p { max-width: 220px; margin: 0; font-size: 8px; line-height: 1.65; }
.docs-output-error { flex: 1; padding: 18px; color: #b17777; }
.docs-output-error strong { font-size: 10px; }
.docs-output-error p { margin: 6px 0 10px; font-size: 9px; line-height: 1.55; }
.docs-output-error button { padding: 4px 8px; border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 3px; color: #e58d8d; font-size: 9px; }
.docs-stopped-note { flex: none; padding: 5px 10px; border-top: 1px solid var(--border-soft); color: var(--warn); font-size: 8px; }
.docs-assist-actions { flex: none; padding: 8px; border-top: 1px solid var(--border-soft); background: #151616; }
.docs-run {
  width: 100%;
  min-height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid var(--accent);
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10px;
}
.docs-run:hover:not(:disabled) { background: rgba(16, 163, 127, 0.22); }
.docs-run:disabled { opacity: 0.4; }
.docs-run kbd { margin-left: auto; margin-right: 5px; border-color: rgba(16, 163, 127, 0.25); color: rgba(16, 163, 127, 0.65); }
.docs-run.stop { border-color: rgba(245, 158, 11, 0.55); background: rgba(245, 158, 11, 0.08); color: var(--warn); }
.docs-output-actions { display: flex; flex-wrap: wrap; gap: 3px; margin-top: 6px; }
.docs-output-actions button { min-height: 25px; padding: 0 6px; display: inline-flex; align-items: center; gap: 3px; border: 1px solid var(--border-soft); border-radius: 3px; color: var(--faint); font-size: 8px; }
.docs-output-actions button:hover:not(:disabled) { color: var(--text); border-color: var(--border); background: var(--surface); }
.docs-output-actions button:disabled { opacity: 0.3; }
.docs-output-actions button.armed { border-color: rgba(245, 158, 11, 0.5); color: var(--warn); }
.docs-assist-foot { flex: none; height: 25px; padding: 0 9px; display: flex; align-items: center; gap: 8px; border-top: 1px solid var(--border-soft); color: #525252; font: 7px/1 Consolas, "Courier New", monospace; }
.docs-assist-foot span:first-child { flex: 1; }

.docs-modal-layer {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(5, 6, 6, 0.82);
  backdrop-filter: blur(5px);
}
.docs-modal {
  width: min(660px, 100%);
  max-height: min(780px, 92vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #3b3c3c;
  border-radius: 7px;
  background: #161717;
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.58);
}
.docs-modal-head { flex: none; padding: 16px 17px 13px; display: flex; align-items: flex-start; justify-content: space-between; border-bottom: 1px solid var(--border-soft); }
.docs-modal-head h2 { margin: 4px 0 0; font-size: 16px; font-weight: 600; }
.docs-modal-head > button { width: 29px; height: 29px; display: grid; place-items: center; border: 1px solid var(--border-soft); border-radius: 4px; color: var(--faint); }
.docs-modal-head > button:hover { color: var(--text); background: var(--surface); }
.docs-create-types { flex: none; display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; padding: 13px 17px 8px; }
.docs-create-types button {
  min-width: 0;
  min-height: 82px;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  grid-template-rows: auto auto;
  align-content: center;
  column-gap: 6px;
  padding: 11px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  text-align: left;
  color: var(--faint);
}
.docs-create-types button:hover { border-color: #454545; background: #1b1c1c; }
.docs-create-types button.active { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.docs-create-types svg { grid-row: 1 / 3; align-self: center; }
.docs-create-types strong { color: var(--muted); font-size: 11px; font-weight: 600; }
.docs-create-types button.active strong { color: var(--text); }
.docs-create-types small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 8px; }
.docs-create-title { flex: none; display: flex; flex-direction: column; gap: 5px; padding: 6px 17px 7px; }
.docs-create-title > span { display: flex; justify-content: space-between; color: var(--muted); font-size: 9px; }
.docs-create-title small { color: var(--faint); font-size: 8px; }
.docs-create-title input { height: 36px; padding: 0 10px; border: 1px solid var(--border); border-radius: 4px; outline: 0; background: #191a1a; font-size: 12px; }
.docs-create-title input:focus { border-color: #45675d; }
.docs-source-search { margin: 3px 17px 7px; flex: none; }
.docs-source-list { flex: 1; min-height: 100px; overflow: auto; margin: 0 17px; border: 1px solid var(--border-soft); border-radius: 4px; background: #121313; }
.docs-source-list > button { width: 100%; display: grid; grid-template-columns: 37px minmax(0, 1fr) 20px; gap: 8px; align-items: center; padding: 10px; text-align: left; border-bottom: 1px solid var(--border-soft); }
.docs-source-list > button:last-of-type { border-bottom: 0; }
.docs-source-list > button:hover { background: #1a1b1b; }
.docs-source-list > button.active { background: rgba(16, 163, 127, 0.08); }
.docs-source-type { color: var(--faint); font: 8px/1 Consolas, "Courier New", monospace; letter-spacing: 0.06em; }
.docs-source-list > button.active .docs-source-type { color: var(--accent); }
.docs-source-copy { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.docs-source-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 11px; font-weight: 500; }
.docs-source-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--faint); font-size: 8px; }
.docs-source-check { color: var(--accent); }
.docs-source-state { min-height: 100px; }
.docs-modal-foot { flex: none; display: flex; align-items: center; gap: 7px; padding: 13px 17px; border-top: 1px solid var(--border-soft); }
.docs-modal-foot > span { flex: 1; color: var(--faint); font-size: 8px; }
.docs-modal-foot > button { min-height: 32px; padding: 0 12px; display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--border); border-radius: 4px; color: var(--muted); font-size: 10px; }
.docs-modal-foot > button:hover { color: var(--text); background: var(--surface); }
.docs-modal-foot > button.primary { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.docs-modal-foot > button:disabled { opacity: 0.4; cursor: default; }

.spin { animation: docs-spin 0.9s linear infinite; }
@keyframes docs-spin { to { transform: rotate(360deg); } }
@keyframes docs-pulse { 50% { opacity: 0.35; } }
@keyframes docs-wave { 0%, 100% { height: 6px; opacity: 0.35; } 50% { height: 16px; opacity: 1; } }
@keyframes docs-blink { 50% { opacity: 0; } }
@media (max-width: 1240px) and (min-width: 921px) {
  .docs-body { grid-template-columns: 224px minmax(0, 1fr) 286px; padding-left: 8px; padding-right: 8px; }
  .docs-top-metrics { display: none; }
  .docs-paper { padding: 28px 36px 24px 54px; }
  .docs-paper-title { font-size: 27px; }
  .docs-editor-caption-meta span:nth-child(2) { display: none; }
  .docs-action-grid small { display: none; }
  .docs-action-grid button { min-height: 48px; grid-template-rows: auto; }
  .docs-assist-foot span:nth-child(2) { display: none; }
}

@media (max-width: 920px) {
  .docs-topbar { height: 50px; padding: 0 9px; gap: 8px; }
  .docs-back { width: 28px; height: 28px; }
  .docs-title-group { flex: 1; }
  .docs-title-group h1 { max-width: 48vw; font-size: 13px; }
  .docs-eyebrow { font-size: 7px; letter-spacing: 0.09em; }
  .docs-top-metrics { display: none; }
  .docs-save-state { display: none; }
  .docs-top-actions { gap: 3px; }
  .docs-icon-btn { min-width: 28px; width: 28px; height: 28px; padding: 0; }
  .docs-icon-btn.danger span { display: none; }
  .docs-mobile-nav {
    flex: none;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 3px;
    padding: 5px 8px 0;
    border-bottom: 1px solid var(--border-soft);
    background: #151616;
  }
  .docs-mobile-nav button {
    height: 33px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    border: 1px solid transparent;
    border-radius: 4px;
    color: var(--faint);
    font-size: 10px;
  }
  .docs-mobile-nav button span { font: 7px/1 Consolas, "Courier New", monospace; }
  .docs-mobile-nav button small { color: #535353; font: 8px/1 Consolas, "Courier New", monospace; }
  .docs-mobile-nav button.active { border-color: var(--border); background: var(--surface); color: var(--text); }
  .docs-body { display: block; padding: 7px 7px calc(var(--dock-h) + 8px); overflow: hidden; }
  .docs-register,
  .docs-editor-pane,
  .docs-assist-rail { display: none; height: 100%; border-radius: 5px; }
  .docs-ws[data-pane="library"] .docs-register,
  .docs-ws[data-pane="editor"] .docs-editor-pane,
  .docs-ws[data-pane="assist"] .docs-assist-rail { display: flex; }
  .docs-register-list { padding-bottom: 8px; }
  .docs-editor-caption { height: 37px; padding: 0 9px; }
  .docs-editor-caption-meta span:nth-child(2) { display: none; }
  .docs-paper-scroll { padding: 8px; }
  .docs-paper { padding: 27px 22px 24px 39px; }
  .docs-paper::before { background: radial-gradient(circle at 15% 22%, rgba(111, 91, 57, 0.04), transparent 30%); }
  .docs-paper-title { margin-top: 19px; font-size: 24px; }
  .docs-paper-content { min-height: 420px; font-size: 14px; line-height: 1.95; }
  .docs-paper-meta { font-size: 7px; }
  .docs-paper-meta span:first-child { max-width: 54%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .docs-editor-status { gap: 8px; height: 28px; padding: 0 8px; }
  .docs-editor-status span:nth-of-type(2) { display: none; }
  .docs-assist-rail { overflow: auto; }
  .docs-assist-output { min-height: 220px; }
  .docs-assist-actions { position: sticky; bottom: 0; z-index: 2; }
  .docs-assist-foot { display: none; }
  .docs-action-grid small { display: block; }
  .docs-create-types { padding-left: 12px; padding-right: 12px; }
  .docs-create-title,
  .docs-modal-head,
  .docs-modal-foot { padding-left: 12px; padding-right: 12px; }
  .docs-source-search { margin-left: 12px; margin-right: 12px; }
  .docs-source-list { margin-left: 12px; margin-right: 12px; }
}

@media (max-width: 560px) {
  .docs-title-group h1 { max-width: 38vw; }
  .docs-icon-btn:nth-of-type(1) { display: none; }
  .docs-paper { padding: 24px 15px 22px 31px; }
  .docs-paper-title { font-size: 22px; }
  .docs-paper-head { font-size: 7px; }
  .docs-paper-head span:last-child { display: none; }
  .docs-paper-content { min-height: 390px; font-size: 13px; }
  .docs-editor-caption .docs-kicker { max-width: 57vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .docs-editor-caption-meta span:first-child { display: none; }
  .docs-editor-empty { padding: 18px; }
  .docs-empty-sheet { width: 122px; height: 151px; margin-bottom: 18px; }
  .docs-empty-actions { display: grid; width: 100%; max-width: 250px; grid-template-columns: 1fr; }
  .docs-empty-actions button { justify-content: center; }
  .docs-modal-layer { padding: 8px; }
  .docs-modal { max-height: 95vh; }
  .docs-modal-head { padding-top: 12px; padding-bottom: 10px; }
  .docs-create-types { grid-template-columns: 1fr; gap: 4px; padding-top: 9px; }
  .docs-create-types button { min-height: 51px; grid-template-columns: 22px minmax(0, 1fr) auto; grid-template-rows: auto; padding: 8px 10px; }
  .docs-create-types button small { grid-column: 3; }
  .docs-create-types svg { grid-row: auto; }
  .docs-modal-foot > span { display: none; }
  .docs-modal-foot { justify-content: flex-end; }
  .docs-source-list { min-height: 80px; }
  .docs-output-actions button { flex: 1; justify-content: center; }
}
</style>