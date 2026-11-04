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
/* 单一流式布局：筛选控件与右侧按钮同属一行流，控件放不下时只在它们之间换行，
   按钮不会被整体挤到单独一行（此前 5 个以上筛选项就会复现）。 */
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--cs-space-2) var(--cs-space-3);
}

/* 用 display: contents 把字段拆成 flex 直接子项，从而与按钮共享换行流 */
.fb-fields {
  display: contents;
}

.fb-actions {
  display: flex;
  align-items: center;
  gap: var(--cs-space-2);
  margin-left: auto;
  flex: none;
}
</style>
