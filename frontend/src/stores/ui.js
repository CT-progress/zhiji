import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', {
  state: () => ({
    // null = bento home；设为 workspace key 则进入该工作区焦点模式
    focus: null,
    settingsOpen: false,
    settingsTab: 'models',
    toast: '',
    toastVisible: false,
    // 会话抽屉（焦点模式下从左侧滑入）
    drawerOpen: false,
    _toastTimer: null,
  }),
  actions: {
    enterWorkspace(w) { this.focus = w; this.drawerOpen = false },
    goHome() { this.focus = null; this.drawerOpen = false },
    openSettings(tab) { this.settingsTab = tab || 'models'; this.settingsOpen = true },
    closeSettings() { this.settingsOpen = false },
    showToast(msg) {
      this.toast = msg
      this.toastVisible = true
      if (this._toastTimer) clearTimeout(this._toastTimer)
      this._toastTimer = setTimeout(() => { this.toastVisible = false }, 2800)
    },
    toggleDrawer() { this.drawerOpen = !this.drawerOpen },
  },
})
