<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ArrowLeft, BookOpenText, ChevronRight, CircleStop, CornerDownLeft, FileText,
  FileUp, FolderOpen, Hash, LibraryBig, LoaderCircle, PanelRightClose,
  PanelRightOpen, Pencil, Plus, Quote, Search, Send, Sparkles, Trash2, Upload, X,
} from 'lucide-vue-next'
import { api, streamSSE } from '../lib/api.js'
import { useUiStore } from '../stores/ui.js'

const ui = useUiStore()
const libraries = ref([])
const documents = ref([])
const stats = ref({ libraries: 0, documents: 0, chunks: 0, characters: 0 })
const selectedLibraryId = ref(null)
const selectedDocId = ref(null)
const selectedDocument = ref(null)
const loading = ref(true)
const documentsLoading = ref(false)
const readLoading = ref(false)
const importing = ref(false)
const importLabel = ref('')
const dragActive = ref(false)
const fileInput = ref(null)
const readerScroll = ref(null)
const messageScroll = ref(null)
const mobilePane = ref('library')
const railCollapsed = ref(false)
const readProgress = ref(0)

const libraryFormOpen = ref(false)
const libraryDraft = ref({ name: '', color: 'accent' })
const librarySaving = ref(false)
const libraryDeleteArmed = ref(false)
const documentDeleteArmed = ref(false)
const renaming = ref(false)
const renameDraft = ref('')

const searchQuery = ref('')
const searchResults = ref([])
const searching = ref(false)
const searchSubmitted = ref(false)
const searchError = ref('')
const highlightRange = ref(null)

const askDraft = ref('')
const askMessages = ref([])
const askBusy = ref(false)
const askError = ref('')

let libraryDeleteTimer = null
let documentDeleteTimer = null

const allowedExtensions = new Set(['txt', 'md', 'markdown', 'csv', 'json', 'html', 'htm'])
const libraryColors = ['accent', 'info', 'warn', 'danger']

const activeLibrary = computed(
  () => libraries.value.find((item) => item.id === selectedLibraryId.value) || null,
)
const activeDocument = computed(() => selectedDocument.value)
const activeDocumentIndex = computed(
  () => documents.value.findIndex((item) => item.id === selectedDocId.value),
)
const hasDocuments = computed(() => documents.value.length > 0)
const readerBlocks = computed(() => {
  const text = activeDocument.value?.text || ''
  if (!text) return []
  const blocks = []
  const maxBlock = 1800
  let start = 0
  while (start < text.length) {
    let end = Math.min(text.length, start + maxBlock)
    if (end < text.length) {
      const boundary = text.lastIndexOf('\n', end)
      if (boundary > start + maxBlock * 0.55) end = boundary + 1
    }
    blocks.push({
      index: blocks.length,
      start,
      end,
      text: text.slice(start, end),
    })
    start = end
  }
  return blocks
})
const highlightedChunk = computed(() => {
  if (!highlightRange.value) return null
  return highlightRange.value
})
const totalAskSources = computed(() =>
  askMessages.value.reduce((count, message) => count + (message.sources?.length || 0), 0),
)

function formatNumber(value) {
  return new Intl.NumberFormat('zh-CN').format(Number(value || 0))
}

function formatScore(value) {
  return Math.round(Number(value || 0) * 100) + '%'
}

