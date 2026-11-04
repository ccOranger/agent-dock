<template>
  <el-drawer v-model="open" :title="title" :size="size" class="cs-drawer" @closed="$emit('closed')">
    <slot />
    <template v-if="$slots.footer" #footer>
      <slot name="footer" />
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
/**
 * 后台统一的详情抽屉：尺寸默认 45%，标题必填，事件与属性透传给 el-drawer。
 *
 * 视觉（头部留白、分隔线、body padding）由 theme.css 的 .el-drawer__* 全局负责，
 * 组件只收敛「尺寸默认值 + footer 插槽 + closed 透传」这三件容易各写一套的事。
 */
const open = defineModel<boolean>({ required: true })

withDefaults(defineProps<{ title?: string; size?: string }>(), {
  title: '',
  size: '45%',
})

defineEmits<{ (e: 'closed'): void }>()
</script>
