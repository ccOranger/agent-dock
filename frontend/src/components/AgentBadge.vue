<template>
  <el-tag size="small" type="success" effect="plain">🤖 {{ agent }}</el-tag>
</template>

<script setup lang="ts">
defineProps<{ agent: string }>()
</script>
