<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  ArrowLeft, AudioLines, Check, CircleAlert, CircleStop, Clock3, Download,
  FileAudio, FileText, Film, Image as ImageIcon, Images, LoaderCircle, Pencil,
  RefreshCw, Scissors, Search, Sparkles, Trash2, Upload, Video, X,
} from 'lucide-vue-next'
import { api } from '../lib/api.js'
import { useUiStore } from '../stores/ui.js'

const ui = useUiStore()
const media = ref([])
const detail = ref(null)
const stats = reactive({
  items: 0, images: 0, audio: 0, videos: 0, bytes: 0, duration_ms: 0,
  artifacts: 0, added_today: 0,
})
const loading = ref(true)
const detailLoading = ref(false)
const selectedId = ref(null)
const mobilePane = ref('library')
const searchQuery = ref('')
const mediaFilter = ref('all')
const searchInput = ref(null)
const fileInput = ref(null)
const renameInput = ref(null)

const renameEditing = ref(false)
const renameDraft = ref('')
const renameSaving = ref(false)
const deleteArmed = ref(false)
const artifactDeleteArmed = ref(null)
const deleteTimer = ref(null)
const artifactDeleteTimer = ref(null)

const uploadQueue = ref([])
const uploadRunning = ref(false)
const dragActive = ref(false)
let dragDepth = 0
let uploadSequence = 0

const trimDraft = reactive({ mediaId: '', start: '0', end: '' })
const subtitlePreview = reactive({
  artifact: null, text: '', loading: false, error: '',
})
const artifactPreview = ref(null)

let pollTimer = null
let searchTimer = null
let listSequence = 0
let detailSequence = 0

const selectedMedia = computed(
  () => detail.value?.media || media.value.find((item) => item.id === selectedId.value) || null,
)
const artifacts = computed(() => detail.value?.artifacts || [])
const tasks = computed(() => detail.value?.tasks || [])
const activeTask = computed(() => tasks.value.find((task) => taskIsActive(task)) || null)
const isBusy = computed(
  () => Boolean(activeTask.value) || selectedMedia.value?.status === 'processing',
)
const selectedIndex = computed(
  () => media.value.findIndex((item) => item.id === selectedId.value),
)
const artifactPreviewUrl = computed(() => artifactPreview.value?.file_url || '')
const queuedCount = computed(
  () => uploadQueue.value.filter((item) => item.status === 'queued').length,
)
const uploadFinished = computed(
  () => uploadQueue.value.filter((item) => ['done', 'error'].includes(item.status)).length,
)
const polling = computed(() => {
  if (selectedMedia.value?.status === 'processing') return true
  return tasks.value.some((task) => taskIsActive(task))
})

const filterOptions = [
  { key: 'all', label: '全部', icon: Images },
  { key: 'image', label: '图片', icon: ImageIcon },
  { key: 'audio', label: '音频', icon: AudioLines },
  { key: 'video', label: '视频', icon: Video },
]

const actionMeta = {
  extract_audio: {
    index: '01', label: '提取音轨', short: '转成可复用的 MP3', icon: FileAudio,
  },
  transcode: {
    index: '02', label: '转码 MP4', short: 'H.264 + AAC 兼容输出', icon: Film,
  },
  trim: {
    index: '03', label: '裁剪片段', short: '按秒截取音视频', icon: Scissors,
  },
  transcribe: {
    index: '04', label: '生成字幕', short: '可选 ASR 输出 SRT', icon: Sparkles,
  },
}

function formatNumber(value) {
  return new Intl.NumberFormat('zh-CN').format(Number(value || 0))
}

