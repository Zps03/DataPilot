"""API 请求 / 响应模型（pydantic schema）。

命名约定：XxxRequest / XxxResponse；内嵌 schema（ToolStep / DocumentInfo）贯穿请求响应。
SSE 流式接口不使用这些模型（事件协议见 CLAUDE.md §6.2）。
"""

from typing import Any

from pydantic import BaseModel, Field

__all__: list[str] = [
    "ChatRequest",
    "ChatResponse",
    "ClearResponse",
    "DocumentInfo",
    "DocumentListResponse",
    "HealthResponse",
    "ModelsResponse",
    "SwitchModelRequest",
    "SwitchModelResponse",
    "ToolStep",
    "UploadResponse",
]


class ChatRequest(BaseModel):
    """对话请求（普通对话与 SSE 流式共用）。"""

    message: str = Field(min_length=1, description="用户消息")
    model_name: str | None = Field(default=None, description="对话模型；为空时用服务端默认模型")
    session_id: str | None = Field(
        default=None, description="会话 ID（预留：会话持久化阶段生效）；为空时用 default"
    )


class ToolStep(BaseModel):
    """一次工具调用步骤（对应 Agent run() 的 intermediate_steps 条目）。"""

    tool: str = Field(description="工具名")
    input: dict[str, Any] = Field(description="工具入参")
    output: str = Field(description="工具结果文本（过长会截断）")


class ChatResponse(BaseModel):
    """普通对话响应。"""

    model_name: str = Field(description="实际使用的模型")
    output: str = Field(description="Agent 最终回答")
    intermediate_steps: list[ToolStep] = Field(description="工具调用步骤（推理过程）")


class UploadResponse(BaseModel):
    """文档上传响应。"""

    filename: str = Field(description="入库文件名（取上传文件名）")
    chunks: int = Field(description="本次入库的片段数")
    message: str


class DocumentInfo(BaseModel):
    """已入库文档（按源文件聚合）。"""

    name: str = Field(description="文件名")
    chunks: int = Field(description="片段数")


class DocumentListResponse(BaseModel):
    """知识库文档列表。"""

    documents: list[DocumentInfo]
    total_chunks: int


class ModelsResponse(BaseModel):
    """可用模型列表。"""

    models: list[str]
    default_model: str


class SwitchModelRequest(BaseModel):
    """切换默认模型请求。"""

    model_name: str = Field(description="目标模型名，须在可用模型列表内")


class SwitchModelResponse(BaseModel):
    """切换默认模型响应。"""

    message: str
    default_model: str


class ClearResponse(BaseModel):
    """清空知识库响应。"""

    message: str


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    version: str
