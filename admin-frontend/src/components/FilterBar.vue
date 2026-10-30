<template>
  <div class="filter-bar">
    <div class="fb-fields">
      <slot />
    </div>
    <div class="fb-actions">
      <slot name="actions">
        <el-button type="primary" :loading="loading" @click="$emit('search')">查询</el-button>
        <el-button @click="$emit('reset')">重置</el-button>
      </slot>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 列表页筛选区：左控件、右操作按钮，统一间距与对齐。
 * 控件宽度由页面自行指定（style="width: 160px"），避免各页各写一套 flex 布局。
 */
defineProps<{ loading?: boolean }>()
defineEmits<{ (e: 'search'): void; (e: 'reset'): void }>()
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cs-space-3);
  flex-wrap: wrap;
}

.fb-fields {
  display: flex;
  align-items: center;
  gap: var(--cs-space-2);
  flex-wrap: wrap;
}

.fb-actions {
  display: flex;
  align-items: center;
  gap: var(--cs-space-2);
  margin-left: auto;
}
</style>
