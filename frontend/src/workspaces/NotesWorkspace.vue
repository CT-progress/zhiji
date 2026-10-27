<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { api } from '../lib/api.js'
import { useUiStore } from '../stores/ui.js'
import {
  ArrowLeft, Plus, Search, Pin, Trash2, Check, CalendarDays,
  Link2, Save, ListTodo, StickyNote, X, ChevronRight,
} from 'lucide-vue-next'

const ui = useUiStore()
const notes = ref([])
const todos = ref([])
const selectedId = ref(null)
const loading = ref(true)
const saving = ref(false)
const dirty = ref(false)
const lastSavedAt = ref(null)
const noteSearch = ref('')
const mobilePane = ref('library')
const noteTitleEl = ref(null)
const deleteArmed = ref(false)
const todoBusyId = ref(null)
const newTodo = reactive({ title: '', priority: 1, due_date: '' })
const linkTodoToNote = ref(false)
const draft = reactive({ title: '', content: '', tags: '', pinned: false })
const tagInput = ref('')

let saveTimer = null
let deleteTimer = null
let editVersion = 0
let savePromise = null

const selectedNote = computed(() => notes.value.find((n) => n.id === selectedId.value) || null)
const currentTags = computed(() => splitTags(draft.tags))
const filteredNotes = computed(() => {
  const q = noteSearch.value.trim().toLowerCase()
  if (!q) return notes.value
  return notes.value.filter((note) =>
    [note.title, note.content, note.tags].some((v) => String(v || '').toLowerCase().includes(q)),
  )
})
const pinnedNotes = computed(() => filteredNotes.value.filter((n) => n.pinned))
const otherNotes = computed(() => filteredNotes.value.filter((n) => !n.pinned))
const openTodos = computed(() => todos.value.filter((t) => !t.done))
const doneTodos = computed(() => todos.value.filter((t) => t.done))
const todoProgress = computed(() => {
  if (!todos.value.length) return 0
  return Math.round((doneTodos.value.length / todos.value.length) * 100)
})
const saveState = computed(() => {
  if (saving.value) return { text: '保存中…', cls: 'saving' }
  if (dirty.value) return { text: '未保存', cls: 'dirty' }
  if (selectedNote.value) return { text: '已保存', cls: 'saved' }
  return { text: '等待编辑', cls: 'idle' }
})
const todayISO = computed(() => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
})

function splitTags(value) {
  return String(value || '').split(',').map((t) => t.trim()).filter(Boolean)
}

function noteNumber(note) {
  const i = notes.value.findIndex((n) => n.id === note.id)
  return String(Math.max(i + 1, 1)).padStart(2, '0')
}

function noteExcerpt(note) {
  const text = String(note.content || '').replace(/\s+/g, ' ').trim()
  return text || '空白笔记'
}

function updatedLabel(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return `今天 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

function dueMeta(todo) {
  if (!todo.due_date) return null
  const d = new Date(todo.due_date)
  if (Number.isNaN(d.getTime())) return null
  const today = new Date(todayISO.value + 'T00:00:00')
  const dueDay = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diff = Math.round((dueDay - today) / 86400000)
  let text
  if (diff === 0) text = '今天'
  else if (diff === 1) text = '明天'
  else text = `${d.getMonth() + 1}月${d.getDate()}日`
  return { text, overdue: diff < 0 && !todo.done }
}

function todoNoteTitle(noteId) {
  return notes.value.find((n) => n.id === noteId)?.title || ''
}

function applyDraft(note) {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  Object.assign(draft, {
    title: note.title || '',
    content: note.content || '',
    tags: note.tags || '',
    pinned: Boolean(note.pinned),
  })
  tagInput.value = ''
  dirty.value = false
  lastSavedAt.value = note.updated_at || null
  editVersion += 1
}

function clearDeleteArm() {
  if (deleteTimer) { clearTimeout(deleteTimer); deleteTimer = null }
  deleteArmed.value = false
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

async function saveCurrent({ silent = false } = {}) {
  if (!selectedId.value || !dirty.value) return
  if (savePromise) return savePromise
  const noteId = selectedId.value
  const version = editVersion
  const payload = {
    title: draft.title.trim() || '无标题笔记',
    content: draft.content,
    tags: draft.tags,
    pinned: draft.pinned,
  }
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  saving.value = true
  savePromise = (async () => {
    try {
      const data = await api(`/api/notes/${encodeURIComponent(noteId)}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      const index = notes.value.findIndex((n) => n.id === noteId)
      if (index >= 0) notes.value[index] = data.note
      if (selectedId.value === noteId) {
        lastSavedAt.value = data.note.updated_at
        if (version === editVersion) dirty.value = false
        if (!silent && version === editVersion) ui.showToast('笔记已保存')
      }
    } catch (err) {
      ui.showToast('保存失败：' + err.message)
    } finally {
      saving.value = false
      savePromise = null
      if (dirty.value && version !== editVersion && selectedId.value === noteId) queueSave()
    }
  })()
  return savePromise
}

