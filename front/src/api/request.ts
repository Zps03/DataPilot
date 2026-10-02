import axios from 'axios'

/**
 * 普通接口统一 axios 实例。
 * 注意：SSE 流式对话不走这里（EventSource 不支持 POST），
 * 后续在 src/api/chat.ts 中用 fetch + ReadableStream 单独实现。
 */
const request = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

export default request
