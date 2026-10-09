"""路由层：对话（普通 / SSE 流式）、知识库（上传 / 列表 / 检索 / 删除 / 清空）、
模型（列表 / 切换）、健康检查。

约定（CLAUDE.md §6）：
- 全部路由以 /api 为前缀；SSE 事件协议见 §6.2，事件由 agent.stream_agent_events 生产，
  本层只做 SSE 包装（不碰 astream_events），响应带 Cache-Control: no-cache。
- 同步库（Chroma / 文档解析）统一用 asyncio.to_thread 包装，不阻塞事件循环。
- 上传文件落盘 settings.upload_dir，文件名取 basename 防路径穿越；重复上传同名文件
  先删除同源旧片段（幂等）；解析后无文本（如扫描版 PDF）返回 422。
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse

from agent import DataAnalysisAgent
from config import settings
from models import (
    ChatRequest,
    ChatResponse,
    ClearResponse,
    DeleteDocumentResponse,
    DocumentInfo,
    DocumentListResponse,
    HealthResponse,
    ModelsResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SwitchModelRequest,
    SwitchModelResponse,
    ToolStep,
    UploadResponse,
)
from rag import SUPPORTED_SUFFIXES, get_rag_service

__all__: list[str] = ["APP_VERSION", "router"]

APP_VERSION = "0.1.0"

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_model_name(model_name: str | None) -> str:
    """校验并解析模型名（为空用服务端默认）；不合法返回 400。"""
    name = model_name or settings.default_model
    if name not in settings.model_list:
        detail = f"不支持的模型：{name}（可用：{', '.join(settings.model_list)}）"
        raise HTTPException(status_code=400, detail=detail)
    return name


# -------------------------------------------------------------------- 对话

chat_router = APIRouter(prefix="/api/chat", tags=["chat"])


@chat_router.post("", response_model=ChatResponse, summary="普通对话（一次性返回最终回答）")
async def chat(request: ChatRequest) -> ChatResponse:
    """执行一轮对话，返回最终回答与工具步骤；流式版本见 /api/chat/stream。

    整轮对话受 settings.agent_timeout_seconds 约束，超时返回 504（流式版为 error 事件）。
    """
    model_name = _resolve_model_name(request.model_name)
    agent = DataAnalysisAgent(model_name, session_id=request.session_id or "default")
    try:
        async with asyncio.timeout(settings.agent_timeout_seconds):
            result = await agent.run(request.message)
    except TimeoutError:
        logger.warning("对话超时：%.0f 秒", settings.agent_timeout_seconds)
        seconds = f"{settings.agent_timeout_seconds:.0f}"
        raise HTTPException(
            status_code=504, detail=f"对话超时（超过 {seconds} 秒），请重试"
        ) from None
    return ChatResponse(
        model_name=model_name,
        output=result["output"],
        intermediate_steps=[ToolStep(**step) for step in result["intermediate_steps"]],
    )


@chat_router.post(
    "/stream",
    response_class=EventSourceResponse,
    summary="SSE 流式对话（事件协议见 CLAUDE.md §6.2）",
)
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    """以 text/event-stream 逐条推送契约事件：meta → token / tool_start / … → done。"""
    model_name = _resolve_model_name(request.model_name)
    session_id = request.session_id or "default"

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        agent = DataAnalysisAgent(model_name, session_id=session_id)
        async for event, data in agent.stream(request.message):
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(
        event_generator(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ 知识库

knowledge_router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_SUPPORTED_HINT = "、".join(sorted(SUPPORTED_SUFFIXES))

_UPLOAD_CHUNK_SIZE = 1024 * 1024


async def _read_upload_limited(file: UploadFile) -> bytes:
    """分块读取上传内容，超过 settings.max_upload_mb 立即中断。

    前端 KnowledgeView 的 20MB 校验只是提示（可绕过），服务端必须独立强制。

    Raises:
        HTTPException: 413 文件超过上限；400 文件为空。
    """
    limit = settings.max_upload_bytes
    buffer = bytearray()
    while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 上限")
    if not buffer:
        raise HTTPException(status_code=400, detail="上传文件为空")
    return bytes(buffer)


@knowledge_router.post("/upload", response_model=UploadResponse, summary="上传文档并入库")
async def upload_document(
    file: Annotated[UploadFile, File(description="待入库文档（PDF / TXT / Markdown）")],
) -> UploadResponse:
    """保存上传文件（settings.upload_dir）→ 解析切分 → 嵌入入库，返回片段数。

    同名文件重复上传为幂等操作（先删同源旧片段）；类型、大小与空文件前置校验。
    """
    filename = Path(file.filename or "").name
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        detail = f"不支持的文件类型：{suffix or '无扩展名'}（支持 {_SUPPORTED_HINT}）"
        raise HTTPException(status_code=400, detail=detail)

    content = await _read_upload_limited(file)

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    target = settings.upload_dir / filename
    await asyncio.to_thread(target.write_bytes, content)

    service = get_rag_service()
    await asyncio.to_thread(service.delete_document, target)  # 幂等：清掉同源旧片段
    try:
        chunks = await asyncio.to_thread(service.add_documents, [target])
    except Exception as exc:  # 解析 / 入库错误不可枚举，已记日志并转 422（BLE001 豁免）
        logger.exception("文档解析或入库失败：%s", target)
        detail = f"文档解析或入库失败：{type(exc).__name__}: {exc}"
        raise HTTPException(status_code=422, detail=detail) from exc
    if chunks == 0:
        raise HTTPException(
            status_code=422,
            detail="文档中没有可用文本（如扫描版 PDF 无 OCR，或内容为空）",
        )
    logger.info("上传入库完成：%s → %d 个片段", filename, chunks)
    return UploadResponse(filename=filename, chunks=chunks, message=f"已入库 {chunks} 个片段")


@knowledge_router.get("/list", response_model=DocumentListResponse, summary="已入库文档列表")
async def list_documents() -> DocumentListResponse:
    """按源文件聚合列出已入库文档、片段数与文件信息（大小 / 上传时间）。"""
    items = await asyncio.to_thread(get_rag_service().list_documents)
    return DocumentListResponse(
        documents=[DocumentInfo(**item) for item in items],
        total_chunks=sum(item["chunks"] for item in items),
    )


@knowledge_router.post(
    "/search", response_model=SearchResponse, summary="检索测试（直接查询向量库）"
)
async def search_knowledge_base(request: SearchRequest) -> SearchResponse:
    """对当前知识库做相似度检索（不经 Agent），返回片段与余弦相似度分数。"""
    results = await asyncio.to_thread(
        get_rag_service().search_with_scores, request.query, request.k
    )
    return SearchResponse(
        query=request.query,
        results=[SearchResultItem(**item) for item in results],
    )


@knowledge_router.delete("/document", response_model=DeleteDocumentResponse, summary="删除单个文档")
async def delete_document(name: str) -> DeleteDocumentResponse:
    """按文件名删除文档的全部片段；同时清理已上传文件（uploads 目录内，若有）。

    文件名按 basename 匹配（与列表聚合口径一致）；文档不存在返回 404。
    """
    deleted = await asyncio.to_thread(get_rag_service().delete_document_by_name, name)
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"文档不存在：{name}")
    # 清理上传落盘文件（仅取 basename 防路径穿越；批量导入的 knowledge_docs 文件不在此目录）
    upload_path = settings.upload_dir / Path(name).name
    if upload_path.is_file():
        await asyncio.to_thread(upload_path.unlink)
    logger.info("删除文档：%s → %d 个片段", name, deleted)
    return DeleteDocumentResponse(
        name=name, deleted_chunks=deleted, message=f"已删除 {deleted} 个片段"
    )


@knowledge_router.delete("/clear", response_model=ClearResponse, summary="清空向量库")
async def clear_knowledge() -> ClearResponse:
    """清空当前知识库（删除并重建 collection）；不删除 uploads 目录中的文件。"""
    await asyncio.to_thread(get_rag_service().clear)
    return ClearResponse(message="知识库已清空")


# -------------------------------------------------------------------- 模型

models_router = APIRouter(prefix="/api/models", tags=["models"])


@models_router.get("", response_model=ModelsResponse, summary="可用模型列表")
async def list_models() -> ModelsResponse:
    """返回可切换的模型列表与当前默认模型。"""
    return ModelsResponse(models=settings.model_list, default_model=settings.default_model)


@models_router.post("/switch", response_model=SwitchModelResponse, summary="切换默认模型")
async def switch_model(request: SwitchModelRequest) -> SwitchModelResponse:
    """切换进程内默认模型（请求未指定 model_name 时生效；重启后回到 .env 配置）。"""
    if request.model_name not in settings.model_list:
        detail = f"不支持的模型：{request.model_name}（可用：{', '.join(settings.model_list)}）"
        raise HTTPException(status_code=400, detail=detail)
    settings.default_model = request.model_name
    logger.info("默认模型已切换为：%s", request.model_name)
    return SwitchModelResponse(
        message=f"默认模型已切换为 {request.model_name}", default_model=request.model_name
    )


# ---------------------------------------------------------------- 健康检查

system_router = APIRouter(prefix="/api", tags=["system"])


@system_router.get("/health", response_model=HealthResponse, summary="健康检查")
async def health() -> HealthResponse:
    """服务存活检查（不探测模型 / 向量库连通性）。"""
    return HealthResponse(status="ok", version=APP_VERSION)


# -------------------------------------------------------------------- 汇总

router.include_router(chat_router)
router.include_router(knowledge_router)
router.include_router(models_router)
router.include_router(system_router)