async function flushSave() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  if (savePromise) await savePromise
  if (dirty.value) await saveCurrent({ silent: true })
}

async function selectNote(note) {
  if (selectedId.value === note.id) {
    mobilePane.value = 'editor'
    return
  }
  await flushSave()
  if (dirty.value) return
  selectedId.value = note.id
  applyDraft(note)
  mobilePane.value = 'editor'
}

async function createNote() {
  await flushSave()
  if (dirty.value) return
  try {
    const data = await api('/api/notes', {
      method: 'POST',
      body: JSON.stringify({ title: '无标题笔记', content: '', tags: '', pinned: false }),
    })
    notes.value.unshift(data.note)
    selectedId.value = data.note.id
    applyDraft(data.note)
    mobilePane.value = 'editor'
    await nextTick()
    noteTitleEl.value?.focus()
    noteTitleEl.value?.select()
    ui.showToast('已新建笔记')
  } catch (err) {
    ui.showToast('新建失败：' + err.message)
  }
}

async function removeCurrentNote() {
  if (!selectedId.value) return
  if (!deleteArmed.value) {
    deleteArmed.value = true
    if (deleteTimer) clearTimeout(deleteTimer)
    deleteTimer = setTimeout(() => { deleteArmed.value = false }, 3200)
    return
  }
  await flushSave()
  if (dirty.value) return
  const noteId = selectedId.value
  try {
    await api(`/api/notes/${encodeURIComponent(noteId)}`, { method: 'DELETE' })
    notes.value = notes.value.filter((n) => n.id !== noteId)
    selectedId.value = null
    dirty.value = false
    clearDeleteArm()
    if (notes.value.length) {
      selectedId.value = notes.value[0].id
      applyDraft(notes.value[0])
    } else {
      Object.assign(draft, { title: '', content: '', tags: '', pinned: false })
      lastSavedAt.value = null
    }
    mobilePane.value = notes.value.length ? 'editor' : 'library'
    ui.showToast('笔记已删除')
  } catch (err) {
    ui.showToast('删除失败：' + err.message)
  }
}

function togglePin() {
  if (!selectedNote.value) return
  draft.pinned = !draft.pinned
  markDirty()
}

function addTag() {
  const next = tagInput.value.trim().replace(/,$/, '')
  if (!next) return
  const tags = splitTags(draft.tags)
  if (!tags.some((t) => t.toLowerCase() === next.toLowerCase())) tags.push(next)
  draft.tags = tags.join(',')
  tagInput.value = ''
  markDirty()
}

function removeTag(tag) {
  draft.tags = splitTags(draft.tags).filter((t) => t !== tag).join(',')
  markDirty()
}

async function loadAll() {
  loading.value = true
  try {
    const [noteData, todoData] = await Promise.all([api('/api/notes'), api('/api/todos')])
    notes.value = noteData.notes || []
    todos.value = todoData.todos || []
    if (notes.value.length) {
      selectedId.value = notes.value[0].id
      applyDraft(notes.value[0])
    }
  } catch (err) {
    ui.showToast('加载笔记失败：' + err.message)
  } finally {
    loading.value = false
  }
}

async function addTodo() {
  const title = newTodo.title.trim()
  if (!title || todoBusyId.value) return
  todoBusyId.value = 'new'
  try {
    const data = await api('/api/todos', {
      method: 'POST',
      body: JSON.stringify({
        title,
        priority: Number(newTodo.priority),
        due_date: newTodo.due_date || null,
        note_id: linkTodoToNote.value ? selectedId.value : null,
      }),
    })
    todos.value.unshift(data.todo)
    newTodo.title = ''
    newTodo.due_date = ''
    ui.showToast('待办已添加')
  } catch (err) {
    ui.showToast('添加待办失败：' + err.message)
  } finally {
    todoBusyId.value = null
  }
}

