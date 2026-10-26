import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', {
  state: () => ({
    workspace: 'video',
    settingsOpen: false,
    settingsTab: 'models',
    toast: '',
    toastVisible: false,
    sidebarOpen: false,
    _toastTimer: null,
  }),
  actions: {
    setWorkspace(w) { this.workspace = w },
    openSettings(tab) { this.settingsTab = tab || 'models'; this.settingsOpen = true },
    closeSettings() { this.settingsOpen = false },
    showToast(msg) {
      this.toast = msg
      this.toastVisible = true
      if (this._toastTimer) clearTimeout(this._toastTimer)
      this._toastTimer = setTimeout(() => { this.toastVisible = false }, 2800)
    },
    toggleSidebar() { this.sidebarOpen = !this.sidebarOpen },
  },
})
