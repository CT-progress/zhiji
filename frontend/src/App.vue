<script setup>
import { onMounted, ref } from 'vue'
import { useAuthStore } from './stores/auth.js'
import { useCoreStore } from './stores/core.js'
import { useUiStore } from './stores/ui.js'
import { configureApi } from './lib/api.js'
import AuthPage from './components/AuthPage.vue'
import AppShell from './components/AppShell.vue'
import Toast from './components/Toast.vue'

const auth = useAuthStore()
const core = useCoreStore()
const ready = ref(false)

onMounted(async () => {
  configureApi({
    getAuthRequired: () => auth.authRequired,
    onUnauthorized: () => auth.handleUnauthorized(),
  })
  await auth.loadStatus()
  if (!auth.authRequired || auth.currentUser) {
    await auth.startApp()
    await Promise.all([core.loadModels(), core.loadSettings(), core.loadPlatforms(), core.loadConversations()])
  }
  ready.value = true
})
</script>
<template>
  <Toast />
  <AuthPage v-if="ready && auth.authRequired && !auth.currentUser" />
  <AppShell v-else-if="ready" />
</template>
