<template>
  <el-card>
    <template #header>
      <div class="head">
        <b>场景包</b>
        <span class="hint">共 {{ packs.length }} 个 · 开关会整体启停包内全部 Agent</span>
        <el-button text :loading="loading" @click="load(true)">刷新</el-button>
      </div>
    </template>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="启停只影响包内代码 Agent 的路由与应答；包内工具与知识库仍随 manifest 装载，彻底卸载需修改 manifest 后重启。"
      style="margin-bottom: 16px"
    />

    <el-empty v-if="!loading && !packs.length" description="暂无场景包" />
    <el-row :gutter="16" v-loading="loading">
      <el-col v-for="p in packs" :key="p.name" :xs="24" :lg="12">
        <el-card shadow="never" class="pack">
          <div class="pack-head">
            <div class="pack-title">
              <b>{{ p.display_name }}</b>
              <el-tag :type="p.enabled ? 'success' : 'info'" size="small">
                {{ p.enabled ? '已启用' : '已停用' }}
              </el-tag>
              <span class="mono">{{ p.name }} · v{{ p.version }}</span>
            </div>
            <el-switch
              :model-value="p.enabled"
              :disabled="!p.manifest_enabled || saving === p.name"
              :loading="saving === p.name"
              @change="onSwitch(p, $event)"
            />
          </div>

          <el-alert
            v-if="!p.manifest_enabled"
            type="warning"
            :closable="false"
            show-icon
            title="manifest 中已停用：需修改 manifest 并重启服务后才能开启"
            class="warn"
          />

          <div class="section">
            <div class="label">Agent（{{ enabledAgentCount(p) }}/{{ p.agents.length }}）</div>
            <div class="tags">
              <el-tag
                v-for="a in p.agents"
                :key="a.name"
                :type="a.enabled ? 'success' : 'info'"
                size="small"
              >
                {{ a.display_name }}
                <span class="mono">{{ a.name }}</span>
              </el-tag>
            </div>
          </div>

          <div class="section">
            <div class="label">工具（{{ p.tools.length }}）</div>
            <div class="tags">
              <el-tag
                v-for="t in p.tools"
                :key="t.name"
                type="info"
                size="small"
                effect="plain"
                :title="t.description"
              >
                {{ t.name }}
              </el-tag>
            </div>
          </div>

          <div class="section">
            <div class="label">知识库命名空间</div>
            <div class="tags">
              <el-tag v-for="ns in p.knowledge_namespaces" :key="ns" size="small" effect="plain">
                {{ ns }}
              </el-tag>
            </div>
          </div>

          <div class="section" v-if="p.features.length">
            <div class="label">运营台能力</div>
            <div class="tags">
              <el-tag v-for="f in p.features" :key="f" type="warning" size="small" effect="plain">
                {{ featureLabel(f) }}
              </el-tag>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'

import { usePacksStore } from '@/stores/packs'
import type { ScenePack } from '@/types'

const store = usePacksStore()
const { packs, loading } = storeToRefs(store)
const saving = ref('')

const FEATURE_LABELS: Record<string, string> = { tickets: '工单管理' }

function featureLabel(feature: string) {
  return FEATURE_LABELS[feature] ?? feature
}

function enabledAgentCount(pack: ScenePack) {
  return pack.agents.filter((a) => a.enabled).length
}

function onSwitch(pack: ScenePack, value: string | number | boolean) {
  onToggle(pack, Boolean(value))
}

async function load(force = false) {
  await store.fetchPacks(force)
}

async function onToggle(pack: ScenePack, enabled: boolean) {
  if (!enabled) {
    try {
      await ElMessageBox.confirm(
        `停用后将关闭「${pack.display_name}」内全部 Agent 的路由与应答，确定继续？`,
        '停用场景包',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  saving.value = pack.name
  try {
    const updated = await store.togglePack(pack.name, enabled)
    ElMessage.success(`${updated.display_name}已${updated.enabled ? '启用' : '停用'}`)
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  } finally {
    saving.value = ''
  }
}

onMounted(() => load(true))
</script>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hint {
  color: #909399;
  font-size: 13px;
}
.pack {
  margin-bottom: 16px;
}
.pack-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.pack-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.mono {
  font-family: Consolas, Menlo, monospace;
  font-size: 12px;
  color: #909399;
}
.warn {
  margin-top: 12px;
}
.section {
  margin-top: 12px;
}
.label {
  font-size: 13px;
  color: #606266;
  margin-bottom: 6px;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
</style>
