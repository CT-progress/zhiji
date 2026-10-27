<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
import { useUiStore } from '../stores/ui.js'
import { useAuthStore } from '../stores/auth.js'
import BentoHome from './BentoHome.vue'
import DotDock from './DotDock.vue'
import VideoWorkspace from '../workspaces/VideoWorkspace.vue'
import NotesWorkspace from '../workspaces/NotesWorkspace.vue'
import KnowledgeWorkspace from '../workspaces/KnowledgeWorkspace.vue'
import FocusPlaceholder from '../workspaces/FocusPlaceholder.vue'
import SettingsPage from '../settings/SettingsPage.vue'

const ui = useUiStore()
const auth = useAuthStore()

function onKey(e) {
  if (e.key === 'Escape' && ui.focus && !ui.settingsOpen) ui.goHome()
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    if (ui.focus === 'video') ui.toggleDrawer()
  }
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

const soonFeatures = {
  docs: ['总结', '问答', '生成', '润色', '导出'],
  media: ['Whisper 字幕', 'FFmpeg 转码', '异步导出'],
}
</script>
<template>
  <div class="shell">
    <header v-if="!ui.focus" class="shell-top">
      <div class="shell-brand"><span class="brand-mark">知</span><span class="brand-name">知记 zhiji</span></div>
      <div class="shell-health">{{ auth.health }}</div>
      <div class="shell-top-right" v-if="auth.currentUser"><span>{{ auth.currentUser.username }}</span></div>
    </header>

    <main class="shell-main">
      <BentoHome v-if="!ui.focus" />
      <div v-else class="focus-stage">
        <VideoWorkspace v-if="ui.focus === 'video'" />
        <NotesWorkspace v-else-if="ui.focus === 'notes'" />
        <KnowledgeWorkspace v-else-if="ui.focus === 'knowledge'" />
        <FocusPlaceholder v-else-if="ui.focus === 'docs'" label="文档助手" sub="总结 · 问答 · 生成 · 润色 · 批处理 · 转换导出" :features="soonFeatures.docs" />
        <FocusPlaceholder v-else-if="ui.focus === 'media'" label="媒体工坊" sub="上传预览 · Whisper 字幕 · FFmpeg · 异步导出" :features="soonFeatures.media" />
      </div>
    </main>

    <DotDock />
    <SettingsPage v-if="ui.settingsOpen" />
  </div>
</template>
