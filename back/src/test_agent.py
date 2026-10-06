"""Agent 模块测试：多步推理（stream 事件流 + 工具链）、run 契约、多轮记忆。

运行：cd back && uv run python src/test_agent.py
前置：已配置 DASHSCOPE_API_KEY（back/.env）。脚本会先清空并重建测试知识库
（复用 test_rag 的样例文档，含「04-经营数据.md」供养多步推理场景）。

用例：
1) 多步推理：检索知识库（退货率 / 退货成本）→ 调计算工具算「退货率降 2%」的节省金额；
2) run() 契约：返回 {"output", "intermediate_steps"}，chat_history 走无状态单次调用；
3) 多轮记忆：同一实例连续 run()（checkpointer 路径）记住上下文。
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from typing import Any

from agent import DataAnalysisAgent
from config import settings
from rag import RagService
from test_rag import create_test_docs

# Windows 控制台默认 GBK 会乱码，统一强制 UTF-8 输出（PyCharm / Windows Terminal 均适用）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

MULTI_STEP_QUESTION = "我们知识库中提到的产品退货率是多少？帮我计算如果退货率降低 2% 能节省多少成本"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[FAIL] {message}")
    print(f"[OK] {message}")


def _compact(kinds: list[str]) -> str:
    """事件序列压缩显示：连续同类合并为 ×N。"""
    merged: list[tuple[str, int]] = []
    for kind in kinds:
        if merged and merged[-1][0] == kind:
            name, count = merged[-1]
            merged[-1] = (name, count + 1)
        else:
            merged.append((kind, 1))
    return " → ".join(name if count == 1 else f"{name}×{count}" for name, count in merged)


async def _consume(
    events: AsyncIterator[tuple[str, dict[str, Any]]], label: str
) -> tuple[str, list[str], list[dict[str, Any]], list[str]]:
    """消费事件流并打印推理过程，返回 (answer, kinds, sources, tools_called)。"""
    print(f"--- {label} ---")
    answer_parts: list[str] = []
    sources: list[dict[str, Any]] = []
    tools_called: list[str] = []
    kinds: list[str] = []
    async for event, data in events:
        kinds.append(event)
        if event == "token":
            answer_parts.append(data["content"])
        elif event == "tool_start":
            tools_called.append(data["name"])
            print(f"  [tool_start] {data['name']} {data['input']}")
        elif event == "tool_end":
            print(f"  [tool_end]   {data['name']} → {data['status']}")
        elif event == "sources":
            sources = data["sources"]
            joined = "、".join(
                f"{s['doc']}（第 {s['page']} 页）" if s.get("page") else s["doc"]
                for s in sources[:3]
            )
            print(f"  [sources]    {len(sources)} 条：{joined}")
        elif event == "error":
            raise SystemExit(f"[FAIL] {label} 流式错误：{data['message']}")
    answer = "".join(answer_parts)
    print(f"助手：{answer}")
    print(f"  事件序列：{_compact(kinds)}\n")
    return answer, kinds, sources, tools_called


def _show_run(label: str, result: dict[str, Any]) -> None:
    """打印 run() 的最终回答与工具步骤（推理过程）。"""
    print(f"--- {label} ---")
    print(f"助手：{result['output']}")
    print("  工具步骤：")
    for step in result["intermediate_steps"]:
        preview = " ".join(step["output"].split())[:60]
        print(f"    - {step['tool']}({step['input']}) → {preview}…")
    print()


async def _main() -> None:
    # 1) 重建测试知识库（幂等）
    create_test_docs()
    service = RagService()
    service.clear()
    count = service.add_directory()
    print(f"[OK] 测试知识库就绪：{count} 个片段（collection={service.collection_name}）\n")

    model = settings.default_model

    # 2) 多步推理：search_knowledge 检索 → calculator / python_executor 计算（stream 事件流）
    print(f"用户：{MULTI_STEP_QUESTION}")
    agent = DataAnalysisAgent(model)
    answer, kinds, sources, tools_called = await _consume(
        agent.stream(MULTI_STEP_QUESTION), label="多步推理（stream 事件流）"
    )
    _assert("meta" in kinds and "done" in kinds, "事件流包含 meta / done 事件")
    _assert("token" in kinds and bool(answer), f"收到流式正文（{len(answer)} 字符）")
    _assert("search_knowledge" in tools_called, "第 1 步：调用了 search_knowledge 检索知识库")
    _assert(
        bool({"calculator", "python_executor"} & set(tools_called)),
        "第 2 步：调用了计算 / 数据处理工具",
    )
    _assert(bool(sources), f"sources 事件包含来源（{len(sources)} 条）")
    _assert("4.5" in answer, "回答引用了检索到的退货率 4.5%")
    _assert("元" in answer and ("省" in answer or "减少" in answer), "回答了节省金额")

    # 3) run() 契约：{"output", "intermediate_steps"}；chat_history 走无状态单次调用
    question = "知识库示例文档中，产品退货率是多少？"
    print(f"用户：{question}（附 chat_history）")
    run_agent = DataAnalysisAgent(model)
    result = await run_agent.run(
        question,
        chat_history=[{"role": "user", "content": "你好，我需要了解一些经营数据。"}],
    )
    _show_run("run() 契约（含 chat_history 无状态调用）", result)
    _assert(isinstance(result["output"], str) and bool(result["output"]), "run() 返回最终回答")
    steps = result["intermediate_steps"]
    _assert(isinstance(steps, list) and bool(steps), f"run() 返回工具步骤（{len(steps)} 步）")
    _assert(
        all(set(step) == {"tool", "input", "output"} for step in steps),
        "步骤字段为 {tool, input, output}",
    )
    _assert(any(step["tool"] == "search_knowledge" for step in steps), "步骤含 search_knowledge")
    _assert("4.5" in result["output"], "回答包含检索到的退货率数据")

    # 4) 多轮记忆：同一实例连续 run()，checkpointer 累积上下文
    memory_agent = DataAnalysisAgent(model)
    first = await memory_agent.run("你好，我叫小明，请记住我的名字。")
    _assert(bool(first["output"]), "多轮 · 第 1 轮返回回答")
    second = await memory_agent.run("我叫什么名字？")
    _show_run("多轮记忆 · 第 2 轮（追问）", second)
    _assert("小明" in second["output"], "第二轮回答记住了姓名（多轮记忆生效）")

    print("[OK] 全部验证通过")


def main() -> None:
    if not settings.dashscope_api_key:
        raise SystemExit("未配置 DASHSCOPE_API_KEY：请先在 back/.env 填写后重试")
    asyncio.run(_main())


if __name__ == "__main__":
    main()
