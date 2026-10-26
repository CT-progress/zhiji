<script setup>
import { useAuthStore } from '../stores/auth.js'
import { useCoreStore } from '../stores/core.js'
import { useUiStore } from '../stores/ui.js'
import { Video, NotebookPen, Library, FileText, Film, Settings, LogOut, UserRound } from 'lucide-vue-next'
import VideoWorkspace from '../workspaces/VideoWorkspace.vue'
import Placeholder from '../workspaces/Placeholder.vue'
import SettingsPage from '../settings/SettingsPage.vue'

const auth = useAuthStore()
const core = useCoreStore()
const ui = useUiStore()

const WORKSPACES = [
  { key: 'video', label: '视频解析', icon: Video },
  { key: 'notes', label: '笔记与待办', icon: NotebookPen },
  { key: 'knowledge', label: '知识库', icon: Library },
  { key: 'docs', label: '文档助手', icon: FileText },
  { key: 'media', label: '媒体工坊', icon: Film },
]
</script>
<template>
  <div class="app">
    <aside class="rail">
      <div class="rail-brand">知</div>
      <button v-for="w in WORKSPACES" :key="w.key" class="rail-btn" :class="{ active: ui.workspace === w.key }" type="button" @click="ui.setWorkspace(w.key)">
        <component :is="w.icon" :size="20" />
        <span class="rail-tip">{{ w.label }}</span>
      </button>
      <div class="rail-foot">
        <button class="rail-btn" type="button" @click="ui.openSettings('models')" title="设置">
          <Settings :size="20" />
          <span class="rail-tip">设置</span>
        </button>
        <button v-if="auth.currentUser" class="rail-btn" type="button" @click="auth.logout()" title="退出">
          <LogOut :size="20" />
          <span class="rail-tip">退出</span>
        </button>
      </div>
    </aside>
    <div class="workspace">
      <VideoWorkspace v-if="ui.workspace === 'video'" />
      <Placeholder v-else-if="ui.workspace === 'notes'" title="笔记与待办" />
      <Placeholder v-else-if="ui.workspace === 'knowledge'" title="知识库" />
      <Placeholder v-else-if="ui.workspace === 'docs'" title="文档助手" />
      <Placeholder v-else-if="ui.workspace === 'media'" title="媒体工坊" />
    </div>
  </div>
  <SettingsPage v-if="ui.settingsOpen" />
</template>
