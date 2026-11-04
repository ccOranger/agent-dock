<template>
  <div class="stream-renderer" v-html="html"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'

const props = defineProps<{ content: string }>()

const md = new MarkdownIt({
  html: false,
  linkify: true,
  highlight: (code, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(code, { language: lang }).value
      } catch {
        /* fallthrough */
      }
    }
    return ''
  },
})

const html = computed(() => md.render(props.content || ''))
</script>

<style scoped>
.stream-renderer :deep(pre) {
  background: #f6f8fa;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
}

/* 移动端：表格、图片、长链接都可能撑破气泡，统一就地横向滚动或缩放 */
.stream-renderer :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}

.stream-renderer :deep(img) {
  max-width: 100%;
  height: auto;
}

.stream-renderer :deep(a) {
  word-break: break-all;
}
</style>
