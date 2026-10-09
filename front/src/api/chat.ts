import { fetchEventSource } from '@microsoft/fetch-event-source'

/** 工具调用步骤（由后端 tool_start / tool_end 事件聚合而来） */
export interface ToolStep {
  id: string
  name: string
  input?: unknown
  status: 'running' | 'success' | 'error'
  summary?: string
}

/** RAG 引用来源条目（search_knowledge 的 sources 事件） */
export interface SourceItem {
  doc?: string
  source?: string
  page?: number | string | null
  snippet?: string
}

export interface ChatStreamParams {
  message: string
  /** 为空时后端使用服务端默认模型 */
  model_name?: string
  /** 会话 ID（后端 thread_id） */
  session_id?: string
}

/** 契约事件回调（事件协议见 CLAUDE.md §6.2，字段与后端 agent/events.py 对齐） */
export interface ChatStreamHandlers {
  onMeta?: (data: { session_id: string; model: string | null }) => void
  onToken?: (content: string) => void
  onReasoning?: (content: string) => void
  onToolStart?: (data: { id: string; name: string; input?: unknown }) => void
  onToolEnd?: (data: { id: string; name: string; status: string; summary?: string }) => void
  onSources?: (sources: SourceItem[]) => void
  onError?: (message: string) => void
  onDone?: (usage: Record<string, number>) => void
}

function parseEventData(raw: string): Record<string, unknown> {
  try {
    const data: unknown = JSON.parse(raw)
    return data !== null && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

/** 从非 2xx 响应提取 FastAPI 错误信息（detail 为字符串或校验错误数组） */
async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    const detail = (body as { detail?: unknown } | null)?.detail
    if (typeof detail === 'string' && detail) {
      return detail
    }
    if (Array.isArray(detail)) {
      const messages = detail
        .map(item => (item as { msg?: string } | null)?.msg)
        .filter((msg): msg is string => Boolean(msg))
      if (messages.length) {
        return messages.join('；')
      }
    }
  } catch {
    // 响应体不是 JSON，走兜底文案
  }
  return `请求失败（HTTP ${response.status}）`
}

/**
 * SSE 流式对话（POST /api/chat/stream）。
 * 契约事件的解析只在本文件内完成，调用方只消费回调。
 * 语义：signal.abort() 后 Promise 正常 resolve（不 reject），
 * 调用方以 signal.aborted 区分「手动停止」与「正常结束」。
 */
export function streamChat(
  params: ChatStreamParams,
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  return fetchEventSource('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    signal,
    openWhenHidden: true, // 页面切后台时不中断流
    async onopen(response) {
      if (!response.ok) {
        throw new Error(await readErrorDetail(response))
      }
      const contentType = response.headers.get('content-type') ?? ''
      if (!contentType.startsWith('text/event-stream')) {
        throw new Error(`后端返回了非 SSE 响应（${contentType || '未知类型'}）`)
      }
    },
    onmessage(ev) {
      const payload = parseEventData(ev.data)
      switch (ev.event) {
        case 'meta':
          handlers.onMeta?.({
            session_id: typeof payload.session_id === 'string' ? payload.session_id : '',
            model: typeof payload.model === 'string' ? payload.model : null,
          })
          break
        case 'token':
          if (typeof payload.content === 'string') handlers.onToken?.(payload.content)
          break
        case 'reasoning':
          if (typeof payload.content === 'string') handlers.onReasoning?.(payload.content)
          break
        case 'tool_start':
          if (typeof payload.id === 'string' && typeof payload.name === 'string') {
            handlers.onToolStart?.({ id: payload.id, name: payload.name, input: payload.input })
          }
          break
        case 'tool_end':
          if (typeof payload.id === 'string' && typeof payload.name === 'string') {
            handlers.onToolEnd?.({
              id: payload.id,
              name: payload.name,
              status: typeof payload.status === 'string' ? payload.status : 'success',
              summary: typeof payload.summary === 'string' ? payload.summary : '',
            })
          }
          break
        case 'sources':
          if (Array.isArray(payload.sources)) handlers.onSources?.(payload.sources as SourceItem[])
          break
        case 'error':
          handlers.onError?.(typeof payload.message === 'string' ? payload.message : '运行出错')
          break
        case 'done':
          handlers.onDone?.((payload.usage ?? {}) as Record<string, number>)
          break
        default:
          break // 契约外事件一律忽略
      }
    },
    // 抛出以终止 fetch-event-source 的自动重试：POST 流重放会出现重复回答
    onerror(err) {
      throw err
    },
  })
}
