<template>
  <div class="table-state">
    <template v-if="error">
      <p class="ts-text is-error">{{ error }}</p>
      <el-button size="small" plain @click="$emit('retry')">重试</el-button>
    </template>
    <p v-else-if="loading" class="ts-text">加载中…</p>
    <p v-else class="ts-text">{{ text }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 表格空态 / 加载态 / 错误态的统一出口，放在 el-table 的 #empty 插槽里。
 *
 * 关键点是区分「没有数据」和「加载失败」：此前失败会被 usePagedList 置空 rows，
 * 界面看起来和「暂无数据」一模一样，运营会把故障当成没业务。
 */
withDefaults(defineProps<{ error?: string; loading?: boolean; text?: string }>(), {
  error: '',
  loading: false,
  text: '暂无数据',
})

defineEmits<{ (e: 'retry'): void }>()
</script>

<style scoped>
.table-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px 0 28px;
}

.ts-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--cs-text-3);
}

.ts-text.is-error {
  color: var(--cs-danger);
}
</style>
