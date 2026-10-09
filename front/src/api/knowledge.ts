import request from './request'

/** 已入库文档（GET /api/knowledge/list，按源文件聚合） */
export interface KnowledgeDocument {
  name: string
  chunks: number
  size: number | null
  upload_time: string | null
}

export interface DocumentListResponse {
  documents: KnowledgeDocument[]
  total_chunks: number
}

export interface UploadResponse {
  filename: string
  chunks: number
  message: string
}

export interface DeleteDocumentResponse {
  name: string
  deleted_chunks: number
  message: string
}

/** 检索结果片段（score 为余弦相似度，越大越相关） */
export interface SearchResultItem {
  doc: string
  page: number | null
  snippet: string
  score: number
}

export interface SearchResponse {
  query: string
  results: SearchResultItem[]
}

export function fetchDocuments(): Promise<DocumentListResponse> {
  return request.get<DocumentListResponse>('/knowledge/list').then(response => response.data)
}

/** 上传文档并入库（multipart）；onProgress 回调上传进度（0-100） */
export function uploadDocument(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return request
    .post<UploadResponse>('/knowledge/upload', form, {
      onUploadProgress: event => {
        if (event.total) {
          onProgress?.(Math.round((event.loaded / event.total) * 100))
        }
      },
    })
    .then(response => response.data)
}

/** 删除单个文档（按文件名，同时清理后端 uploads 目录中的文件） */
export function deleteDocument(name: string): Promise<DeleteDocumentResponse> {
  return request
    .delete<DeleteDocumentResponse>('/knowledge/document', { params: { name } })
    .then(response => response.data)
}

export function clearKnowledge(): Promise<{ message: string }> {
  return request.delete<{ message: string }>('/knowledge/clear').then(response => response.data)
}

/** 检索测试：直接查询向量库（不经 Agent），返回片段与相似度分数 */
export function searchKnowledge(query: string): Promise<SearchResponse> {
  return request.post<SearchResponse>('/knowledge/search', { query }).then(response => response.data)
}
