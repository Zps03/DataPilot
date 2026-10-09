<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowRight, CircleCheck, CircleClose, Loading, MagicStick } from '@element-plus/icons-vue'
import type { ChatMessage } from '@/stores/chat'

const props = defineProps<{ message: ChatMessage }>()

const running = computed(() => props.message.status === 'streaming')
// 生成中默认展开；生成结束自动折叠（用户手动开合不受影响）
const expanded = ref(props.message.status === 'streaming')

watch(
  () => props.message.status,
  status => {
    if (status !== 'streaming') {
      expanded.value = false
    }
  },
)

const title = computed(() => {
  if (running.value) {
    return props.message.steps.length || props.message.reasoning ? '推理中…' : '思考中…'
  }
  return props.message.steps.length ? `推理过程 · ${props.message.steps.length} 步` : '深度思考'
})

/** 工具名 → 展示名（未收录的按原名展示） */
const TOOL_LABELS: Record<string, string> = {
  search_knowledge: '知识库检索',
  calculator: '数学计算',
  python_executor: 'Python 数据分析',
  get_current_time: '获取当前时间',
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

function statusLabel(status: string): string {
  if (status === 'running') {
    return '执行中'
  }
  return status === 'error' ? '失败' : '完成'
}

function formatInput(input: unknown): string {
  if (input === undefined || input === null) {
    return ''
  }
  if (typeof input === 'string') {
    return input
  }
  try {
    return JSON.stringify(input)
  } catch {
    return String(input)
  }
}
</script>

<template>
  <div
    v-if="running || message.reasoning || message.steps.length"
    class="reasoning-panel"
    :class="{ streaming: running }"
  >
    <button type="button" class="panel-header" @click="expanded = !expanded">
      <el-icon v-if="running" class="is-loading"><Loading /></el-icon>
      <el-icon v-else class="icon-done"><CircleCheck /></el-icon>
      <span class="panel-title">{{ title }}</span>
      <el-icon class="arrow" :class="{ open: expanded }"><ArrowRight /></el-icon>
    </button>
    <div v-show="expanded" class="panel-body">
      <div v-if="message.reasoning" class="reasoning-block">
        <div class="section-label">
          <el-icon><MagicStick /></el-icon>
          <span>深度思考</span>
        </div>
        <div class="reasoning-content">{{ message.reasoning }}</div>
      </div>
      <div v-if="message.steps.length" class="steps-block">
        <div class="section-label"><span>工具调用</span></div>
        <div v-for="step in message.steps" :key="step.id" class="step">
          <div class="step-head">
            <el-icon v-if="step.status === 'running'" class="is-loading"><Loading /></el-icon>
            <el-icon v-else-if="step.status === 'error'" class="icon-error"><CircleClose /></el-icon>
            <el-icon v-else class="icon-ok"><CircleCheck /></el-icon>
            <span class="step-name">{{ toolLabel(step.name) }}</span>
            <span class="step-status">{{ statusLabel(step.status) }}</span>
          </div>
          <div v-if="formatInput(step.input)" class="step-line">
            <span class="line-label">输入</span>
            <code>{{ formatInput(step.input) }}</code>
          </div>
          <div v-if="step.summary" class="step-line">
            <span class="line-label">结果</span>
            <code>{{ step.summary }}</code>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reasoning-panel {
  margin-bottom: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  overflow: hidden;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  background: none;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}

.panel-header:hover {
  background: var(--el-fill-color-lighter);
}

.panel-title {
  flex: 1;
  text-align: left;
}

.arrow {
  transition: transform 0.2s;
}

.arrow.open {
  transform: rotate(90deg);
}

.icon-done {
  color: var(--el-color-success);
}

.icon-ok {
  color: var(--el-color-success);
}

.icon-error {
  color: var(--el-color-danger);
}

.panel-body {
  padding: 4px 12px 12px;
  border-top: 1px dashed var(--el-border-color-lighter);
}

.section-label {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 8px 0 4px;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.reasoning-content {
  max-height: 220px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

.step {
  margin-bottom: 8px;
}

.step-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.step-name {
  font-weight: 500;
}

.step-status {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.step-line {
  margin: 4px 0 0 22px;
}

.line-label {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.step-line code {
  display: block;
  max-height: 140px;
  margin-top: 2px;
  padding: 4px 6px;
  border-radius: 4px;
  background: var(--el-fill-color-lighter);
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
  white-space: pre-wrap;
  word-break: break-all;
  overflow-y: auto;
}
</style>
