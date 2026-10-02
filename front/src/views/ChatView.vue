<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import ChatInput from '@/components/ChatInput.vue'
import MessageItem from '@/components/MessageItem.vue'
import SessionList from '@/components/SessionList.vue'
import { useChatStore } from '@/stores/chat'

const store = useChatStore()

const messageArea = ref<HTMLElement | null>(null)
// 用户是否停留在底部附近；上翻阅读历史时不强制回底
const stickToBottom = ref(true)

const messages = computed(() => store.activeSession?.messages ?? [])

onMounted(() => {
  void store.loadModels()
  if (!store.sessions.length) {
    store.newSession()
  }
})

function onScroll(): void {
  const el = messageArea.value
  if (!el) {
    return
  }
  stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = messageArea.value
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

// 消息新增 / 流式增量（正文、思考、工具步骤）时贴底滚动
watch(
  () => {
    const last = messages.value[messages.value.length - 1]
    return [
      messages.value.length,
      last?.content.length ?? 0,
      last?.reasoning.length ?? 0,
      last?.steps.length ?? 0,
      last?.steps.map(step => step.status).join('') ?? '',
    ].join(':')
  },
  () => {
    if (stickToBottom.value) {
      void scrollToBottom()
    }
  },
)

// 切换会话直接回到底部
watch(
  () => store.activeSessionId,
  () => {
    stickToBottom.value = true
    void scrollToBottom()
  },
)
</script>

<template>
  <div class="chat-view">
    <SessionList />
    <section class="chat-main">
      <div ref="messageArea" class="message-area" @scroll.passive="onScroll">
        <div class="message-list">
          <el-empty v-if="!messages.length" description="发送消息，开始与 DataPilot 对话" />
          <MessageItem v-for="message in messages" :key="message.id" :message="message" />
        </div>
      </div>
      <ChatInput />
    </section>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  height: 100%;
  overflow: hidden;
}

.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.message-area {
  flex: 1;
  min-height: 0;
  padding: 8px 16px 16px;
  overflow-y: auto;
}

.message-list {
  max-width: 900px;
  margin: 0 auto;
}
</style>
