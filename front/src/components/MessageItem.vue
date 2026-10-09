<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowRight, Document } from '@element-plus/icons-vue'
import ReasoningPanel from './ReasoningPanel.vue'
import { renderMarkdown } from '@/utils/markdown'
import type { ChatMessage } from '@/stores/chat'

const props = defineProps<{ message: ChatMessage }>()

const isUser = computed(() => props.message.role === 'user')
const streaming = computed(() => props.message.status === 'streaming')
const html = computed(() => (isUser.value ? '' : renderMarkdown(props.message.content)))
const sourcesOpen = ref(false)

function pageLabel(page: number | string | null | undefined): string {
  return page === null || page === undefined || page === '' ? '' : `第 ${page} 页`
}
</script>

<template>
  <div class="message-item" :class="isUser ? 'is-user' : 'is-assistant'">
    <div class="bubble">
      <template v-if="isUser">
        <div class="user-text">{{ message.content }}</div>
      </template>
      <template v-else>
        <ReasoningPanel :message="message" />
        <div
          v-if="message.content"
          class="markdown-body"
          :class="{ streaming }"
          v-html="html"
        ></div>
        <el-alert
          v-if="message.error"
          class="message-error"
          type="error"
          :title="message.error"
          :closable="false"
          show-icon
        />
        <div v-if="message.status === 'stopped'" class="stopped">已停止生成</div>
        <div v-if="message.sources.length" class="sources">
          <button type="button" class="sources-header" @click="sourcesOpen = !sourcesOpen">
            <el-icon><Document /></el-icon>
            <span>引用来源 · {{ message.sources.length }}</span>
            <el-icon class="arrow" :class="{ open: sourcesOpen }"><ArrowRight /></el-icon>
          </button>
          <ul v-show="sourcesOpen" class="source-items">
            <li v-for="(source, index) in message.sources" :key="index">
              <div class="source-title">
                {{ source.doc ?? source.source ?? '未知来源' }}
                <span v-if="pageLabel(source.page)" class="source-page">{{ pageLabel(source.page) }}</span>
              </div>
              <div v-if="source.snippet" class="source-snippet">{{ source.snippet }}</div>
            </li>
          </ul>
        </div>
        <div v-if="message.model" class="meta-row">
          <el-tag size="small" type="info" effect="plain">{{ message.model }}</el-tag>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.message-item {
  display: flex;
  margin-bottom: 16px;
}

.message-item.is-user {
  justify-content: flex-end;
}

.bubble {
  max-width: min(85%, 780px);
  padding: 10px 14px;
  border-radius: 10px;
}

.is-user .bubble {
  background: var(--el-color-primary);
  color: #fff;
}

.is-assistant .bubble {
  background: var(--el-fill-color-light);
}

.user-text {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.markdown-body {
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

/* 流式输出时在末尾显示闪烁光标 */
.markdown-body.streaming > :last-child::after {
  content: '▍';
  margin-left: 2px;
  color: var(--el-color-primary);
  animation: cursor-blink 1s steps(2, start) infinite;
}

@keyframes cursor-blink {
  to {
    visibility: hidden;
  }
}

.markdown-body :deep(p) {
  margin: 6px 0;
}

.markdown-body :deep(p:first-child) {
  margin-top: 0;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  border-radius: 6px;
  background: #f6f8fa;
  overflow-x: auto;
}

.markdown-body :deep(pre code) {
  padding: 0;
  background: transparent;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
}

.markdown-body :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--el-fill-color);
  font-family: Consolas, 'Courier New', monospace;
  font-size: 13px;
}

.markdown-body :deep(table) {
  margin: 8px 0;
  border-collapse: collapse;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: 4px 10px;
  border: 1px solid var(--el-border-color);
}

.markdown-body :deep(blockquote) {
  margin: 8px 0;
  padding: 2px 12px;
  border-left: 3px solid var(--el-border-color);
  color: var(--el-text-color-secondary);
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 6px 0;
  padding-left: 22px;
}

.markdown-body :deep(img) {
  max-width: 100%;
}

.markdown-body :deep(a) {
  color: var(--el-color-primary);
}

.message-error {
  margin-top: 8px;
}

.stopped {
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.sources {
  margin-top: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  overflow: hidden;
}

.sources-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  background: none;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}

.sources-header:hover {
  background: var(--el-fill-color-lighter);
}

.sources-header .arrow {
  transition: transform 0.2s;
}

.sources-header .arrow.open {
  transform: rotate(90deg);
}

.source-items {
  margin: 0;
  padding: 0 12px 10px;
  list-style: none;
}

.source-items li {
  padding: 6px 0;
  border-top: 1px dashed var(--el-border-color-lighter);
}

.source-title {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.source-page {
  margin-left: 4px;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.source-snippet {
  margin-top: 2px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.meta-row {
  margin-top: 8px;
}
</style>
