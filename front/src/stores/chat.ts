import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { streamChat, type SourceItem, type ToolStep } from '@/api/chat'
import { fetchModels } from '@/api/models'

export type MessageStatus = 'streaming' | 'done' | 'error' | 'stopped'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 深度思考内容（后端 reasoning 事件增量，可能为空） */
  reasoning: string
  /** 工具调用步骤（推理过程面板） */
  steps: ToolStep[]
  /** RAG 引用来源 */
  sources: SourceItem[]
  /** 实际作答模型（meta 事件回传后覆盖） */
  model?: string
  status: MessageStatus
  error?: string
}

export interface ChatSession {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
}

const TITLE_MAX_LENGTH = 20

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ChatSession[]>([])
  const activeSessionId = ref('')
  const availableModels = ref<string[]>([])
  /** 当前选中模型（每次请求作为 model_name 发送，不改服务端默认值） */
  const model = ref('')
  const streaming = ref(false)

  // 当前流的取消句柄（非响应式，不进 state）
  let abortController: AbortController | null = null

  const activeSession = computed(
    () => sessions.value.find(session => session.id === activeSessionId.value) ?? null,
  )

  function newSession(): void {
    const session: ChatSession = {
      id: crypto.randomUUID(), // 本地会话 ID，同时作为后端 session_id（thread_id）
      title: '新对话',
      messages: [],
      createdAt: Date.now(),
    }
    sessions.value.unshift(session)
    activeSessionId.value = session.id
  }

  function selectSession(id: string): void {
    activeSessionId.value = id
  }

  /** 删除会话；是否处于流式生成的校验由调用方负责（见 SessionList） */
  function deleteSession(id: string): void {
    const index = sessions.value.findIndex(session => session.id === id)
    if (index === -1) {
      return
    }
    sessions.value.splice(index, 1)
    if (activeSessionId.value === id) {
      activeSessionId.value = sessions.value[0]?.id ?? ''
    }
  }

  async function loadModels(): Promise<void> {
    if (availableModels.value.length) {
      return
    }
    const info = await fetchModels()
    availableModels.value = info.models
    if (!model.value) {
      model.value = info.default_model
    }
  }

  function stopStreaming(): void {
    abortController?.abort()
  }

  async function sendMessage(text: string): Promise<void> {
    const content = text.trim()
    if (!content || streaming.value) {
      return
    }
    if (!activeSession.value) {
      newSession()
    }
    const session = activeSession.value
    if (!session) {
      return
    }
    if (!session.messages.length) {
      session.title =
        content.length > TITLE_MAX_LENGTH ? `${content.slice(0, TITLE_MAX_LENGTH)}…` : content
    }

    session.messages.push({
      id: crypto.randomUUID(),
      role: 'user',
      content,
      reasoning: '',
      steps: [],
      sources: [],
      status: 'done',
    })
    session.messages.push({
      id: crypto.randomUUID(),
      role: 'assistant',
      content: '',
      reasoning: '',
      steps: [],
      sources: [],
      model: model.value || undefined,
      status: 'streaming',
    })
    // 重新从响应式数组读取以拿到槽内的 reactive 代理：
    // 直接改 push 前的原始对象不会触发视图更新
    const reply = session.messages[session.messages.length - 1]
    if (!reply) {
      return
    }

    streaming.value = true
    abortController = new AbortController()
    const { signal } = abortController
    try {
      await streamChat(
        { message: content, model_name: model.value || undefined, session_id: session.id },
        {
          onMeta: data => {
            if (data.model) {
              reply.model = data.model
            }
          },
          onToken: chunk => {
            reply.content += chunk
          },
          onReasoning: chunk => {
            reply.reasoning += chunk
          },
          onToolStart: data => {
            reply.steps.push({
              id: data.id,
              name: data.name,
              input: data.input,
              status: 'running',
            })
          },
          onToolEnd: data => {
            const step = reply.steps.find(item => item.id === data.id)
            if (step) {
              step.status = data.status === 'error' ? 'error' : 'success'
              step.summary = data.summary
            }
          },
          onSources: sources => {
            reply.sources.push(...sources)
          },
          onError: message => {
            reply.status = 'error'
            reply.error = message
          },
        },
        signal,
      )
      if (reply.status === 'streaming') {
        reply.status = signal.aborted ? 'stopped' : 'done'
      }
    } catch (error) {
      reply.status = signal.aborted ? 'stopped' : 'error'
      if (!signal.aborted) {
        reply.error = error instanceof Error ? error.message : String(error)
      }
    } finally {
      streaming.value = false
      abortController = null
    }
  }

  return {
    sessions,
    activeSessionId,
    availableModels,
    model,
    streaming,
    activeSession,
    newSession,
    selectSession,
    deleteSession,
    loadModels,
    sendMessage,
    stopStreaming,
  }
})
