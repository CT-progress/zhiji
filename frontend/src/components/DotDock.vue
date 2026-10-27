<script setup>
import { computed } from 'vue'
import { useUiStore } from '../stores/ui.js'
import { useAuthStore } from '../stores/auth.js'
import { Home, Settings, LogOut, Video, NotebookPen, Library, FileText, Film } from 'lucide-vue-next'

const ui = useUiStore()
const auth = useAuthStore()

const items = [
  { key: 'video', label: '视频解析', icon: Video },
  { key: 'notes', label: '笔记与待办', icon: NotebookPen },
  { key: 'knowledge', label: '知识库', icon: Library },
  { key: 'docs', label: '文档助手', icon: FileText },
  { key: 'media', label: '媒体工坊', icon: Film },
]
const isHome = computed(() => ui.focus === null)
</script>
<template>
  <nav class="dock" aria-label="工作区切换">
    <button class="dock-btn home" :class="{ active: isHome }" type="button" @click="ui.goHome()" :title="isHome ? '首页' : '返回首页'">
      <Home :size="16" />
      <span class="dock-label">首页</span>
    </button>
    <span class="dock-sep" aria-hidden="true"></span>
    <button v-for="(it, i) in items" :key="it.key" class="dock-btn" :class="{ active: ui.focus === it.key }" type="button" @click="ui.enterWorkspace(it.key)" :title="it.label">
      <component :is="it.icon" :size="16" />
      <span class="dock-label">{{ it.label }}</span>
    </button>
    <span class="dock-sep" aria-hidden="true"></span>
    <button class="dock-btn" type="button" @click="ui.openSettings('models')" title="设置">
      <Settings :size="16" />
      <span class="dock-label">设置</span>
    </button>
    <button v-if="auth.currentUser" class="dock-btn" type="button" @click="auth.logout()" title="退出 ({{ auth.currentUser.username }})">
      <LogOut :size="16" />
      <span class="dock-label">退出</span>
    </button>
  </nav>
</template>
