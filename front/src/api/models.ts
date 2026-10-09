import request from './request'

export interface ModelsInfo {
  models: string[]
  default_model: string
}

/** 可用模型列表与当前默认模型（GET /api/models） */
export async function fetchModels(): Promise<ModelsInfo> {
  const { data } = await request.get<ModelsInfo>('/models')
  return data
}
