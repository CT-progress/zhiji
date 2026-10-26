import { defineStore } from 'pinia'
import { api } from '../lib/api.js'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    authRequired: false,
    currentUser: null,
    mode: 'login',
    health: '连接中',
    started: false,
  }),
  actions: {
    async loadStatus() {
      try {
        const data = await api('/api/auth/status')
        this.authRequired = Boolean(data.require_auth)
        this.currentUser = data.user || null
      } catch (e) {
        this.authRequired = false
        this.currentUser = null
      }
    },
    handleUnauthorized() {
      this.currentUser = null
      this.started = false
    },
    async startApp() {
      try {
        const data = await api('/api/health')
        this.health = data.status || '已连接'
      } catch (e) { this.health = '离线' }
      this.started = true
    },
    async login(payload) {
      const data = await api('/api/auth/login', { method: 'POST', body: JSON.stringify(payload) })
      this.currentUser = data.user || null
      await this.startApp()
    },
    async register(payload) {
      const data = await api('/api/auth/register', { method: 'POST', body: JSON.stringify(payload) })
      this.currentUser = data.user || null
      await this.startApp()
    },
    async logout() {
      try { await api('/api/auth/logout', { method: 'POST' }) } catch (e) {}
      this.currentUser = null
      this.started = false
    },
  },
})