async function toggleTodo(todo) {
  if (todoBusyId.value) return
  todoBusyId.value = todo.id
  try {
    const data = await api(`/api/todos/${encodeURIComponent(todo.id)}`, {
      method: 'PUT',
      body: JSON.stringify({ done: !todo.done }),
    })
    const index = todos.value.findIndex((t) => t.id === todo.id)
    if (index >= 0) todos.value[index] = data.todo
  } catch (err) {
    ui.showToast('更新待办失败：' + err.message)
  } finally {
    todoBusyId.value = null
  }
}

async function removeTodo(todo) {
  if (todoBusyId.value) return
  todoBusyId.value = todo.id
  try {
    await api(`/api/todos/${encodeURIComponent(todo.id)}`, { method: 'DELETE' })
    todos.value = todos.value.filter((t) => t.id !== todo.id)
  } catch (err) {
    ui.showToast('删除待办失败：' + err.message)
  } finally {
    todoBusyId.value = null
  }
}

async function jumpToNote(noteId) {
  const note = notes.value.find((n) => n.id === noteId)
  if (note) await selectNote(note)
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    if (selectedId.value) saveCurrent()
    return
  }
  const target = e.target
  const isTyping = target instanceof HTMLElement && (
    target.matches('input, textarea, select') || target.isContentEditable
  )
  if (!isTyping && !e.ctrlKey && !e.metaKey && !e.altKey && e.key.toLowerCase() === 'n') {
    e.preventDefault()
    createNote()
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  loadAll()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  if (deleteTimer) clearTimeout(deleteTimer)
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
    if (dirty.value) saveCurrent({ silent: true })
  }
})
</script>

