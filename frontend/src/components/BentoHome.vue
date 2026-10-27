<script setup>
import { computed } from 'vue'
import { useUiStore } from '../stores/ui.js'
import { useCoreStore } from '../stores/core.js'
import { useAuthStore } from '../stores/auth.js'
import { Video, NotebookPen, Library, FileText, Film, ArrowRight, Sparkles } from 'lucide-vue-next'

const ui = useUiStore()
const core = useCoreStore()
const auth = useAuthStore()

const lastConv = computed(() => core.conversations[0] || null)
const convCount = computed(() => core.conversations.length)
const noteCount = computed(() => core.conversations.filter((c) => c.has_note).length)

const tiles = [
  { key: 'video', label: '视频解析', sub: '把视频 / 图文变成结构化笔记', icon: Video, area: 'video', live: true },
  { key: 'notes', label: '笔记与待办', sub: 'notes · todos · reminders', icon: NotebookPen, area: 'notes', soon: ['笔记', '待办', '提醒', '习惯打卡'] },
  { key: 'knowledge', label: '知识库', sub: '文件 · 向量检索 · 资料问答', icon: Library, area: 'knowledge', soon: ['上传', '解析', '检索', '问答'] },
  { key: 'docs', label: '文档助手', sub: '总结 · 问答 · 生成 · 润色', icon: FileText, area: 'docs', soon: ['总结', '问答', '润色', '导出'] },
  { key: 'media', label: '媒体工坊', sub: '字幕 · FFmpeg · 异步导出', icon: Film, area: 'media', soon: ['Whisper', '字幕', '转码', '导出'] },
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

      <div v-if="t.live" class="tile-body live">
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

      <div v-else class="tile-body soon-body">
        <div class="soon-chips">
          <span v-for="s in t.soon" :key="s" class="soon-chip">{{ s }}</span>
        </div>
        <div class="soon-tag">阶段 C · 即将到来</div>
      </div>
    </button>
  </div>
</template>