function formatBytes(value) {
  const size = Number(value || 0)
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function extensionOf(filename) {
  return String(filename || '').split('.').pop().toLowerCase()
}

function fileKind(filename) {
  const extension = extensionOf(filename)
  if (extension === 'html' || extension === 'htm') return 'HTML'
  if (extension === 'md' || extension === 'markdown') return 'MD'
  if (extension === 'json') return 'JSON'
  if (extension === 'csv') return 'CSV'
  return 'TXT'
}

function libraryColor(color) {
  return ['accent', 'info', 'warn', 'danger'].includes(color) ? color : 'accent'
}

function showError(error) {
  ui.showToast(error?.message || String(error || '操作失败'))
}

async function loadAll({ keepSelection = true } = {}) {
  loading.value = true
  try {
    const [libraryData, statsData] = await Promise.all([
      api('/api/knowledge/libraries'),
      api('/api/knowledge/stats'),
    ])
    libraries.value = libraryData.libraries || []
    stats.value = statsData.stats || stats.value
    const previous = keepSelection ? selectedLibraryId.value : null
    if (!libraries.value.some((item) => item.id === previous)) {
      selectedLibraryId.value = libraries.value[0]?.id || null
    }
    await loadDocuments({ preserveDocument: keepSelection })
  } catch (error) {
    showError(error)
  } finally {
    loading.value = false
  }
}

async function loadDocuments({ preserveDocument = true, selectFirst = true } = {}) {
  if (!selectedLibraryId.value) {
    documents.value = []
    selectedDocument.value = null
    selectedDocId.value = null
    return
  }
  documentsLoading.value = true
  try {
    const params = new URLSearchParams({ library_id: selectedLibraryId.value })
    const data = await api(`/api/knowledge/documents?${params}`)
    documents.value = data.documents || []
    const stillExists = documents.value.some((item) => item.id === selectedDocId.value)
    if (!preserveDocument || !stillExists) {
      selectedDocId.value = documents.value[0]?.id || null
      selectedDocument.value = null
      if (selectedDocId.value && selectFirst) {
        await loadDocument(documents.value[0], { switchPane: false })
      }
    }
  } catch (error) {
    documents.value = []
    showError(error)
  } finally {
    documentsLoading.value = false
  }
}

async function loadStats() {
  try {
    const data = await api('/api/knowledge/stats')
    stats.value = data.stats || stats.value
  } catch { /* 统计失败不阻断阅读 */ }
}

async function selectLibrary(library) {
  if (selectedLibraryId.value === library.id) return
  selectedLibraryId.value = library.id
  selectedDocId.value = null
  selectedDocument.value = null
  highlightRange.value = null
  searchResults.value = []
  searchSubmitted.value = false
  clearDeleteArms()
  await loadDocuments({ preserveDocument: false })
}

async function createLibrary() {
  const name = libraryDraft.value.name.trim()
  if (!name) {
    ui.showToast('先给资料库起一个名字')
    return
  }
  librarySaving.value = true
  try {
    const data = await api('/api/knowledge/libraries', {
      method: 'POST',
      body: JSON.stringify({
        name,
        description: '',
        color: libraryDraft.value.color,
      }),
    })
    libraries.value = [data.library, ...libraries.value]
    selectedLibraryId.value = data.library.id
    libraryDraft.value = { name: '', color: 'accent' }
    libraryFormOpen.value = false
    await loadDocuments({ preserveDocument: false })
    await loadStats()
    ui.showToast('资料库已建立')
  } catch (error) {
    showError(error)
  } finally {
    librarySaving.value = false
  }
}

function armLibraryDelete() {
  if (!activeLibrary.value) return
  if (libraryDeleteArmed.value) {
    deleteLibrary()
    return
  }
  libraryDeleteArmed.value = true
  if (libraryDeleteTimer) clearTimeout(libraryDeleteTimer)
  libraryDeleteTimer = setTimeout(() => { libraryDeleteArmed.value = false }, 2600)
}

async function deleteLibrary() {
  if (!activeLibrary.value) return
  try {
    await api(`/api/knowledge/libraries/${activeLibrary.value.id}`, { method: 'DELETE' })
    const deletedName = activeLibrary.value.name
    selectedLibraryId.value = null
    selectedDocId.value = null
    selectedDocument.value = null
    libraryDeleteArmed.value = false
    await loadAll({ keepSelection: false })
    ui.showToast(`已删除资料库「${deletedName}」`)
  } catch (error) {
    showError(error)
  }
}

async function loadDocument(item, { switchPane = true, range = null } = {}) {
  if (!item?.id) return
  readLoading.value = true
  readError.value = ''
  selectedDocId.value = item.id
  highlightRange.value = range
  if (switchPane) mobilePane.value = 'reader'
  try {
    const data = await api(`/api/knowledge/documents/${item.id}`)
    selectedDocument.value = data.document
    await nextTick()
    if (range) scrollToHighlight()
    else if (readerScroll.value) readerScroll.value.scrollTop = 0
  } catch (error) {
    selectedDocument.value = null
    readError.value = error.message || '资料读取失败'
  } finally {
    readLoading.value = false
  }
}

const readError = ref('')

function scrollToHighlight() {
  const target = readerScroll.value?.querySelector('.kb-block-hit')
  if (target) target.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

function startRename() {
  if (!activeDocument.value) return
  renameDraft.value = activeDocument.value.title
  renaming.value = true
}

async function saveRename() {
  if (!renaming.value || !activeDocument.value) return
  const title = renameDraft.value.trim()
  if (!title || title === activeDocument.value.title) {
    renaming.value = false
    return
  }
  try {
    const data = await api(`/api/knowledge/documents/${activeDocument.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    })
    selectedDocument.value = { ...selectedDocument.value, title: data.document.title }
    const index = documents.value.findIndex((item) => item.id === selectedDocument.value.id)
    if (index >= 0) documents.value[index] = { ...documents.value[index], title }
    renaming.value = false
    ui.showToast('标题已更新')
  } catch (error) {
    showError(error)
  }
}

function armDocumentDelete() {
  if (!activeDocument.value) return
  if (documentDeleteArmed.value) {
    deleteDocument()
    return
  }
  documentDeleteArmed.value = true
  if (documentDeleteTimer) clearTimeout(documentDeleteTimer)
  documentDeleteTimer = setTimeout(() => { documentDeleteArmed.value = false }, 2600)
}

function clearDeleteArms() {
  libraryDeleteArmed.value = false
  documentDeleteArmed.value = false
  if (libraryDeleteTimer) clearTimeout(libraryDeleteTimer)
  if (documentDeleteTimer) clearTimeout(documentDeleteTimer)
}

async function deleteDocument() {
  if (!activeDocument.value) return
  const deletedId = activeDocument.value.id
  try {
    await api(`/api/knowledge/documents/${deletedId}`, { method: 'DELETE' })
    documentDeleteArmed.value = false
    selectedDocId.value = null
    selectedDocument.value = null
    highlightRange.value = null
    await loadDocuments({ preserveDocument: false })
    await loadStats()
    ui.showToast('资料已删除')
  } catch (error) {
    showError(error)
  }
}
const searchInput = ref(null)
const askInput = ref(null)
const searchAcrossLibraries = ref(false)
let dragDepth = 0
let askAbortController = null

const displayBlocks = computed(() => {
  const range = highlightRange.value
  return readerBlocks.value.map((block) => {
    if (!range) return { ...block, before: block.text, hit: '', after: '' }
    const hitStart = Math.max(block.start, Number(range.start) || 0)
    const hitEnd = Math.min(block.end, Number(range.end) || 0)
    if (hitStart >= hitEnd) return { ...block, before: block.text, hit: '', after: '' }
    return {
      ...block,
      before: block.text.slice(0, hitStart - block.start),
      hit: block.text.slice(hitStart - block.start, hitEnd - block.start),
      after: block.text.slice(hitEnd - block.start),
    }
  })
})

function padIndex(value) {
  return String(value).padStart(2, '0')
}

function setMobilePane(pane) {
  mobilePane.value = pane
  if (pane === 'ask') railCollapsed.value = false
}

function toggleRail() {
  railCollapsed.value = !railCollapsed.value
}

function focusSearch() {
  railCollapsed.value = false
  setMobilePane('ask')
  nextTick(() => searchInput.value?.focus())
}

function focusAsk() {
  railCollapsed.value = false
  setMobilePane('ask')
  nextTick(() => askInput.value?.focus())
}

function onReaderScroll() {
  const el = readerScroll.value
  if (!el) {
    readProgress.value = 0
    return
  }
  const max = Math.max(1, el.scrollHeight - el.clientHeight)
  readProgress.value = Math.min(100, Math.max(0, Math.round((el.scrollTop / max) * 100)))
}

function scrollMessages() {
  nextTick(() => {
    if (messageScroll.value) messageScroll.value.scrollTop = messageScroll.value.scrollHeight
  })
}

function openFilePicker() {
  if (!selectedLibraryId.value) {
    libraryFormOpen.value = true
    setMobilePane('library')
    ui.showToast('先建立一个资料库，再导入资料')
    return
  }
  fileInput.value?.click()
}

function onFileInput(event) {
  const files = event.target.files
  event.target.value = ''
  importFiles(files)
}

function onDragEnter(event) {
  event.preventDefault()
  dragDepth += 1
  dragActive.value = true
}

function onDragOver(event) {
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

function onDragLeave(event) {
  event.preventDefault()
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dragActive.value = false
}

function onDrop(event) {
  event.preventDefault()
  dragDepth = 0
  dragActive.value = false
  importFiles(event.dataTransfer?.files)
}

async function importFiles(fileList) {
  if (importing.value) return
  if (!selectedLibraryId.value) {
    libraryFormOpen.value = true
    setMobilePane('library')
    ui.showToast('先建立一个资料库，再导入资料')
    return
  }
  const files = Array.from(fileList || [])
  if (!files.length) return
  const supported = files.filter((file) => allowedExtensions.has(extensionOf(file.name)))
  if (!supported.length) {
    ui.showToast('当前支持 TXT、Markdown、CSV、JSON 与 HTML')
    return
  }
  const skipped = files.length - supported.length
  const failures = []
  let imported = 0
  let lastImportedId = null
  importing.value = true
  try {
    for (let index = 0; index < supported.length; index += 1) {
      const file = supported[index]
      importLabel.value = `正在解析 ${index + 1}/${supported.length} · ${file.name}`
      try {
        const content = await file.text()
        if (!content.trim()) throw new Error('文件内容为空')
        const data = await api(
          `/api/knowledge/libraries/${encodeURIComponent(selectedLibraryId.value)}/documents`,
          {
            method: 'POST',
            body: JSON.stringify({
              filename: file.name,
              title: file.name.replace(/\.[^.]+$/, ''),
              content,
              mime_type: file.type || null,
            }),
          },
        )
        documents.value = [
          data.document,
          ...documents.value.filter((item) => item.id !== data.document.id),
        ]
        imported += 1
        lastImportedId = data.document.id
      } catch (error) {
        failures.push(`${file.name}：${error?.message || '导入失败'}`)
      }
    }
    if (imported) {
      await loadDocuments({ preserveDocument: false, selectFirst: false })
      const lastDocument = documents.value.find((item) => item.id === lastImportedId) || documents.value[0]
      if (lastDocument) await loadDocument(lastDocument)
      await loadStats()
    }
    if (failures.length) {
      ui.showToast(failures[0] + (failures.length > 1 ? `（另有 ${failures.length - 1} 个失败）` : ''))
    } else if (imported) {
      ui.showToast(`已导入 ${imported} 份资料并建立检索切片${skipped ? `，跳过 ${skipped} 个不支持文件` : ''}`)
    }
  } finally {
    importing.value = false
    importLabel.value = ''
  }
}

async function submitSearch() {
  const query = searchQuery.value.trim()
  if (!query) {
    searchResults.value = []
    searchSubmitted.value = false
    return
  }
  searching.value = true
  searchError.value = ''
  searchSubmitted.value = true
  try {
    const params = new URLSearchParams({ q: query, limit: '12' })
    if (!searchAcrossLibraries.value && selectedLibraryId.value) {
      params.set('library_id', selectedLibraryId.value)
    }
    const data = await api(`/api/knowledge/search?${params}`)
    searchResults.value = data.results || []
  } catch (error) {
    searchResults.value = []
    searchError.value = error?.message || '检索失败'
  } finally {
    searching.value = false
  }
}

async function toggleSearchScope() {
  searchAcrossLibraries.value = !searchAcrossLibraries.value
  if (searchSubmitted.value) await submitSearch()
}

async function jumpToSource(source, { switchPane = true } = {}) {
  if (!source?.document?.id || !source?.chunk) return
  const targetLibraryId = source.library?.id || selectedLibraryId.value
  if (targetLibraryId && targetLibraryId !== selectedLibraryId.value) {
    selectedLibraryId.value = targetLibraryId
    selectedDocId.value = null
    selectedDocument.value = null
    highlightRange.value = null
    await loadDocuments({ preserveDocument: false, selectFirst: false })
  }
  await loadDocument(
    { id: source.document.id },
    {
      switchPane,
      range: {
        start: Number(source.chunk.start_offset) || 0,
        end: Number(source.chunk.end_offset) || 0,
      },
    },
  )
}

function askFromSearch() {
  if (!searchQuery.value.trim()) return
  askDraft.value = `请基于资料解释：${searchQuery.value.trim()}`
  focusAsk()
}

function useSuggestion(text) {
  askDraft.value = text
  focusAsk()
}

function citationParts(content) {
  const parts = []
  const pattern = /\[(\d+)\]/g
  const value = String(content || '')
  let lastIndex = 0
  let match = pattern.exec(value)
  while (match) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', text: value.slice(lastIndex, match.index) })
    }
    parts.push({ type: 'citation', number: Number(match[1]), index: Number(match[1]) - 1 })
    lastIndex = match.index + match[0].length
    match = pattern.exec(value)
  }
  if (lastIndex < value.length) parts.push({ type: 'text', text: value.slice(lastIndex) })
  return parts
}

async function openCitation(message, index) {
  const source = message.sources?.[index]
  if (source) await jumpToSource(source)
}

async function submitAsk() {
  const question = askDraft.value.trim()
  if (!question || askBusy.value) return
  if (!stats.value.documents) {
    ui.showToast('知识库还没有可检索的资料')
    return
  }
  askDraft.value = ''
  askError.value = ''
  askMessages.value.push({ role: 'user', content: question })
  askMessages.value.push({
    role: 'assistant',
    content: '',
    sources: [],
    model: '',
    error: '',
  })
  const assistantIndex = askMessages.value.length - 1
  const assistant = askMessages.value[assistantIndex]
  askBusy.value = true
  scrollMessages()
  const controller = new AbortController()
  askAbortController = controller
  try {
    await streamSSE(
      '/api/knowledge/ask/stream',
      {
        question,
        library_id: searchAcrossLibraries.value ? null : selectedLibraryId.value,
        top_k: 6,
      },
      (event) => {
        if (event.meta) {
          assistant.sources = event.meta.sources || []
          assistant.model = event.meta.model || ''
          scrollMessages()
        }
        if (typeof event.delta === 'string') {
          assistant.content += event.delta
          scrollMessages()
        }
        if (event.error) {
          assistant.error = event.error.message || '回答生成失败'
        }
      },
      { signal: controller.signal },
    )
  } catch (error) {
    if (error?.name === 'AbortError') {
      if (!assistant.content) assistant.content = '已停止生成。'
    } else {
      assistant.error = error?.message || '问答失败'
      askError.value = assistant.error
    }
  } finally {
    askBusy.value = false
    askAbortController = null
    scrollMessages()
  }
}

function stopAsk() {
  askAbortController?.abort()
}

function retryLastAsk() {
  const lastUser = [...askMessages.value].reverse().find((message) => message.role === 'user')
  if (!lastUser) return
  askDraft.value = lastUser.content
  focusAsk()
}

function onKeydown(event) {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && askDraft.value.trim()) {
    event.preventDefault()
    submitAsk()
    return
  }
  if (event.altKey && ['1', '2', '3'].includes(event.key)) {
    event.preventDefault()
    setMobilePane({ '1': 'library', '2': 'reader', '3': 'ask' }[event.key])
    return
  }
  const tag = String(event.target?.tagName || '').toLowerCase()
  const editing = ['input', 'textarea', 'select'].includes(tag) || event.target?.isContentEditable
  if (!editing && event.key === '/') {
    event.preventDefault()
    focusSearch()
  }
}

onMounted(() => {
  loadAll()
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  askAbortController?.abort()
  clearDeleteArms()
  window.removeEventListener('keydown', onKeydown)
})</script>

<template>
  <div
    class="kb-ws"
    :data-pane="mobilePane"
    @dragenter="onDragEnter"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <header class="kb-topbar">
      <button class="kb-back" type="button" aria-label="返回工作台" title="返回工作台" @click="ui.goHome">
        <ArrowLeft :size="18" />
      </button>
      <span class="kb-topbar-rule"></span>
      <div class="kb-title-group">
        <span class="kb-eyebrow"><Hash :size="11" /> INDEX / KNOWLEDGE</span>
        <div class="kb-topbar-title">
          <span class="kb-live-dot"></span>
          <h1>{{ activeLibrary?.name || '资料工作台' }}</h1>
          <span v-if="activeLibrary" class="kb-topbar-sub">{{ activeLibrary.description || '本地资料索引' }}</span>
        </div>
      </div>
      <div class="kb-topbar-metrics">
        <span><strong>{{ formatNumber(stats.documents) }}</strong> 资料</span>
        <span><strong>{{ formatNumber(stats.chunks) }}</strong> 切片</span>
        <span><strong>{{ formatNumber(stats.characters) }}</strong> 字符</span>
      </div>
      <button
        class="kb-icon-btn kb-rail-toggle"
        type="button"
        :aria-label="railCollapsed ? '展开检索轨道' : '收起检索轨道'"
        :title="railCollapsed ? '展开检索轨道' : '收起检索轨道'"
        @click="toggleRail"
      >
        <PanelRightOpen v-if="railCollapsed" :size="17" />
        <PanelRightClose v-else :size="17" />
      </button>
    </header>

    <nav class="kb-mobile-nav" aria-label="知识库页面切换">
      <button type="button" :class="{ active: mobilePane === 'library' }" @click="setMobilePane('library')">
        <LibraryBig :size="14" /><span>资料库</span><small>{{ libraries.length }}</small>
      </button>
      <button type="button" :class="{ active: mobilePane === 'reader' }" @click="setMobilePane('reader')">
        <BookOpenText :size="14" /><span>阅读</span><small>{{ activeDocument ? padIndex(activeDocumentIndex + 1) : '—' }}</small>
      </button>
      <button type="button" :class="{ active: mobilePane === 'ask' }" @click="setMobilePane('ask')">
        <Sparkles :size="14" /><span>检索问答</span><small>{{ searchResults.length || totalAskSources }}</small>
      </button>
    </nav>

    <div class="kb-ws-body" :class="{ 'rail-collapsed': railCollapsed }">
      <aside class="kb-library-pane">
        <div class="kb-pane-heading">
          <div>
            <span class="kb-section-code">01 / LIBRARIES</span>
            <h2>资料库</h2>
          </div>
          <button class="kb-icon-btn" type="button" aria-label="新建资料库" title="新建资料库" @click="libraryFormOpen = !libraryFormOpen">
            <X v-if="libraryFormOpen" :size="16" />
            <Plus v-else :size="16" />
          </button>
        </div>

        <form v-if="libraryFormOpen" class="kb-library-form" @submit.prevent="createLibrary">
          <label>
            <span>资料库名称</span>
            <input v-model="libraryDraft.name" maxlength="120" placeholder="例如：产品研究" autofocus />
          </label>
          <div class="kb-color-row" aria-label="资料库标记色">
            <button
              v-for="color in libraryColors"
              :key="color"
              class="kb-color-dot"
              :class="['color-' + color, { active: libraryDraft.color === color }]"
              type="button"
              :aria-label="color"
              @click="libraryDraft.color = color"
            ></button>
            <span>索引色</span>
          </div>
          <div class="kb-form-actions">
            <button type="button" @click="libraryFormOpen = false">取消</button>
            <button class="primary" type="submit" :disabled="librarySaving">
              <LoaderCircle v-if="librarySaving" class="spin" :size="13" />
              <Plus v-else :size="13" />
              {{ librarySaving ? '建立中' : '建立资料库' }}
            </button>
          </div>
        </form>

        <div class="kb-library-scroll">
          <div v-if="loading && !libraries.length" class="kb-inline-loading">
            <LoaderCircle class="spin" :size="16" /> 正在整理索引
          </div>
          <div v-else-if="!libraries.length" class="kb-library-empty">
            <FolderOpen :size="24" />
            <strong>还没有资料库</strong>
            <span>先划出一块主题空间。</span>
          </div>
          <button
            v-for="(library, index) in libraries"
            :key="library.id"
            class="kb-library-row"
            :class="['color-' + libraryColor(library.color), { active: library.id === selectedLibraryId }]"
            type="button"
            @click="selectLibrary(library)"
          >
            <span class="kb-library-index">{{ padIndex(index + 1) }}</span>
            <span class="kb-library-copy">
              <strong>{{ library.name }}</strong>
              <small>
                {{ library.document_count || 0 }} 份资料
                <span>·</span>
                {{ formatNumber(library.char_count) }} 字符
              </small>
            </span>
            <ChevronRight :size="14" class="kb-row-arrow" />
          </button>
        </div>

        <div class="kb-doc-heading">
          <div>
            <span class="kb-section-code">02 / DOCUMENTS</span>
            <h2>资料索引 <small>{{ documents.length }}</small></h2>
          </div>
          <button class="kb-icon-btn" type="button" aria-label="导入资料" title="导入资料" :disabled="importing" @click="openFilePicker">
            <LoaderCircle v-if="importing" class="spin" :size="15" />
            <FileUp v-else :size="15" />
          </button>
        </div>

        <div class="kb-document-scroll">
          <div v-if="documentsLoading" class="kb-inline-loading compact"><LoaderCircle class="spin" :size="14" /> 读取资料</div>
          <div v-else-if="!selectedLibraryId" class="kb-muted-note">选择一个资料库开始阅读</div>
          <div v-else-if="!hasDocuments" class="kb-document-empty">
            <FileText :size="20" />
            <span>这里还没有资料</span>
            <button type="button" @click="openFilePicker"><Upload :size="12" />导入文件</button>
          </div>
          <button
            v-for="(document, index) in documents"
            :key="document.id"
            class="kb-doc-row"
            :class="{ active: document.id === selectedDocId }"
            type="button"
            @click="loadDocument(document)"
          >
            <span class="kb-doc-index">{{ padIndex(index + 1) }}</span>
            <span class="kb-doc-copy">
              <strong>{{ document.title }}</strong>
              <small>{{ fileKind(document.filename) }} · {{ formatBytes(document.size) }} · {{ document.chunk_count || 0 }} 片</small>
            </span>
            <span class="kb-doc-state" :title="document.status">{{ document.status === 'ready' ? 'READY' : document.status }}</span>
          </button>
        </div>

        <footer class="kb-library-foot">
          <span>{{ stats.libraries }} LIBRARIES</span>
          <span>{{ formatNumber(stats.characters) }} CHARS</span>
        </footer>
      </aside>

      <main class="kb-reader-pane">
        <div v-if="readLoading" class="kb-reader-state"><LoaderCircle class="spin" :size="22" /><span>正在展开资料</span></div>
        <div v-else-if="readError" class="kb-reader-state error"><X :size="20" /><span>{{ readError }}</span><button type="button" @click="loadDocument({ id: selectedDocId })">重试</button></div>
        <div v-else-if="!activeDocument" class="kb-reader-state empty">
          <div class="kb-empty-glyph"><BookOpenText :size="28" /></div>
          <span class="kb-section-code">READING SURFACE</span>
          <h2>{{ selectedLibraryId ? '从左侧打开一份资料' : '建立你的第一块知识空间' }}</h2>
          <p>{{ selectedLibraryId ? '资料会被切成可检索片段，并在这里保持原文阅读。' : '资料库负责归档，检索轨道负责把问题送回原文。' }}</p>
          <button v-if="!selectedLibraryId" class="kb-primary-btn" type="button" @click="libraryFormOpen = true; setMobilePane('library')">
            <Plus :size="14" />新建资料库
          </button>
          <button v-else class="kb-secondary-btn" type="button" @click="openFilePicker"><Upload :size="14" />导入第一份资料</button>
        </div>

        <template v-else>
          <header class="kb-reader-head">
            <div class="kb-reader-filemark">
              <span>{{ fileKind(activeDocument.filename) }}</span>
              <i></i>
              <small>#{{ padIndex(activeDocumentIndex + 1) }}</small>
            </div>
            <div class="kb-reader-title-wrap">
              <input
                v-if="renaming"
                v-model="renameDraft"
                class="kb-rename-input"
                maxlength="240"
                @blur="saveRename"
                @keydown.enter.prevent="saveRename"
                @keydown.esc.prevent="renaming = false"
              />
              <h2 v-else @dblclick="startRename">{{ activeDocument.title }}</h2>
              <div class="kb-reader-meta">
                <span>{{ activeDocument.filename }}</span>
                <span>·</span>
                <span>{{ formatNumber(activeDocument.char_count) }} 字符</span>
                <span>·</span>
                <span>{{ activeDocument.chunk_count || 0 }} 个检索切片</span>
              </div>
            </div>
            <div class="kb-reader-actions">
              <button class="kb-icon-btn" type="button" aria-label="重命名资料" title="重命名" @click="startRename"><Pencil :size="15" /></button>
              <button class="kb-icon-btn danger" :class="{ armed: documentDeleteArmed }" type="button" aria-label="删除资料" :title="documentDeleteArmed ? '再次点击确认删除' : '删除资料'" @click="armDocumentDelete">
                <Trash2 :size="15" />
                <span v-if="documentDeleteArmed">确认</span>
              </button>
            </div>
          </header>

          <div ref="readerScroll" class="kb-reader-scroll" @scroll.passive="onReaderScroll">
            <article class="kb-paper">
              <div class="kb-paper-rule">
                <span>{{ fileKind(activeDocument.filename) }}</span>
                <span>SOURCE / {{ activeDocument.id.slice(0, 8).toUpperCase() }}</span>
              </div>
              <div class="kb-paper-heading">
                <span class="kb-paper-number">{{ padIndex(activeDocumentIndex + 1) }}</span>
                <div>
                  <h1>{{ activeDocument.title }}</h1>
                  <p>正在读原文 · 命中片段会保留上下文与阅读位置</p>
                </div>
              </div>
              <div v-if="highlightedChunk" class="kb-located-banner">
                <Search :size="13" />
                <span>已定位检索片段</span>
                <small>字符 {{ highlightedChunk.start }}–{{ highlightedChunk.end }}</small>
                <button type="button" @click="highlightRange = null">收起标记</button>
              </div>

              <section v-for="block in displayBlocks" :key="block.index" class="kb-text-block" :class="{ located: block.hit }">
                <span class="kb-text-number">{{ padIndex(block.index + 1) }}</span>
                <pre v-if="block.hit"><span>{{ block.before }}</span><mark class="kb-block-hit">{{ block.hit }}</mark><span>{{ block.after }}</span></pre>
                <pre v-else>{{ block.text }}</pre>
              </section>
            </article>
          </div>

          <footer class="kb-reader-foot">
            <span>{{ readProgress }}% READ</span>
            <div class="kb-progress-track"><span :style="{ width: readProgress + '%' }"></span></div>
            <span>{{ padIndex(activeDocumentIndex + 1) }} / {{ padIndex(documents.length) }}</span>
          </footer>
        </template>
      </main>

      <aside class="kb-rail" :class="{ collapsed: railCollapsed }">
        <div class="kb-rail-head">
          <div v-if="!railCollapsed" class="kb-rail-heading">
            <span class="kb-section-code">03 / RETRIEVAL</span>
            <strong>检索轨道</strong>
          </div>
          <span v-else class="kb-rail-vertical">RETRIEVAL</span>
          <button class="kb-icon-btn" type="button" :aria-label="railCollapsed ? '展开' : '收起'" @click="toggleRail">
            <PanelRightOpen v-if="railCollapsed" :size="16" />
            <PanelRightClose v-else :size="16" />
          </button>
        </div>

        <div v-if="!railCollapsed" class="kb-rail-body">
          <div class="kb-rail-scroll">
            <section class="kb-search-section">
              <div class="kb-rail-section-title">
                <span><Search :size="13" />全文检索</span>
                <button type="button" @click="toggleSearchScope">
                  {{ searchAcrossLibraries ? '全部资料' : '当前资料库' }}
                </button>
              </div>
              <form class="kb-search-form" @submit.prevent="submitSearch">
                <Search :size="14" />
                <input ref="searchInput" v-model="searchQuery" placeholder="在资料中找一个词" aria-label="全文检索">
                <button type="submit" :disabled="searching || !searchQuery.trim()" aria-label="开始检索">
                  <LoaderCircle v-if="searching" class="spin" :size="14" />
                  <CornerDownLeft v-else :size="14" />
                </button>
              </form>
              <div class="kb-search-meta">
                <span>{{ searchSubmitted ? `${searchResults.length} 个命中片段` : '按 / 聚焦检索' }}</span>
                <button v-if="searchQuery.trim()" type="button" @click="askFromSearch">转成问题 <ChevronRight :size="11" /></button>
              </div>
              <div v-if="searchError" class="kb-inline-error">{{ searchError }}</div>
              <div v-else-if="searchSubmitted && !searchResults.length && !searching" class="kb-no-results">
                <span>没有命中片段</span>
                <small>试试更短的关键词，或切换到全部资料。</small>
              </div>
              <div v-else-if="searchResults.length" class="kb-search-results">
                <button v-for="(result, index) in searchResults" :key="result.chunk.id" class="kb-result-row" type="button" @click="jumpToSource(result)">
                  <span class="kb-result-index">{{ padIndex(index + 1) }}</span>
                  <span class="kb-result-copy">
                    <span class="kb-result-title">{{ result.document.title }}<small>{{ formatScore(result.score) }}</small></span>
                    <span class="kb-result-snippet">{{ result.snippet }}</span>
                    <span class="kb-result-meta">{{ result.library.name }} · 片段 {{ result.chunk.ordinal + 1 }}</span>
                  </span>
                  <Quote :size="13" class="kb-result-quote" />
                </button>
              </div>
            </section>

            <section class="kb-ask-section">
              <div class="kb-rail-section-title ask">
                <span><Sparkles :size="13" />资料问答</span>
                <small>{{ totalAskSources }} 引用</small>
              </div>
              <div ref="messageScroll" class="kb-ask-stream">
                <div v-if="!askMessages.length" class="kb-ask-empty">
                  <div class="kb-ask-orbit"><span></span><Sparkles :size="18" /></div>
                  <strong>让答案落回资料</strong>
                  <p>问题会先经过全文切片检索，再交给模型组织回答。</p>
                  <div class="kb-suggestions">
                    <button type="button" @click="useSuggestion('这份资料的核心结论是什么？')">核心结论</button>
                    <button type="button" @click="useSuggestion('把资料中的关键步骤整理成清单。')">整理步骤</button>
                    <button type="button" @click="useSuggestion('资料里有哪些值得继续追查的线索？')">追查线索</button>
                  </div>
                </div>

                <article v-for="(message, index) in askMessages" :key="index" class="kb-message" :class="message.role">
                  <template v-if="message.role === 'user'">
                    <span class="kb-message-mark">Q</span>
                    <p>{{ message.content }}</p>
                  </template>
                  <template v-else>
                    <div class="kb-answer-head">
                      <span><Sparkles :size="13" />{{ message.model || '资料助手' }}</span>
                      <small v-if="message.sources.length">{{ message.sources.length }} 条来源</small>
                    </div>
                    <div v-if="message.content" class="kb-answer-copy">
                      <template v-for="(part, partIndex) in citationParts(message.content)" :key="partIndex">
                        <button v-if="part.type === 'citation'" class="kb-citation" type="button" @click="openCitation(message, part.index)">[{{ part.number }}]</button>
                        <span v-else>{{ part.text }}</span>
                      </template>
                    </div>
                    <span v-else-if="askBusy && index === askMessages.length - 1" class="kb-thinking"><i></i><i></i><i></i> 正在检索与组织</span>
                    <div v-if="message.sources.length" class="kb-source-list">
                      <button v-for="(source, sourceIndex) in message.sources" :key="source.chunk.id" type="button" @click="openCitation(message, sourceIndex)">
                        <span>[{{ sourceIndex + 1 }}]</span>
                        <strong>{{ source.document.title }}</strong>
                        <small>{{ source.snippet }}</small>
                      </button>
                    </div>
                    <div v-if="message.error" class="kb-inline-error">
                      {{ message.error }}
                      <button type="button" @click="retryLastAsk">重新提问</button>
                    </div>
                  </template>
                </article>
              </div>

              <form class="kb-ask-composer" @submit.prevent="submitAsk">
                <textarea ref="askInput" v-model="askDraft" rows="2" maxlength="2000" placeholder="基于当前资料提问…" aria-label="基于资料提问"></textarea>
                <div class="kb-composer-foot">
                  <span>{{ searchAcrossLibraries ? '范围：全部资料' : `范围：${activeLibrary?.name || '当前资料库'}` }}</span>
                  <span class="kb-composer-hint">Ctrl ↵</span>
                  <button v-if="askBusy" class="kb-stop-btn" type="button" aria-label="停止生成" @click="stopAsk"><CircleStop :size="15" /></button>
                  <button v-else class="kb-send-btn" type="submit" :disabled="!askDraft.trim() || !stats.documents" aria-label="发送问题"><Send :size="15" /></button>
                </div>
              </form>
            </section>
          </div>
        </div>
      </aside>
    </div>

    <input
      ref="fileInput"
      class="kb-file-input"
      type="file"
      multiple
      accept=".txt,.md,.markdown,.csv,.json,.html,.htm,text/plain,text/markdown,text/csv,application/json,text/html"
      @change="onFileInput"
    >

    <div v-if="dragActive" class="kb-drop-layer" aria-live="polite">
      <div class="kb-drop-target">
        <FileUp :size="30" />
        <strong>把资料落到这里</strong>
        <span>TXT · Markdown · CSV · JSON · HTML</span>
        <small>导入后会立即切分并建立本地索引</small>
      </div>
    </div>

    <div v-if="importing" class="kb-import-status">
      <LoaderCircle class="spin" :size="14" />
      <span>{{ importLabel }}</span>
    </div>
  </div>
</template>