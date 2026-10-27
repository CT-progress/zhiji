<script setup>
import { computed, onMounted, ref } from 'vue'
import { useUiStore } from '../stores/ui.js'
import { useCoreStore } from '../stores/core.js'
import { api } from '../lib/api.js'
import { Video, NotebookPen, Library, FileText, Film, ArrowRight, Sparkles } from 'lucide-vue-next'

const ui = useUiStore()
const core = useCoreStore()

const lastConv = computed(() => core.conversations[0] || null)
const convCount = computed(() => core.conversations.length)
const noteCount = computed(() => core.conversations.filter((c) => c.has_note).length)
const noteStats = ref({ notes: 0, todos: 0 })
const knowledgeStats = ref({ libraries: 0, documents: 0, chunks: 0, characters: 0 })
const docStats = ref({ documents: 0, characters: 0, updated_today: 0, sources: 0 })
const mediaStats = ref({ items: 0, images: 0, audio: 0, videos: 0, artifacts: 0, bytes: 0 })

async function loadWorkspaceStats() {
  const results = await Promise.allSettled([
    api('/api/notes'),
    api('/api/todos?done=false'),
    api('/api/knowledge/stats'),
    api('/api/docs/stats'),
    api('/api/media/stats'),
  ])
  if (results[0].status === 'fulfilled') {
    noteStats.value.notes = (results[0].value.notes || []).length
  }
  if (results[1].status === 'fulfilled') {
    noteStats.value.todos = (results[1].value.todos || []).length
  }
  if (results[2].status === 'fulfilled') {
    knowledgeStats.value = results[2].value.stats || knowledgeStats.value
  }
  if (results[3].status === 'fulfilled') {
    docStats.value = results[3].value.stats || docStats.value
  }
  if (results[4].status === 'fulfilled') {
    mediaStats.value = results[4].value.stats || mediaStats.value
  }
}

onMounted(loadWorkspaceStats)

const tiles = [
  { key: 'video', label: '视频解析', sub: '把视频 / 图文变成结构化笔记', icon: Video, area: 'video', live: true },
  { key: 'notes', label: '笔记与待办', sub: '独立笔记 · 待办流 · 本地自动保存', icon: NotebookPen, area: 'notes', live: true },
  { key: 'knowledge', label: '知识库', sub: '文件 · 全文检索 · 资料问答', icon: Library, area: 'knowledge', live: true },
  { key: 'docs', label: '文档助手', sub: '取材 · 写作 · 流式协作 · 导出', icon: FileText, area: 'docs', live: true },
  { key: 'media', label: '媒体工坊', sub: '素材库 · 预览 · PyAV 异步处理', icon: Film, area: 'media', live: true },
]
</script>
<template>
  <div class="bento">
    <button v-for="t in tiles" :key="t.key" class="bento-tile" :class="['t-' + t.area, { soon: t.soon }]" type="button" @click="ui.enterWorkspace(t.key)">
      <div class="tile-head">
        <div class="tile-icon"><component :is="t.icon" :size="22" /></div>
        <div class="tile-titles">
          <div class="tile-label">{{ t.label }}</div>
          <div class="tile-sub">{{ t.sub }}</div>
        </div>
        <ArrowRight class="tile-go" :size="18" />
      </div>

      <div v-if="t.key === 'video'" class="tile-body live">
        <template v-if="convCount">
          <div class="live-stat"><span class="ls-num">{{ convCount }}</span><span class="ls-lab">条会话</span><span class="ls-num">{{ noteCount }}</span><span class="ls-lab">篇笔记</span></div>
          <div v-if="lastConv" class="live-recent">
            <span class="lr-badge" v-if="lastConv.platform">{{ lastConv.platform }}</span>
            <span class="lr-title">{{ lastConv.title }}</span>
          </div>
          <div class="live-cta">继续解析新链接 <ArrowRight :size="14" /></div>
        </template>
        <template v-else>
          <div class="live-empty"><Sparkles :size="16" /> 粘贴一条视频链接，开始第一篇笔记</div>
        </template>
      </div>

      <div v-else-if="t.key === 'notes'" class="tile-body live notes-live">
        <div class="live-stat"><span class="ls-num">{{ noteStats.notes }}</span><span class="ls-lab">篇笔记</span><span class="ls-num">{{ noteStats.todos }}</span><span class="ls-lab">项未完成</span></div>
        <div class="live-cta">打开笔记与待办 <ArrowRight :size="14" /></div>
      </div>

      <div v-else-if="t.key === 'knowledge'" class="tile-body live knowledge-live">
        <div class="live-stat"><span class="ls-num">{{ knowledgeStats.libraries }}</span><span class="ls-lab">个资料库</span><span class="ls-num">{{ knowledgeStats.documents }}</span><span class="ls-lab">份资料</span></div>
        <div class="knowledge-index-line">
          <span>{{ knowledgeStats.chunks }} 个检索切片</span>
          <span>{{ new Intl.NumberFormat('zh-CN').format(knowledgeStats.characters) }} 字符</span>
        </div>
        <div class="live-cta">{{ knowledgeStats.documents ? '继续检索与提问' : '建立第一块知识空间' }} <ArrowRight :size="14" /></div>
      </div>

      <div v-else-if="t.key === 'docs'" class="tile-body live docs-live">
        <div class="live-stat"><span class="ls-num">{{ docStats.documents }}</span><span class="ls-lab">篇文档</span><span class="ls-num">{{ docStats.sources }}</span><span class="ls-lab">次取材</span></div>
        <div class="knowledge-index-line">
          <span>{{ new Intl.NumberFormat('zh-CN').format(docStats.characters) }} 字符</span>
          <span>{{ docStats.updated_today }} 篇今日更新</span>
        </div>
        <div class="live-cta">{{ docStats.documents ? '继续写作与协作' : '打开第一张写作台' }} <ArrowRight :size="14" /></div>
      </div>

      <div v-else-if="t.key === 'media'" class="tile-body live media-live">
        <div class="live-stat"><span class="ls-num">{{ mediaStats.items }}</span><span class="ls-lab">个素材</span><span class="ls-num">{{ mediaStats.artifacts }}</span><span class="ls-lab">件产物</span></div>
        <div class="knowledge-index-line">
          <span>{{ mediaStats.images }} 图片 · {{ mediaStats.audio }} 音频 · {{ mediaStats.videos }} 视频</span>
          <span>{{ new Intl.NumberFormat('zh-CN').format(mediaStats.bytes) }} B</span>
        </div>
        <div class="live-cta">{{ mediaStats.items ? '打开处理轨道' : '上传第一份媒体素材' }} <ArrowRight :size="14" /></div>
      </div>
    </button>
  </div>
</template>
