<script setup lang="ts">
import { useRoute } from 'vue-router'
import { ChatDotRound, Collection, Setting } from '@element-plus/icons-vue'

const route = useRoute()
</script>

<template>
  <el-container class="app-layout">
    <el-aside width="200px" class="app-aside">
      <div class="app-logo">DataPilot</div>
      <el-menu router :default-active="route.path">
        <el-menu-item index="/">
          <el-icon><ChatDotRound /></el-icon>
          <span>对话</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <el-icon><Collection /></el-icon>
          <span>知识库</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-layout {
  height: 100vh;
}

.app-aside {
  border-right: 1px solid var(--el-border-color-light);
}

/* 导航边框由 aside 提供，去掉 el-menu 自带右边框避免双线 */
.app-aside :deep(.el-menu) {
  border-right: none;
}

.app-logo {
  padding: 16px;
  font-size: 18px;
  font-weight: 600;
}

.app-main {
  padding: 16px;
  overflow: auto;
}
</style>
