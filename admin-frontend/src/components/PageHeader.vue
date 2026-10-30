<template>
  <header class="page-header">
    <div class="ph-main">
      <div class="ph-title-row">
        <h1 class="ph-title">{{ title }}</h1>
        <span v-if="count !== undefined && count !== null" class="ph-count">{{ count }}</span>
        <slot name="tags" />
      </div>
      <p v-if="description" class="ph-desc">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="ph-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
/**
 * 页面标题栏：标题 + 数量徽标 + 描述 + 右侧操作区
 * 用于替代各页面 el-card header 里手写的 <b>标题</b> + 按钮，
 * 让 18 个页面的层级、间距、字号保持一致。
 */
defineProps<{
  title: string
  description?: string
  /** 标题右侧的数量/统计徽标，传 undefined 不显示 */
  count?: number | string
}>()
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
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
  background: rgba(64, 158, 255, 0.1);
  color: var(--cs-brand);
  font-size: 12px;
  font-weight: 600;
}

.ph-desc {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--cs-text-3);
}

.ph-actions {
  display: flex;
  align-items: center;
  gap: var(--cs-space-2);
  flex-shrink: 0;
}
</style>