function formatBytes(value) {
  const size = Number(value || 0)
  if (!size) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatTimecode(value) {
  const ms = Number(value || 0)
  if (!ms) return '--:--'
  const total = Math.max(0, Math.floor(ms / 1000))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function formatDate(value) {
  if (!value) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知'
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return `今天 ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  }
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

function mediaTypeLabel(type) {
  return { image: '图片', audio: '音频', video: '视频' }[type] || '媒体'
}

function mediaIcon(type) {
  return { image: ImageIcon, audio: AudioLines, video: Video }[type] || Film
}

function mediaIndex(item) {
  const index = media.value.findIndex((entry) => entry.id === item.id)
  return String(Math.max(1, index + 1)).padStart(2, '0')
}

function mediaMeasure(item) {
  if (!item) return '—'
  if (item.media_type === 'image' && item.width && item.height) {
    return `${item.width}×${item.height}`
  }
  if (item.duration_ms) return formatTimecode(item.duration_ms)
  return item.extension?.replace('.', '').toUpperCase() || '—'
}

function statusLabel(status) {
  return {
    ready: '可用', processing: '处理中', error: '有错误', pending: '排队中',
    running: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消',
  }[status] || status || '未知'
}

function taskLabel(task) {
  return {
    'media.extract_audio': '提取音轨', 'media.transcode': '转码 MP4',
    'media.trim': '裁剪片段', 'media.transcribe': '生成字幕',
  }[task?.type] || '媒体处理'
}

function taskIsActive(task) {
  return ['pending', 'running'].includes(task?.status)
}

function taskProgress(task) {
  return Math.max(0, Math.min(100, Math.round(Number(task?.progress || 0))))
}

function artifactKindLabel(kind) {
  return { audio: '音轨', video: '视频', clip: '片段', subtitle: '字幕' }[kind] || '产物'
}

function artifactIcon(artifact) {
  if (artifact.kind === 'subtitle') return FileText
  if (artifact.kind === 'audio') return FileAudio
  return Film
}

function capability(action) {
  const item = selectedMedia.value
  if (!item) return { ok: false, reason: '请先选择素材' }
  if (isBusy.value) return { ok: false, reason: '请等待当前任务结束或先取消' }
  if (action === 'extract_audio' || action === 'transcribe') {
    if (item.media_type === 'image' || !item.audio_codec) {
      return { ok: false, reason: '该素材没有可处理的音轨' }
    }
  }
  if (action === 'transcode' && item.media_type !== 'video') {
    return { ok: false, reason: '仅视频支持 MP4 转码' }
  }
  if (action === 'trim' && item.media_type === 'image') {
    return { ok: false, reason: '图片不支持时间裁剪' }
  }
  if (action === 'trim' && !Number(item.duration_ms)) {
    return { ok: false, reason: '该素材缺少可用的时长信息' }
  }
  return { ok: true, reason: '' }
}

function showError(error) {
  ui.showToast(error?.message || String(error || '操作失败'))
}

function updateMediaInList(item, artifactCount) {
  if (!item) return
  const index = media.value.findIndex((entry) => entry.id === item.id)
  const next = artifactCount == null ? item : { ...item, artifact_count: artifactCount }
  if (index >= 0) media.value[index] = { ...media.value[index], ...next }
}

async function refreshStats() {
  try {
    const data = await api('/api/media/stats')
    Object.assign(stats, data.stats || {})
  } catch (error) {
    showError(error)
  }
}

async function loadMedia({ preserveSelection = true } = {}) {
  const sequence = ++listSequence
  try {
    const params = new URLSearchParams({ limit: '200' })
    if (searchQuery.value.trim()) params.set('q', searchQuery.value.trim())
    if (mediaFilter.value !== 'all') params.set('media_type', mediaFilter.value)
    const data = await api(`/api/media?${params.toString()}`)
    if (sequence !== listSequence) return
    media.value = data.media || []
    const previous = preserveSelection ? selectedId.value : null
    const next = media.value.find((item) => item.id === previous) || media.value[0] || null
    selectedId.value = next?.id || null
    if (!selectedId.value) {
      detail.value = null
      artifactPreview.value = null
    } else if (detail.value?.media?.id !== selectedId.value) {
      await loadDetail(selectedId.value)
    }
  } catch (error) {
    showError(error)
  } finally {
    if (sequence === listSequence) loading.value = false
  }
}

async function loadDetail(id, { quiet = false } = {}) {
  if (!id) return
  const sequence = ++detailSequence
  if (!quiet) detailLoading.value = true
  try {
    const data = await api(`/api/media/${id}`)
    if (sequence !== detailSequence || selectedId.value !== id) return
    detail.value = data
    updateMediaInList(data.media, (data.artifacts || []).length)
    if (trimDraft.mediaId !== id) {
      trimDraft.mediaId = id
      trimDraft.start = '0'
      trimDraft.end = data.media?.duration_ms
        ? String((data.media.duration_ms / 1000).toFixed(2)).replace(/\.00$/, '')
        : ''
    }
    if (artifactPreview.value && !(data.artifacts || []).some(
      (artifact) => artifact.id === artifactPreview.value.id,
    )) artifactPreview.value = null
  } catch (error) {
    if (!quiet) showError(error)
  } finally {
    if (sequence === detailSequence) detailLoading.value = false
  }
}

async function reloadAll({ preserveSelection = true } = {}) {
  await Promise.allSettled([loadMedia({ preserveSelection }), refreshStats()])
}

async function selectMedia(item) {
  if (!item) return
  selectedId.value = item.id
  renameEditing.value = false
  deleteArmed.value = false
  subtitlePreview.artifact = null
  subtitlePreview.text = ''
  subtitlePreview.error = ''
  await loadDetail(item.id)
  if (window.matchMedia('(max-width: 920px)').matches) mobilePane.value = 'preview'
}

function startRename() {
  if (!selectedMedia.value) return
  renameDraft.value = selectedMedia.value.title
  renameEditing.value = true
  nextTick(() => {
    renameInput.value?.focus()
    renameInput.value?.select()
  })
}

function cancelRename() {
  renameEditing.value = false
  renameDraft.value = ''
}

async function saveRename() {
  const title = renameDraft.value.trim()
  if (!selectedMedia.value || !title || title === selectedMedia.value.title) {
    cancelRename()
    return
  }
  renameSaving.value = true
  try {
    const data = await api(`/api/media/${selectedMedia.value.id}`, {
      method: 'PATCH', body: JSON.stringify({ title }),
    })
    if (detail.value?.media) detail.value.media = { ...detail.value.media, ...data.media }
    updateMediaInList(data.media, artifacts.value.length)
    renameEditing.value = false
    ui.showToast('素材名称已更新')
  } catch (error) {
    showError(error)
  } finally {
    renameSaving.value = false
  }
}

function armDelete() {
  if (!selectedMedia.value) return
  if (!deleteArmed.value) {
    deleteArmed.value = true
    clearTimeout(deleteTimer.value)
    deleteTimer.value = setTimeout(() => { deleteArmed.value = false }, 3500)
    ui.showToast('再次点击删除素材，产物将一并移除')
    return
  }
  deleteSelectedMedia()
}

async function deleteSelectedMedia() {
  if (!selectedMedia.value) return
  const id = selectedMedia.value.id
  clearTimeout(deleteTimer.value)
  deleteArmed.value = false
  try {
    await api(`/api/media/${id}`, { method: 'DELETE' })
    detail.value = null
    artifactPreview.value = null
    selectedId.value = null
    ui.showToast('素材已删除')
    await reloadAll({ preserveSelection: false })
  } catch (error) {
    showError(error)
  }
}

async function runAction(action) {
  const item = selectedMedia.value
  const check = capability(action)
  if (!item || !check.ok) {
    ui.showToast(check.reason || '当前素材不支持该操作')
    return
  }
  const body = { action }
  if (action === 'trim') {
    const start = Number(trimDraft.start || 0)
    const end = trimDraft.end === '' ? null : Number(trimDraft.end)
    if (!Number.isFinite(start) || start < 0) {
      ui.showToast('裁剪开始时间必须是非负秒数')
      return
    }
    if (end !== null && (!Number.isFinite(end) || end <= start)) {
      ui.showToast('裁剪结束时间必须晚于开始时间')
      return
    }
    body.start_ms = Math.round(start * 1000)
    body.end_ms = end === null ? null : Math.round(end * 1000)
  }
  try {
    const data = await api(`/api/media/${item.id}/tasks`, {
      method: 'POST', body: JSON.stringify(body),
    })
    if (detail.value) {
      detail.value.tasks = [data.task, ...(detail.value.tasks || [])]
      detail.value.media = data.media || detail.value.media
    }
    updateMediaInList(data.media, artifacts.value.length)
    ui.showToast(`${actionMeta[action].label}已加入处理队列`)
    if (window.matchMedia('(max-width: 920px)').matches) mobilePane.value = 'processing'
  } catch (error) {
    showError(error)
  }
}

async function cancelTask(task) {
  if (!task || !taskIsActive(task)) return
  try {
    await api(`/api/media/tasks/${task.id}/cancel`, { method: 'POST' })
    ui.showToast('任务已取消')
    await loadDetail(selectedId.value, { quiet: true })
    await refreshStats()
  } catch (error) {
    showError(error)
  }
}

function armArtifactDelete(artifact) {
  if (artifactDeleteArmed.value === artifact.id) {
    deleteArtifact(artifact)
    return
  }
  artifactDeleteArmed.value = artifact.id
  clearTimeout(artifactDeleteTimer.value)
  artifactDeleteTimer.value = setTimeout(() => { artifactDeleteArmed.value = null }, 3500)
  ui.showToast('再次点击删除该产物')
}

async function deleteArtifact(artifact) {
  clearTimeout(artifactDeleteTimer.value)
  artifactDeleteArmed.value = null
  try {
    await api(`/api/media/artifacts/${artifact.id}`, { method: 'DELETE' })
    if (detail.value) {
      detail.value.artifacts = (detail.value.artifacts || []).filter(
        (item) => item.id !== artifact.id,
      )
      if (detail.value.media) {
        detail.value.media.artifact_count = detail.value.artifacts.length
        updateMediaInList(detail.value.media, detail.value.artifacts.length)
      }
    }
    if (artifactPreview.value?.id === artifact.id) artifactPreview.value = null
    if (subtitlePreview.artifact?.id === artifact.id) {
      subtitlePreview.artifact = null
      subtitlePreview.text = ''
    }
    ui.showToast('产物已删除')
    await refreshStats()
  } catch (error) {
    showError(error)
  }
}

async function previewArtifact(artifact) {
  if (window.matchMedia('(max-width: 920px)').matches) mobilePane.value = 'processing'
  if (artifact.kind !== 'subtitle') {
    artifactPreview.value = artifact
    return
  }
  artifactPreview.value = null
  subtitlePreview.artifact = artifact
  subtitlePreview.text = ''
  subtitlePreview.error = ''
  subtitlePreview.loading = true
  try {
    const response = await fetch(artifact.file_url)
    if (!response.ok) throw new Error('字幕文件读取失败')
    subtitlePreview.text = await response.text()
  } catch (error) {
    subtitlePreview.error = error.message || '字幕文件读取失败'
  } finally {
    subtitlePreview.loading = false
  }
}

function onFileInput(event) {
  enqueueFiles(event.target.files)
  event.target.value = ''
}

function enqueueFiles(fileList) {
  const files = Array.from(fileList || []).filter((file) => file instanceof File)
  if (!files.length) return
  const additions = files.map((file) => ({
    id: `upload-${Date.now()}-${++uploadSequence}`,
    file, name: file.name, size: file.size, progress: 0, status: 'queued', error: '',
  }))
  uploadQueue.value.push(...additions)
  processUploadQueue()
}

function patchQueueItem(id, patch) {
  uploadQueue.value = uploadQueue.value.map(
    (item) => (item.id === id ? { ...item, ...patch } : item),
  )
}

function uploadFile(item) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const params = new URLSearchParams({ filename: item.name })
    xhr.open('POST', `/api/media/upload?${params.toString()}`)
    xhr.setRequestHeader('Content-Type', item.file.type || 'application/octet-stream')
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return
      patchQueueItem(item.id, {
        progress: Math.min(99, Math.round((event.loaded / event.total) * 100)),
      })
    }
    xhr.onload = () => {
      let data = {}
      try {
        data = JSON.parse(xhr.responseText || '{}')
      } catch {
        data = {}
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data.media)
        return
      }
      const detailValue = data.detail || data.error || {}
      const message = detailValue.message || detailValue.title || data.detail || '上传失败'
      reject(new Error(typeof message === 'string' ? message : JSON.stringify(message)))
    }
    xhr.onerror = () => reject(new Error('网络连接中断，上传失败'))
    xhr.onabort = () => reject(new Error('上传已中断'))
    xhr.send(item.file)
  })
}

async function processUploadQueue() {
  if (uploadRunning.value) return
  uploadRunning.value = true
  let latestMediaId = null
  try {
    for (;;) {
      const item = uploadQueue.value.find((entry) => entry.status === 'queued')
      if (!item) break
      patchQueueItem(item.id, { status: 'uploading', progress: 0, error: '' })
      try {
        const uploaded = await uploadFile(item)
        latestMediaId = uploaded?.id || latestMediaId
        patchQueueItem(item.id, { status: 'done', progress: 100, mediaId: uploaded?.id })
      } catch (error) {
        patchQueueItem(item.id, {
          status: 'error', error: error?.message || '上传失败',
        })
      }
    }
  } finally {
    uploadRunning.value = false
  }
  await refreshStats()
  if (latestMediaId) {
    searchQuery.value = ''
    mediaFilter.value = 'all'
    await loadMedia({ preserveSelection: false })
    selectedId.value = latestMediaId
    await loadDetail(latestMediaId)
    ui.showToast('素材已加入媒体库')
  } else {
    await loadMedia()
  }
}

function clearFinishedUploads() {
  uploadQueue.value = uploadQueue.value.filter(
    (item) => !['done', 'error'].includes(item.status),
  )
}

function onDragEnter(event) {
  if (!event.dataTransfer?.types?.includes('Files')) return
  event.preventDefault()
  dragDepth += 1
  dragActive.value = true
}

function onDragLeave(event) {
  if (!event.dataTransfer?.types?.includes('Files')) return
  event.preventDefault()
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dragActive.value = false
}

function onDrop(event) {
  dragDepth = 0
  dragActive.value = false
  enqueueFiles(event.dataTransfer?.files)
}

function onDragOver(event) {
  if (event.dataTransfer?.types?.includes('Files')) event.preventDefault()
}

function focusSearch() {
  searchInput.value?.focus()
}

function onKeydown(event) {
  const tag = document.activeElement?.tagName
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)
  if (event.altKey && ['1', '2', '3'].includes(event.key)) {
    event.preventDefault()
    mobilePane.value = ['library', 'preview', 'processing'][Number(event.key) - 1]
  }
  if (event.key === '/' && !typing) {
    event.preventDefault()
    focusSearch()
  }
}

watch([searchQuery, mediaFilter], () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadMedia(), 260)
})

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  await reloadAll({ preserveSelection: false })
  pollTimer = setInterval(() => {
    if (selectedId.value && polling.value) loadDetail(selectedId.value, { quiet: true })
  }, 900)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  clearInterval(pollTimer)
  clearTimeout(searchTimer)
  clearTimeout(deleteTimer.value)
  clearTimeout(artifactDeleteTimer.value)
})
</script>

<template>
  <div
    class="media-ws"
    :data-pane="mobilePane"
    @dragenter="onDragEnter"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop.prevent="onDrop"
  >
    <header class="media-topbar">
      <button class="media-back" type="button" aria-label="返回首页" @click="ui.goHome()">
        <ArrowLeft :size="16" />
      </button>
      <div class="media-title-group">
        <span class="media-eyebrow">MEDIA WORKBENCH · C05</span>
        <h1>媒体工坊</h1>
      </div>
      <div class="media-top-metrics" aria-label="媒体统计">
        <span><b>{{ formatNumber(stats.items) }}</b> 素材</span>
        <span><b>{{ formatNumber(stats.artifacts) }}</b> 产物</span>
        <span><b>{{ formatBytes(stats.bytes) }}</b> 占用</span>
        <span><b>{{ formatNumber(stats.added_today) }}</b> 今日加入</span>
      </div>
      <div class="media-top-actions">
        <button
          class="media-icon-btn"
          type="button"
          :disabled="loading"
          title="刷新媒体库"
          @click="reloadAll()"
        >
          <RefreshCw :class="{ 'media-spin': loading }" :size="15" />
        </button>
        <button class="media-upload-top" type="button" @click="fileInput?.click()">
          <Upload :size="14" /><span>上传素材</span>
        </button>
      </div>
    </header>

    <nav class="media-mobile-nav" aria-label="媒体工坊页面">
      <button
        v-for="pane in [
          { key: 'library', mark: '01', label: '媒体', count: stats.items },
          { key: 'preview', mark: '02', label: '预览', count: 0 },
          { key: 'processing', mark: '03', label: '处理', count: artifacts.length },
        ]"
        :key="pane.key"
        type="button"
        :class="{ active: mobilePane === pane.key }"
        @click="mobilePane = pane.key"
      >
        <span>{{ pane.mark }}</span>{{ pane.label }}<small v-if="pane.count">{{ pane.count }}</small>
      </button>
    </nav>

    <div class="media-body">
      <aside class="media-library">
        <div class="media-pane-head">
          <div><span>媒体库索引</span><small>LIBRARY INDEX</small></div>
          <b>{{ formatNumber(media.length) }}</b>
        </div>
        <div class="media-stat-strip">
          <span><ImageIcon :size="12" />{{ stats.images }}</span>
          <span><AudioLines :size="12" />{{ stats.audio }}</span>
          <span><Video :size="12" />{{ stats.videos }}</span>
          <span><Clock3 :size="12" />{{ formatTimecode(stats.duration_ms) }}</span>
        </div>

        <label class="media-search">
          <Search :size="14" />
          <input
            ref="searchInput"
            v-model="searchQuery"
            type="search"
            placeholder="搜索标题或文件名"
            aria-label="搜索媒体"
          >
          <kbd>/</kbd>
        </label>

        <div class="media-filter-row" role="tablist" aria-label="媒体类型筛选">
          <button
            v-for="filter in filterOptions"
            :key="filter.key"
            type="button"
            :class="{ active: mediaFilter === filter.key }"
            @click="mediaFilter = filter.key"
          >
            <component :is="filter.icon" :size="12" />{{ filter.label }}
          </button>
        </div>

        <div class="media-index-list">
          <button
            v-for="item in media"
            :key="item.id"
            class="media-index-row"
            :class="{ active: item.id === selectedId }"
            type="button"
            @click="selectMedia(item)"
          >
            <span class="media-index-no">{{ mediaIndex(item) }}</span>
            <span class="media-index-icon">
              <component :is="mediaIcon(item.media_type)" :size="15" />
            </span>
            <span class="media-index-copy">
              <strong>{{ item.title }}</strong>
              <small>{{ mediaTypeLabel(item.media_type) }} · {{ mediaMeasure(item) }} · {{ formatBytes(item.size) }}</small>
            </span>
            <span class="media-index-tail">
              <span v-if="item.artifact_count" class="media-count-badge">{{ item.artifact_count }}</span>
              <span v-if="item.status === 'processing'" class="media-live-dot" title="处理中"></span>
            </span>
          </button>

          <div v-if="loading" class="media-index-empty">
            <LoaderCircle class="media-spin" :size="17" /><span>正在读取媒体索引</span>
          </div>
          <div v-else-if="!media.length" class="media-index-empty">
            <Images :size="22" />
            <strong>{{ searchQuery || mediaFilter !== 'all' ? '没有匹配的素材' : '媒体库还是空的' }}</strong>
            <span>{{ searchQuery || mediaFilter !== 'all' ? '换个关键词或筛选条件' : '拖入文件，或从上传按钮开始' }}</span>
          </div>
        </div>

        <button class="media-upload-trigger" type="button" @click="fileInput?.click()">
          <span class="media-upload-mark"><Upload :size="15" /></span>
          <span><strong>拖拽或选择文件</strong><small>图片 · 音频 · 视频 / 单文件上限 512 MiB</small></span>
        </button>
      </aside>

      <section class="media-canvas">
        <div v-if="selectedMedia" class="media-canvas-inner">
          <div class="media-canvas-head">
            <div class="media-canvas-heading">
              <span class="media-canvas-rule">PREVIEW / {{ mediaTypeLabel(selectedMedia.media_type).toUpperCase() }} / {{ String(Math.max(1, selectedIndex + 1)).padStart(2, '0') }}</span>
              <div v-if="renameEditing" class="media-rename-row">
                <input
                  ref="renameInput"
                  v-model="renameDraft"
                  maxlength="240"
                  aria-label="素材名称"
                  @keydown.enter.prevent="saveRename"
                  @keydown.esc.prevent="cancelRename"
                >
                <button type="button" :disabled="renameSaving || !renameDraft.trim()" @click="saveRename">
                  <LoaderCircle v-if="renameSaving" class="media-spin" :size="14" />
                  <Check v-else :size="14" />
                </button>
                <button type="button" @click="cancelRename"><X :size="14" /></button>
              </div>
              <h2 v-else>{{ selectedMedia.title }}</h2>
              <p>{{ selectedMedia.filename }} · {{ formatBytes(selectedMedia.size) }} · {{ formatDate(selectedMedia.created_at) }}</p>
            </div>
            <div class="media-canvas-actions">
              <button type="button" title="重命名" @click="startRename"><Pencil :size="14" /><span>重命名</span></button>
              <a :href="selectedMedia.download_url" title="下载原文件"><Download :size="14" /><span>下载</span></a>
              <button
                type="button"
                class="danger"
                :class="{ armed: deleteArmed }"
                :disabled="isBusy"
                @click="armDelete"
              >
                <Trash2 :size="14" /><span>{{ deleteArmed ? '确认删除' : '删除' }}</span>
              </button>
            </div>
          </div>

          <div class="media-preview-stage" :data-kind="selectedMedia.media_type">
            <div class="media-preview-grid" aria-hidden="true"></div>
            <img
              v-if="selectedMedia.media_type === 'image'"
              class="media-preview-image"
              :src="selectedMedia.preview_url"
              :alt="selectedMedia.title"
            >
            <video
              v-else-if="selectedMedia.media_type === 'video'"
              class="media-preview-video"
              :src="selectedMedia.preview_url"
              controls
              preload="metadata"
            ></video>
            <div v-else class="media-audio-stage">
              <div class="media-audio-rings"><AudioLines :size="40" /></div>
              <div class="media-waveform" aria-hidden="true">
                <span v-for="bar in 18" :key="bar"></span>
              </div>
              <audio :src="selectedMedia.preview_url" controls preload="metadata"></audio>
            </div>
            <div class="media-preview-stamp">
              <span>{{ mediaTypeLabel(selectedMedia.media_type) }}</span>
              <b>{{ mediaMeasure(selectedMedia) }}</b>
              <small>{{ selectedMedia.extension?.replace('.', '').toUpperCase() }}</small>
            </div>
            <div v-if="isBusy" class="media-processing-veil">
              <LoaderCircle class="media-spin" :size="20" />
              <span>{{ activeTask ? taskLabel(activeTask) : '处理中' }}</span>
              <b v-if="activeTask">{{ taskProgress(activeTask) }}%</b>
            </div>
          </div>

          <div class="media-meta-strip">
            <div><span>文件</span><strong>{{ selectedMedia.extension?.replace('.', '').toUpperCase() || '—' }}</strong></div>
            <div><span>大小</span><strong>{{ formatBytes(selectedMedia.size) }}</strong></div>
            <div><span>时长</span><strong>{{ formatTimecode(selectedMedia.duration_ms) }}</strong></div>
            <div><span>尺寸</span><strong>{{ selectedMedia.width ? `${selectedMedia.width}×${selectedMedia.height}` : '—' }}</strong></div>
            <div><span>视频编码</span><strong>{{ selectedMedia.video_codec || '—' }}</strong></div>
            <div><span>音频编码</span><strong>{{ selectedMedia.audio_codec || '—' }}</strong></div>
            <div><span>产物</span><strong>{{ artifacts.length }}</strong></div>
            <div class="media-meta-wide"><span>SHA-256</span><strong>{{ selectedMedia.sha256 || '—' }}</strong></div>
          </div>

          <div v-if="selectedMedia.error" class="media-media-error">
            <CircleAlert :size="15" /><span>{{ selectedMedia.error }}</span>
          </div>
        </div>

        <div v-else class="media-canvas-empty">
          <div class="media-empty-orbit"><Upload :size="28" /><i></i><i></i></div>
          <span class="media-eyebrow">NO MEDIA SELECTED</span>
          <h2>把素材放到工作台上</h2>
          <p>拖入图片、音频或视频；原始文件、处理任务和全部产物会留在同一条轨道里。</p>
          <div class="media-empty-actions">
            <button type="button" @click="fileInput?.click()"><Upload :size="14" />选择文件</button>
            <button type="button" @click="focusSearch"><Search :size="14" />查找素材</button>
          </div>
        </div>
      </section>

      <aside class="media-rail">
        <div class="media-pane-head">
          <div><span>处理与产物轨道</span><small>PROCESSING TRACK</small></div>
          <b>{{ tasks.length + artifacts.length }}</b>
        </div>

        <section class="media-rail-section">
          <div class="media-rail-title"><span>处理动作</span><small>PYAV PIPELINE</small></div>
          <div class="media-action-grid">
            <button
              v-for="(meta, action) in actionMeta"
              :key="action"
              type="button"
              :class="{ disabled: !capability(action).ok }"
              :title="capability(action).reason || meta.short"
              @click="runAction(action)"
            >
              <span class="media-action-index">{{ meta.index }}</span>
              <span class="media-action-copy">
                <strong>{{ meta.label }}</strong>
                <small>{{ capability(action).ok ? meta.short : capability(action).reason }}</small>
              </span>
              <component :is="meta.icon" :size="15" />
            </button>
          </div>

          <div class="media-trim-box" :class="{ disabled: !capability('trim').ok }">
            <div class="media-trim-head"><Scissors :size="13" /><span>裁剪区间</span><small>{{ formatTimecode(selectedMedia?.duration_ms) }}</small></div>
            <div class="media-trim-inputs">
              <label><span>开始 / 秒</span><input v-model="trimDraft.start" type="number" min="0" step="0.1"></label>
              <span>→</span>
              <label><span>结束 / 秒</span><input v-model="trimDraft.end" type="number" min="0" step="0.1" placeholder="结尾"></label>
            </div>
          </div>
        </section>

        <section v-if="activeTask" class="media-running-card">
          <div class="media-running-head">
            <span><LoaderCircle class="media-spin" :size="14" />{{ taskLabel(activeTask) }}</span>
            <b>{{ taskProgress(activeTask) }}%</b>
          </div>
          <div class="media-progress-track"><i :style="{ width: `${taskProgress(activeTask)}%` }"></i></div>
          <div class="media-running-foot">
            <span>{{ activeTask.status === 'pending' ? '等待 worker 接手' : (activeTask.payload?.start_ms != null ? `区间 ${formatTimecode(activeTask.payload.start_ms)} → ${formatTimecode(activeTask.payload.end_ms)}` : '正在写入产物') }}</span>
            <button type="button" @click="cancelTask(activeTask)"><CircleStop :size="13" />取消</button>
          </div>
        </section>

        <section class="media-rail-section media-task-section">
          <div class="media-rail-title"><span>任务记录</span><small>{{ tasks.length }}</small></div>
          <div class="media-task-list">
            <article v-for="task in tasks" :key="task.id" class="media-task-row" :class="task.status">
              <span class="media-task-status"><LoaderCircle v-if="taskIsActive(task)" class="media-spin" :size="12" /><Check v-else-if="task.status === 'succeeded'" :size="12" /><CircleAlert v-else-if="task.status === 'failed'" :size="12" /><CircleStop v-else :size="12" /></span>
              <div class="media-task-copy">
                <strong>{{ taskLabel(task) }}</strong>
                <small>{{ statusLabel(task.status) }} · {{ formatDate(task.created_at) }}</small>
                <div v-if="taskIsActive(task)" class="media-task-progress"><i :style="{ width: `${taskProgress(task)}%` }"></i></div>
                <p v-if="task.error">{{ task.error }}</p>
              </div>
              <button v-if="taskIsActive(task)" type="button" title="取消任务" @click="cancelTask(task)"><CircleStop :size="13" /></button>
            </article>
            <div v-if="!tasks.length" class="media-rail-empty"><Clock3 :size="14" />还没有处理任务</div>
          </div>
        </section>

        <section class="media-rail-section media-artifact-section">
          <div class="media-rail-title"><span>产物</span><small>{{ artifacts.length }} FILES</small></div>
          <div class="media-artifact-list">
            <article v-for="artifact in artifacts" :key="artifact.id" class="media-artifact-row">
              <button class="media-artifact-main" type="button" @click="previewArtifact(artifact)">
                <span class="media-artifact-icon"><component :is="artifactIcon(artifact)" :size="14" /></span>
                <span class="media-artifact-copy">
                  <strong>{{ artifact.filename }}</strong>
                  <small>{{ artifactKindLabel(artifact.kind) }} · {{ formatBytes(artifact.size) }}</small>
                </span>
              </button>
              <div class="media-artifact-actions">
                <a :href="artifact.download_url" :title="`下载 ${artifact.filename}`"><Download :size="13" /></a>
                <button
                  type="button"
                  :class="{ armed: artifactDeleteArmed === artifact.id }"
                  :title="artifactDeleteArmed === artifact.id ? '再次点击确认删除' : '删除产物'"
                  @click="armArtifactDelete(artifact)"
                >
                  <Trash2 :size="13" />
                </button>
              </div>
            </article>
            <div v-if="!artifacts.length" class="media-rail-empty"><Film :size="14" />处理完成后，产物会出现在这里</div>
          </div>

          <div v-if="artifactPreview" class="media-artifact-preview">
            <div class="media-artifact-preview-head"><span>{{ artifactPreview.filename }}</span><button type="button" @click="artifactPreview = null"><X :size="13" /></button></div>
            <audio v-if="artifactPreview.kind === 'audio'" :src="artifactPreviewUrl" controls preload="metadata"></audio>
            <video v-else-if="['video', 'clip'].includes(artifactPreview.kind)" :src="artifactPreviewUrl" controls preload="metadata"></video>
          </div>

          <div v-if="subtitlePreview.artifact" class="media-subtitle-preview">
            <div class="media-artifact-preview-head">
              <span>SRT · {{ subtitlePreview.artifact.filename }}</span>
              <button type="button" @click="subtitlePreview.artifact = null"><X :size="13" /></button>
            </div>
            <div v-if="subtitlePreview.loading" class="media-rail-empty"><LoaderCircle class="media-spin" :size="14" />正在读取字幕</div>
            <p v-else-if="subtitlePreview.error" class="media-subtitle-error">{{ subtitlePreview.error }}</p>
            <pre v-else>{{ subtitlePreview.text }}</pre>
          </div>
        </section>
      </aside>
    </div>

    <input
      ref="fileInput"
      class="media-file-input"
      type="file"
      multiple
      accept=".jpg,.jpeg,.png,.webp,.gif,.bmp,.mp3,.wav,.m4a,.aac,.ogg,.opus,.flac,.mp4,.m4v,.mov,.mkv,.webm,.avi,.flv,image/*,audio/*,video/*"
      @change="onFileInput"
    >

    <div v-if="dragActive" class="media-drop-layer" aria-live="polite">
      <div class="media-drop-target">
        <span class="media-drop-mark"><Upload :size="30" /></span>
        <strong>把素材放到媒体库</strong>
        <p>图片 · 音频 · 视频</p>
        <small>上传队列会逐个处理，并显示真实进度</small>
      </div>
    </div>

    <aside v-if="uploadQueue.length" class="media-upload-stack" aria-live="polite">
      <div class="media-upload-stack-head">
        <span><Upload :size="14" />上传队列 <b>{{ queuedCount || uploadFinished }}</b></span>
        <button
          v-if="!uploadRunning && uploadFinished"
          type="button"
          aria-label="清除已完成上传"
          @click="clearFinishedUploads"
        ><X :size="13" /></button>
      </div>
      <div class="media-upload-items">
        <article v-for="item in uploadQueue" :key="item.id" :class="item.status">
          <div class="media-upload-item-head">
            <span>{{ item.name }}</span>
            <small>{{ item.status === 'error' ? '失败' : item.status === 'done' ? '完成' : `${item.progress}%` }}</small>
          </div>
          <div class="media-upload-progress"><i :style="{ width: `${item.progress}%` }"></i></div>
          <p v-if="item.error">{{ item.error }}</p>
        </article>
      </div>
    </aside>
  </div>
</template>
