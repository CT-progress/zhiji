<script setup>
import { useUiStore } from '../stores/ui.js'
import { ArrowLeft } from 'lucide-vue-next'
defineProps({
  label: { type: String, default: '' },
  sub: { type: String, default: '阶段 C 实现' },
  features: { type: Array, default: () => [] },
})
const ui = useUiStore()
</script>
<template>
  <div class="focus-placeholder">
    <button class="back-btn" type="button" @click="ui.goHome()"><ArrowLeft :size="16" /> 返回首页</button>
    <div class="fp-card">
      <div class="fp-label">{{ label }}</div>
      <div class="fp-sub">{{ sub }}</div>
      <div v-if="features.length" class="fp-chips">
        <span v-for="f in features" :key="f" class="fp-chip">{{ f }}</span>
      </div>
      <div class="fp-status">建设中 · 框架与设计已就位，待接入后端</div>
    </div>
  </div>
</template>
