<script setup>
defineProps({ title: { type: String, default: '' } })
</script>
<template>
  <div class="placeholder-page">
    <div style="font-size:18px;color:var(--text)">{{ title }}</div>
    <div>建设中 · 阶段 C 实现</div>
  </div>
</template>
