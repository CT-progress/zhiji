<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
import { useUiStore } from '../stores/ui.js'
import { useAuthStore } from '../stores/auth.js'
import BentoHome from './BentoHome.vue'
import DotDock from './DotDock.vue'
import VideoWorkspace from '../workspaces/VideoWorkspace.vue'
import NotesWorkspace from '../workspaces/NotesWorkspace.vue'
import KnowledgeWorkspace from '../workspaces/KnowledgeWorkspace.vue'
import DocsWorkspace from '../workspaces/DocsWorkspace.vue'
import MediaWorkspace from '../workspaces/MediaWorkspace.vue'
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
        <DocsWorkspace v-else-if="ui.focus === 'docs'" />
        <MediaWorkspace v-else-if="ui.focus === 'media'" />
      </div>
    </main>

    <DotDock />
    <SettingsPage v-if="ui.settingsOpen" />
  </div>
</template>
