<template>
  <header class="page-header">
    <div class="ph-title-row">
      <h1 class="ph-title">{{ title }}</h1>
      <span v-if="count !== undefined && count !== null" class="ph-count">{{ count }}</span>
      <slot name="tags" />
    </div>
    <div v-if="$slots.actions" class="ph-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
/**
 * 页面标题栏：标题 + 数量徽标 + 右侧操作区。
 *
 * 刻意不提供 description：页面用途由标题、筛选与表格本身表达，
 * 避免每个页面顶部再挂一句复述性的副标题。
 */
defineProps<{
  title: string
  /** 标题右侧的数量/统计徽标，传 undefined 不显示 */
  count?: number | string
}>()
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cs-space-4);
  padding: 2px 2px 0;
}

.ph-title-row {
  display: flex;
  align-items: center;
  gap: var(--cs-space-2);
}

.ph-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--cs-text-1);
}

.ph-count {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 10px;
  background: var(--cs-brand-fill);
  color: var(--cs-brand-strong);
  font-size: 12px;
  font-weight: 600;
}

.ph-actions {
  display: flex;
  align-items: center;
  gap: var(--cs-space-2);
  flex-shrink: 0;
}
</style>
