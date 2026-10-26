import { defineStore } from 'pinia'
import { api } from '../lib/api.js'

export const useCoreStore = defineStore('core', {
  state: () => ({
    models: [],
    model: null,
    settings: null,
    platforms: [],
    conversations: [],
  }),
  getters: {
    enabledModels: (s) => s.models.filter((m) => m.enabled),
    hasModel: (s) => s.models.some((m) => m.enabled),
  },
  actions: {
    async loadModels() {
      try {
        const data = await api('/api/models')
        this.models = data.models || []
        if (!this.model || !this.models.some((m) => m.enabled && m.name === this.model)) {
          const def = this.models.find((m) => m.default && m.enabled) || this.models.find((m) => m.enabled)
          this.model = def ? def.name : null
        }
      } catch (e) {}
    },
    async loadSettings() {
      try { this.settings = await api('/api/settings') } catch (e) {}
    },
    async loadPlatforms() {
      try {
        const data = await api('/api/platforms')
        this.platforms = data.platforms || []
      } catch (e) { this.platforms = [] }
    },
    async loadConversations() {
      try {
        const data = await api('/api/conversations')
        this.conversations = data.conversations || []
      } catch (e) {}
    },
    async saveSettings(payload) {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) })
      await this.loadSettings()
    },
  },
})
