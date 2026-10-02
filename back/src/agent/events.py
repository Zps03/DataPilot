"""astream_events(v2) → SSE 事件协议适配层（前后端契约见 CLAUDE.md §6.2）。

产出 `(event, data)` 元组：meta / token / reasoning / tool_start / tool_end / sources /
error / done；langgraph 单轮事件可达上千条，这里只保留契约内事件，其余全部丢弃。

注意：显式指定 `version="v2"`——langchain-core 1.6 已有实验性 v3 事件流，
禁止依赖默认值，避免升级后事件形态悄悄变化。
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from tools import SEARCH_TOOL_NAME

logger = logging.getLogger(__name__)


def _tool_content(tool_output: Any) -> str:
    """取 ToolMessage 的文本内容（非字符串返回空串）。"""
    content = getattr(tool_output, "content", tool_output)
    return content if isinstance(content, str) else ""


def _summarize(tool_output: Any, max_len: int = 120) -> str:
    """工具输出摘要（折叠空白并截断，用于 tool_end 的 summary 字段）。"""
    text = " ".join(_tool_content(tool_output).split())
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _extract_sources(tool_output: Any) -> list[dict[str, Any]]:
    """读取检索工具 ToolMessage.artifact 中的 sources；缺失时回退解析 JSON 内容。"""
    artifact = getattr(tool_output, "artifact", None)
    if isinstance(artifact, dict) and isinstance(artifact.get("sources"), list):
        return artifact["sources"]
    try:
        payload = json.loads(_tool_content(tool_output))
    except json.JSONDecodeError:
        return []
    sources = payload.get("sources") if isinstance(payload, dict) else None
    return sources if isinstance(sources, list) else []


async def stream_agent_events(
    agent: CompiledStateGraph,
    message: str,
    *,
    session_id: str = "default",
    model: str | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """运行 Agent 并逐条产出契约事件。

    Args:
        agent: get_agent() 构建的 Agent。
        message: 本轮用户输入。
        session_id: 会话 ID（thread_id）；配合 checkpointer 实现多轮记忆。
        model: 模型名，仅用于 meta 事件回传前端。
    """
    yield "meta", {"session_id": session_id, "model": model}

    usage: dict[str, int] = {}
    config = {"configurable": {"thread_id": session_id}}
    try:
        async for event in agent.astream_events(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            version="v2",
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk is None:
                    continue
                reasoning = chunk.additional_kwargs.get("reasoning_content")
                if reasoning:
                    yield "reasoning", {"content": reasoning}
                if isinstance(chunk.content, str) and chunk.content:
                    yield "token", {"content": chunk.content}
            elif kind == "on_chat_model_end":
                meta = getattr(event["data"].get("output"), "usage_metadata", None)
                if meta:
                    for key, value in meta.items():
                        if isinstance(value, int):
                            usage[key] = usage.get(key, 0) + value
            elif kind == "on_tool_start":
                yield "tool_start", {
                    "id": event["run_id"],
                    "name": event["name"],
                    "input": event["data"].get("input"),
                }
            elif kind == "on_tool_end":
                output = event["data"].get("output")
                yield "tool_end", {
                    "id": event["run_id"],
                    "name": event["name"],
                    "status": "success",
                    "summary": _summarize(output),
                }
                if event["name"] == SEARCH_TOOL_NAME:
                    sources = _extract_sources(output)
                    if sources:
                        yield "sources", {"sources": sources}
            elif kind == "on_tool_error":
                yield "tool_end", {
                    "id": event["run_id"],
                    "name": event["name"],
                    "status": "error",
                    "summary": str(event["data"].get("error"))[:200],
                }
    except Exception:  # 已记日志，转为 error 事件收尾（BLE001 豁免）
        logger.exception("Agent 流式运行失败")
        yield "error", {"message": "运行出错，请查看后端日志"}
        return

    yield "done", {"usage": usage}
