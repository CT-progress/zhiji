<script setup>
import { useUiStore } from '../stores/ui.js'
const ui = useUiStore()
</script>
<template>
  <div id="toast" :style="{ display: ui.toastVisible ? 'block' : 'none' }">{{ ui.toast }}</div>
</template>
