<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  ElMessage,
  ElMessageBox,
  type UploadProgressEvent,
  type UploadRequestOptions,
} from 'element-plus'
import { Delete, Document, Refresh, Search, UploadFilled } from '@element-plus/icons-vue'
import {
  clearKnowledge,
  deleteDocument,
  fetchDocuments,
  searchKnowledge,
  uploadDocument,
  type KnowledgeDocument,
  type SearchResultItem,
} from '@/api/knowledge'

// ---------------------------------------------------------------- 文档列表

const documents = ref<KnowledgeDocument[]>([])
const totalChunks = ref(0)
const listLoading = ref(false)
const deletingName = ref<string | null>(null)

async function loadDocuments(): Promise<void> {
  listLoading.value = true
  try {
    const data = await fetchDocuments()
    documents.value = data.documents
    totalChunks.value = data.total_chunks
  } finally {
    listLoading.value = false
  }
}

// ---------------------------------------------------------------- 上传

const ALLOWED_SUFFIXES = ['.pdf', '.txt', '.md', '.markdown']
const MAX_SIZE = 20 * 1024 * 1024

function beforeUpload(file: File): boolean {
  const suffix = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  if (!ALLOWED_SUFFIXES.includes(suffix)) {
    ElMessage.error(`不支持的文件类型：${suffix || '无扩展名'}（支持 PDF / TXT / Markdown）`)
    return false
  }
  if (file.size > MAX_SIZE) {
    ElMessage.error('文件超过 20MB 上限')
    return false
  }
  return true
}

async function customUpload(options: UploadRequestOptions): Promise<void> {
  try {
    const result = await uploadDocument(options.file, percent => {
      options.onProgress({ percent } as UploadProgressEvent)
    })
    options.onSuccess(result)
    ElMessage.success(`「${result.filename}」入库成功（${result.chunks} 个片段）`)
    await loadDocuments()
  } catch (error) {
    // 具体错误信息已由 axios 拦截器统一提示，这里只标记文件行失败
    options.onError(error as Parameters<UploadRequestOptions['onError']>[0])
  }
}

// ---------------------------------------------------------------- 删除 / 清空

async function onDelete(row: KnowledgeDocument): Promise<void> {
  const confirmed = await ElMessageBox.confirm(
    `确定从知识库中删除「${row.name}」吗？共 ${row.chunks} 个片段。`,
    '删除文档',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
    .then(() => true)
    .catch(() => false)
  if (!confirmed) {
    return
  }
  deletingName.value = row.name
  try {
    const result = await deleteDocument(row.name)
    ElMessage.success(`已删除「${row.name}」（${result.deleted_chunks} 个片段）`)
    await loadDocuments()
  } finally {
    deletingName.value = null
  }
}

async function onClear(): Promise<void> {
  const confirmed = await ElMessageBox.confirm(
    `确定清空知识库吗？将删除全部 ${documents.value.length} 个文档的向量数据（${totalChunks.value} 个片段），此操作不可恢复。`,
    '清空知识库',
    { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' },
  )
    .then(() => true)
    .catch(() => false)
  if (!confirmed) {
    return
  }
  await clearKnowledge()
  ElMessage.success('知识库已清空')
  await loadDocuments()
}

// ---------------------------------------------------------------- 检索测试

const query = ref('')
const searching = ref(false)
const searched = ref(false)
const results = ref<SearchResultItem[]>([])

function onQueryEnter(event: Event): void {
  // 中文输入法组词确认时回车不触发检索
  if (event instanceof KeyboardEvent && (event.isComposing || event.keyCode === 229)) {
    return
  }
  void runSearch()
}

async function runSearch(): Promise<void> {
  const text = query.value.trim()
  if (!text || searching.value) {
    return
  }
  searching.value = true
  try {
    const data = await searchKnowledge(text)
    results.value = data.results
    searched.value = true
  } finally {
    searching.value = false
  }
}

// ---------------------------------------------------------------- 展示格式化

function formatBytes(size: number | null): string {
  if (size === null) {
    return '—'
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(iso: string | null): string {
  if (!iso) {
    return '—'
  }
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return iso
  }
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

function scorePercent(score: number): number {
  return Math.round(Math.min(1, Math.max(0, score)) * 100)
}

onMounted(() => {
  void loadDocuments()
})
</script>

<template>
  <div class="knowledge-view">
    <el-card class="section-card">
      <template #header>
        <span class="card-title">文档上传</span>
      </template>
      <el-upload
        drag
        multiple
        accept=".pdf,.txt,.md,.markdown"
        :http-request="customUpload"
        :before-upload="beforeUpload"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或 <em>点击上传</em></div>
        <template #tip>
          <div class="el-upload__tip">
            支持 PDF / TXT / Markdown，单文件不超过 20MB；同名文件重复上传会覆盖旧片段
          </div>
        </template>
      </el-upload>
    </el-card>

    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">已入库文档</span>
          <div class="card-actions">
            <span v-if="documents.length" class="summary">
              共 {{ documents.length }} 个文档 · {{ totalChunks }} 个片段
            </span>
            <el-button size="small" :icon="Refresh" :disabled="listLoading" @click="loadDocuments">
              刷新
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :icon="Delete"
              :disabled="!documents.length"
              @click="onClear"
            >
              清空全部
            </el-button>
          </div>
        </div>
      </template>
      <el-table v-loading="listLoading" :data="documents" empty-text="暂无文档，请先上传">
        <el-table-column prop="name" label="文件名" min-width="240" show-overflow-tooltip />
        <el-table-column label="大小" width="110">
          <template #default="{ row }">{{ formatBytes(row.size) }}</template>
        </el-table-column>
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">{{ formatTime(row.upload_time) }}</template>
        </el-table-column>
        <el-table-column prop="chunks" label="片段数" width="90" align="center" />
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button
              link
              type="danger"
              :loading="deletingName === row.name"
              @click="onDelete(row as KnowledgeDocument)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="section-card">
      <template #header>
        <span class="card-title">检索测试</span>
      </template>
      <div class="search-bar">
        <el-input
          v-model="query"
          placeholder="输入查询文本，直接检索向量库（不经 Agent）"
          clearable
          @keydown.enter="onQueryEnter"
        />
        <el-button
          type="primary"
          :icon="Search"
          :loading="searching"
          :disabled="!query.trim()"
          @click="runSearch"
        >
          检索
        </el-button>
      </div>
      <template v-if="searched">
        <el-empty v-if="!results.length" description="没有检索到相关片段" :image-size="80" />
        <ul v-else class="result-list">
          <li v-for="(item, index) in results" :key="index" class="result-item">
            <div class="result-head">
              <span class="result-source">
                <el-icon><Document /></el-icon>
                {{ item.doc }}
                <span v-if="item.page" class="result-page">第 {{ item.page }} 页</span>
              </span>
              <span class="result-score">相似度 {{ (item.score * 100).toFixed(1) }}%</span>
            </div>
            <el-progress :percentage="scorePercent(item.score)" :stroke-width="6" :show-text="false" />
            <p class="result-snippet">{{ item.snippet }}</p>
          </li>
        </ul>
      </template>
      <el-empty v-else description="输入查询文本以验证检索效果" :image-size="80" />
    </el-card>
  </div>
</template>

<style scoped>
.knowledge-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 1000px;
  margin: 0 auto;
}

.card-title {
  font-weight: 600;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.summary {
  margin-right: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.search-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.search-bar .el-input {
  flex: 1;
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.result-item {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.result-source {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.result-page {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.result-score {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary);
  white-space: nowrap;
}

.result-snippet {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
  word-break: break-word;
}
</style>
