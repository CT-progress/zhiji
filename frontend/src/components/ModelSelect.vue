<script setup>
import { useCoreStore } from '../stores/core.js'
const core = useCoreStore()
const emit = defineEmits(['change'])
function onChange(e) { emit('change', e.target.value) }
</script>
<template>
  <select class="model-select" :value="core.model" @change="onChange" aria-label="选择模型">
    <option v-if="!core.enabledModels.length" value="">未配置模型</option>
    <option v-for="m in core.enabledModels" :key="m.name" :value="m.name">{{ m.name }}{{ m.default ? '（默认）' : '' }}</option>
  </select>
</template>
