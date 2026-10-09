<script setup lang="ts">
import { computed, ref } from 'vue'
import { Promotion, VideoPause } from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chat'

const store = useChatStore()
const text = ref('')
const canSend = computed(() => text.value.trim().length > 0 && !store.streaming)

function onEnter(event: Event): void {
  if (!(event instanceof KeyboardEvent)) {
    return
  }
  // 中文输入法组词确认时回车不发送（isComposing，部分浏览器为 keyCode 229）
  if (event.isComposing || event.keyCode === 229) {
    return
  }
  event.preventDefault()
  void send()
}

async function send(): Promise<void> {
  if (!canSend.value) {
    return
  }
  const content = text.value
  text.value = ''
  await store.sendMessage(content)
}
</script>

<template>
  <div class="chat-input">
    <div class="input-inner">
      <el-input
        v-model="text"
        type="textarea"
        :rows="3"
        resize="none"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        @keydown.enter.exact="onEnter"
      />
      <div class="input-footer">
        <el-select
          v-model="store.model"
          class="model-select"
          placeholder="选择模型"
          :disabled="store.streaming"
        >
          <el-option v-for="name in store.availableModels" :key="name" :label="name" :value="name" />
        </el-select>
        <div class="footer-right">
          <span class="hint">Enter 发送 · Shift+Enter 换行</span>
          <el-button
            v-if="store.streaming"
            type="danger"
            plain
            :icon="VideoPause"
            @click="store.stopStreaming()"
          >
            停止
          </el-button>
          <el-button v-else type="primary" :icon="Promotion" :disabled="!canSend" @click="send">
            发送
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-input {
  padding: 12px 16px;
  border-top: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
}

.input-inner {
  max-width: 900px;
  margin: 0 auto;
}

.input-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
}

.model-select {
  width: 180px;
}

.footer-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
</style>