<template>
  <div class="notes-ws" :data-pane="mobilePane">
    <header class="notes-topbar">
      <button class="icon-btn" type="button" aria-label="返回首页" @click="ui.goHome()"><ArrowLeft :size="18" /></button>
      <div class="notes-topbar-title">
        <span class="notes-eyebrow">WORKSPACE 02</span>
        <h1>笔记与待办</h1>
      </div>
      <div class="notes-topbar-counts">
        <span><strong>{{ notes.length }}</strong> 笔记</span>
        <span><strong>{{ openTodos.length }}</strong> 待办</span>
      </div>
      <div class="notes-topbar-status" :class="saveState.cls">
        <span class="notes-status-dot"></span>{{ saveState.text }}
      </div>
      <button class="notes-save-btn" type="button" :disabled="!dirty || saving || !selectedNote" title="保存 (Ctrl+S)" @click="saveCurrent()">
        <Save :size="15" /><span>{{ saving ? '保存中' : '保存' }}</span>
      </button>
    </header>

    <nav class="notes-mobile-nav" aria-label="笔记工作区视图">
      <button type="button" :class="{ active: mobilePane === 'library' }" @click="mobilePane = 'library'">
        <StickyNote :size="15" />笔记<span>{{ notes.length }}</span>
      </button>
      <button type="button" :class="{ active: mobilePane === 'editor' }" @click="mobilePane = 'editor'">
        <Search :size="15" />编辑
      </button>
      <button type="button" :class="{ active: mobilePane === 'todos' }" @click="mobilePane = 'todos'">
        <ListTodo :size="15" />待办<span>{{ openTodos.length }}</span>
      </button>
    </nav>

    <div class="notes-ws-body">
      <aside class="notes-panel notes-library">
        <div class="notes-panel-head">
          <div>
            <span class="notes-label">LIBRARY</span>
            <h2>笔记索引</h2>
          </div>
          <span class="notes-count">{{ notes.length }}</span>
        </div>
        <label class="notes-search">
          <Search :size="15" />
          <input v-model="noteSearch" type="search" placeholder="搜索标题、正文或标签" autocomplete="off">
          <button v-if="noteSearch" type="button" aria-label="清除搜索" @click="noteSearch = ''"><X :size="13" /></button>
        </label>
        <button class="notes-new-btn" type="button" @click="createNote"><Plus :size="16" /><span>新建笔记</span><kbd>N</kbd></button>

        <div class="notes-index-scroll">
          <div v-if="loading" class="notes-loading"><span class="spin">◌</span> 正在整理笔记</div>
          <template v-else>
            <div v-if="!filteredNotes.length" class="notes-list-empty">
              <StickyNote :size="22" />
              <span>{{ noteSearch ? '没有匹配的笔记' : '还没有笔记' }}</span>
              <small>{{ noteSearch ? '换个关键词试试' : '从一条念头开始' }}</small>
            </div>
            <template v-if="pinnedNotes.length">
              <div class="notes-group-head"><span><Pin :size="11" />置顶</span><span>{{ pinnedNotes.length }}</span></div>
              <button v-for="note in pinnedNotes" :key="note.id" class="notes-index-item" :class="{ active: note.id === selectedId }" type="button" @click="selectNote(note)">
                <span class="notes-index-no">{{ noteNumber(note) }}</span>
                <span class="notes-index-copy">
                  <strong>{{ note.title || '无标题笔记' }}</strong>
                  <small>{{ noteExcerpt(note) }}</small>
                </span>
                <Pin :size="12" class="notes-index-pin" />
              </button>
            </template>
            <template v-if="otherNotes.length">
              <div class="notes-group-head"><span>全部笔记</span><span>{{ otherNotes.length }}</span></div>
              <button v-for="note in otherNotes" :key="note.id" class="notes-index-item" :class="{ active: note.id === selectedId }" type="button" @click="selectNote(note)">
                <span class="notes-index-no">{{ noteNumber(note) }}</span>
                <span class="notes-index-copy">
                  <strong>{{ note.title || '无标题笔记' }}</strong>
                  <small>{{ noteExcerpt(note) }}</small>
                </span>
                <ChevronRight :size="13" class="notes-index-chevron" />
              </button>
            </template>
          </template>
        </div>
        <div class="notes-library-foot">
          <span class="notes-foot-dot"></span>
          <span>本地自动保存</span>
          <span class="notes-foot-spacer"></span>
          <span>{{ updatedLabel(lastSavedAt) || '尚未编辑' }}</span>
        </div>
      </aside>

      <main class="notes-panel notes-editor">
        <template v-if="selectedNote">
          <div class="notes-editor-top">
            <div class="notes-editor-path"><StickyNote :size="14" /><span>NOTE / {{ noteNumber(selectedNote) }}</span></div>
            <div class="notes-editor-actions">
              <button class="notes-icon-action" :class="{ active: draft.pinned }" type="button" :title="draft.pinned ? '取消置顶' : '置顶笔记'" @click="togglePin">
                <Pin :size="15" :fill="draft.pinned ? 'currentColor' : 'none'" />
              </button>
              <button class="notes-icon-action danger" :class="{ armed: deleteArmed }" type="button" :title="deleteArmed ? '再次点击确认删除' : '删除笔记'" @click="removeCurrentNote">
                <Trash2 :size="15" /><span v-if="deleteArmed">确认删除</span>
              </button>
            </div>
          </div>

          <input ref="noteTitleEl" class="notes-title-input" :value="draft.title" placeholder="无标题笔记" maxlength="200" @input="draft.title = $event.target.value; markDirty()">
          <div class="notes-tags-row">
            <span v-for="tag in currentTags" :key="tag" class="notes-tag">
              {{ tag }}<button type="button" :aria-label="`移除标签 ${tag}`" @click="removeTag(tag)"><X :size="10" /></button>
            </span>
            <input v-model="tagInput" class="notes-tag-input" placeholder="+ 添加标签" maxlength="40" @keydown.enter.prevent="addTag" @blur="addTag">
          </div>
          <textarea class="notes-content-input" :value="draft.content" placeholder="写点什么。标题之外，这里可以自由展开。" spellcheck="false" @input="draft.content = $event.target.value; markDirty()"></textarea>
          <footer class="notes-editor-foot">
            <span>{{ draft.content.length }} 字符</span>
            <span class="notes-foot-sep"></span>
            <span>{{ dirty ? '有未保存修改' : '所有修改已保存' }}</span>
            <span class="notes-foot-spacer"></span>
            <span>{{ updatedLabel(lastSavedAt) }}</span>
          </footer>
        </template>
        <div v-else-if="loading" class="notes-editor-empty"><span class="spin">◌</span><p>载入笔记中</p></div>
        <div v-else class="notes-editor-empty">
          <div class="notes-empty-mark">知</div>
          <h2>从一条未完成的想法开始</h2>
          <p>笔记和待办都在本地保存，随时回来继续。</p>
          <button class="btn primary" type="button" @click="createNote"><Plus :size="15" />新建第一篇笔记</button>
        </div>
      </main>

      <aside class="notes-panel notes-todo-rail">
        <div class="notes-todo-head">
          <div class="notes-panel-head">
            <div>
              <span class="notes-label">FLOW</span>
              <h2>待办流</h2>
            </div>
            <span class="notes-count">{{ openTodos.length }}</span>
          </div>
          <div class="notes-progress-track"><span :style="{ width: todoProgress + '%' }"></span></div>
          <div class="notes-progress-copy"><span>{{ doneTodos.length }} / {{ todos.length }} 完成</span><strong>{{ todoProgress }}%</strong></div>
        </div>

        <form class="notes-todo-form" @submit.prevent="addTodo">
          <div class="notes-todo-input-row">
            <input v-model="newTodo.title" placeholder="添加一项待办…" maxlength="200" aria-label="待办内容">
            <button type="submit" :disabled="!newTodo.title.trim() || todoBusyId" aria-label="添加待办"><Plus :size="17" /></button>
          </div>
          <div class="notes-todo-controls">
            <label class="notes-select-wrap">
              <span class="priority-dot" :class="'p' + newTodo.priority"></span>
              <select v-model="newTodo.priority" aria-label="优先级">
                <option :value="2">高优先</option>
                <option :value="1">普通</option>
                <option :value="0">低优先</option>
              </select>
            </label>
            <label class="notes-date-wrap"><CalendarDays :size="12" /><input v-model="newTodo.due_date" type="date" :min="todayISO" aria-label="截止日期"></label>
          </div>
          <button v-if="selectedNote" class="notes-link-toggle" :class="{ active: linkTodoToNote }" type="button" @click="linkTodoToNote = !linkTodoToNote">
            <Link2 :size="12" />{{ linkTodoToNote ? '关联当前笔记' : '不关联笔记' }}
          </button>
        </form>

        <div class="notes-todo-scroll">
          <div v-if="!todos.length && !loading" class="notes-todo-empty">
            <ListTodo :size="20" /><span>今天没有待办</span><small>留一点空间给意外。</small>
          </div>
          <template v-if="openTodos.length">
            <div class="notes-group-head todo"><span>待完成</span><span>{{ openTodos.length }}</span></div>
            <article v-for="todo in openTodos" :key="todo.id" class="notes-todo-item" :class="{ busy: todoBusyId === todo.id }">
              <button class="notes-check" type="button" aria-label="标记完成" @click="toggleTodo(todo)"></button>
              <div class="notes-todo-copy">
                <strong>{{ todo.title }}</strong>
                <div class="notes-todo-meta">
                  <span class="priority-dot" :class="'p' + todo.priority"></span>
                  <span v-if="dueMeta(todo)" class="notes-due" :class="{ overdue: dueMeta(todo).overdue }">{{ dueMeta(todo).text }}</span>
                  <button v-if="todo.note_id && todoNoteTitle(todo.note_id)" class="notes-todo-link" type="button" @click="jumpToNote(todo.note_id)"><Link2 :size="10" />{{ todoNoteTitle(todo.note_id) }}</button>
                </div>
              </div>
              <button class="notes-todo-delete" type="button" aria-label="删除待办" @click="removeTodo(todo)"><X :size="13" /></button>
            </article>
          </template>
          <template v-if="doneTodos.length">
            <div class="notes-group-head todo done"><span>已完成</span><span>{{ doneTodos.length }}</span></div>
            <article v-for="todo in doneTodos" :key="todo.id" class="notes-todo-item done" :class="{ busy: todoBusyId === todo.id }">
              <button class="notes-check checked" type="button" aria-label="标记未完成" @click="toggleTodo(todo)"><Check :size="12" /></button>
              <div class="notes-todo-copy"><strong>{{ todo.title }}</strong></div>
              <button class="notes-todo-delete" type="button" aria-label="删除待办" @click="removeTodo(todo)"><X :size="13" /></button>
            </article>
          </template>
        </div>
        <div class="notes-todo-foot"><span>每日推进一点，比突击更可靠</span><span class="notes-foot-spacer"></span><span>{{ todos.length }} 项</span></div>
      </aside>
    </div>
  </div>
</template>
