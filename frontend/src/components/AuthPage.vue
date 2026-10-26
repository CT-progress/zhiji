<script setup>
import { ref } from 'vue'
import { useAuthStore } from '../stores/auth.js'
import { useUiStore } from '../stores/ui.js'
const auth = useAuthStore()
const ui = useUiStore()
const username = ref('')
const password = ref('')
const displayName = ref('')
const error = ref('')
const busy = ref(false)

function setMode(m) { auth.mode = m; error.value = '' }
async function submit() {
  if (busy.value) return
  if (!username.value || password.value.length < 8) { error.value = '请填写用户名与至少 8 位密码'; return }
  busy.value = true
  error.value = ''
  try {
    const payload = { username: username.value, password: password.value }
    if (auth.mode === 'register' && displayName.value) payload.display_name = displayName.value
    if (auth.mode === 'login') await auth.login(payload)
    else await auth.register(payload)
  } catch (e) {
    error.value = e.message || '操作失败'
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="auth-page">
    <main class="auth-card">
      <div class="auth-brand">
        <div class="brand-mark auth-mark">知</div>
        <div>
          <div class="brand-name">知记 zhiji</div>
          <div class="brand-sub">本地智能工作台</div>
        </div>
      </div>
      <div class="auth-copy">
        <h1>{{ auth.mode === 'login' ? '欢迎回来' : '创建账号' }}</h1>
        <p>{{ auth.mode === 'login' ? '登录后继续使用你的笔记与会话' : '注册后即可使用全部工作区' }}</p>
      </div>
      <div class="auth-tabs" role="tablist" aria-label="账号入口">
        <button class="auth-tab" :class="{ active: auth.mode === 'login' }" type="button" @click="setMode('login')">登录</button>
        <button class="auth-tab" :class="{ active: auth.mode === 'register' }" type="button" @click="setMode('register')">注册</button>
      </div>
      <form class="auth-form" @submit.prevent="submit" novalidate>
        <div class="auth-field" v-if="auth.mode === 'register'">
          <label>显示名称</label>
          <input v-model="displayName" type="text" autocomplete="name" maxlength="80" placeholder="可留空">
        </div>
        <div class="auth-field">
          <label>用户名</label>
          <input v-model="username" type="text" autocomplete="username" required maxlength="32" placeholder="3-32 位字母、数字或 _ . -">
        </div>
        <div class="auth-field">
          <label>密码</label>
          <input v-model="password" type="password" :autocomplete="auth.mode === 'login' ? 'current-password' : 'new-password'" required minlength="8" placeholder="至少 8 位">
        </div>
        <div class="auth-error" v-if="error" role="alert">{{ error }}</div>
        <button class="btn primary auth-submit" type="submit" :disabled="busy">{{ auth.mode === 'login' ? '登录' : '注册' }}</button>
      </form>
      <div class="auth-hint">{{ auth.mode === 'login' ? '还没有账号？切换到「注册」即可创建。' : '已有账号？切换到「登录」。' }}</div>
    </main>
  </div>
</template>
