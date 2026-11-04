<template>
  <el-upload
    drag
    :auto-upload="false"
    :show-file-list="false"
    accept=".pdf,.docx,.md,.txt"
    :on-change="onChange"
  >
    <div class="uploader">
      将文档拖到此处，或点击上传<br />
      <small>支持 pdf / docx / md / txt，目标知识库：{{ namespace }}</small>
    </div>
  </el-upload>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { apiUploadDocument } from '@/api'

const props = defineProps<{ namespace: string }>()
const emit = defineEmits<{ uploaded: [] }>()

async function onChange(file: UploadFile) {
  if (!file.raw) return
  try {
    await apiUploadDocument(file.raw, props.namespace, file.name)
    ElMessage.success('上传成功，正在索引')
    emit('uploaded')
  } catch (e: any) {
    ElMessage.error(e?.message || '上传失败')
  }
}
</script>

<style scoped>
.uploader {
  padding: 24px;
  color: var(--cs-text-2);
}
</style>
