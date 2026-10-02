import axios, {
  type AxiosError,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { ElMessage } from 'element-plus'

/**
 * 普通接口统一 axios 实例（baseURL 为 /api，开发环境经 Vite 代理到后端）。
 * 注意：SSE 流式对话不走这里（EventSource 不支持 POST），
 * 后续在 src/api/chat.ts 中单独实现。
 */
const request = axios.create({
  baseURL: '/api',
  timeout: 120_000,
})

// 请求拦截器：预留统一处理入口（单机单用户无鉴权，当前直接放行）
request.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => config,
  (error: AxiosError) => Promise.reject(error),
)

// 响应拦截器：统一错误提示；FastAPI 错误体为 {detail: ...}
request.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError<{ detail?: unknown }>) => {
    ElMessage.error(resolveErrorMessage(error))
    return Promise.reject(error)
  },
)

function resolveErrorMessage(error: AxiosError<{ detail?: unknown }>): string {
  const detail = error.response?.data?.detail
  if (typeof detail === 'string' && detail) {
    return detail
  }
  if (error.code === 'ECONNABORTED') {
    return '请求超时，请稍后重试'
  }
  if (!error.response) {
    return '无法连接后端服务，请确认后端已启动（端口 8000）'
  }
  return `请求失败（HTTP ${error.response.status}）`
}

export default request
