<script setup lang="ts">
import { ChatDotRound, Delete, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useChatStore } from '@/stores/chat'

const store = useChatStore()

function handleDelete(id: string): void {
  const session = store.sessions.find(item => item.id === id)
  if (session?.messages.some(message => message.status === 'streaming')) {
    ElMessage.warning('回答生成中，暂不能删除该会话')
    return
  }
  store.deleteSession(id)
}
</script>

<template>
  <aside class="session-list">
    <el-button class="new-session-btn" type="primary" plain :icon="Plus" @click="store.newSession()">
      新建对话
    </el-button>
    <ul class="sessions">
      <li
        v-for="session in store.sessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.id === store.activeSessionId }"
        @click="store.selectSession(session.id)"
      >
        <el-icon class="session-icon"><ChatDotRound /></el-icon>
        <span class="session-title" :title="session.title">{{ session.title }}</span>
        <el-icon class="session-delete" title="删除会话" @click.stop="handleDelete(session.id)">
          <Delete />
        </el-icon>
      </li>
    </ul>
    <p v-if="!store.sessions.length" class="empty-tip">暂无会话</p>
  </aside>
</template>

<style scoped>
.session-list {
  width: 230px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 10px 10px;
  border-right: 1px solid var(--el-border-color-light);
  overflow: hidden;
}

.new-session-btn {
  width: 100%;
}

.sessions {
  flex: 1;
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-y: auto;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  cursor: pointer;
  transition: background-color 0.15s;
}

.session-item:hover {
  background: var(--el-fill-color-light);
}

.session-item.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.session-icon {
  flex-shrink: 0;
}

.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-delete {
  opacity: 0;
  color: var(--el-text-color-secondary);
}

.session-item:hover .session-delete {
  opacity: 1;
}

.session-delete:hover {
  color: var(--el-color-danger);
}

.empty-tip {
  margin: 8px 0;
  font-size: 12px;
  text-align: center;
  color: var(--el-text-color-placeholder);
}
</style>
