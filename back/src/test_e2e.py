"""端到端联调测试（httpx）：健康检查 → 模型列表 → 普通对话 → 知识库上传 → RAG 问答 → 多步推理。

运行：cd back && uv run python src/test_e2e.py
前置：后端已启动（uv run uvicorn main:app --app-dir src --port 8000），
      并已配置 DASHSCOPE_API_KEY（上传入库、检索与对话需联网）。
脚本会先清空知识库再上传测试文档，结束时再次清空（与 test_api.py 约定一致）。
"""

import asyncio
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from test_rag import create_test_docs

BASE_URL = "http://127.0.0.1:8000"

# Windows 控制台默认 GBK 会乱码，统一强制 UTF-8 输出（PyCharm / Windows Terminal 均适用）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[FAIL] {message}")
    print(f"[OK] {message}")


def _normalize(text: str) -> str:
    """去掉千分位逗号与空白，便于数字断言（如「24,000 元」→「24000元」）。"""
    return re.sub(r"[,\s]", "", text)


async def _chat(client: httpx.AsyncClient, message: str) -> dict[str, Any]:
    """POST /api/chat（普通对话）并返回响应 JSON。"""
    response = await client.post("/api/chat", json={"message": message})
    _assert(response.status_code == 200, f"对话 HTTP 200：{message[:24]}…")
    return response.json()


async def _main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180.0) as client:
        # ① 健康检查
        try:
            response = await client.get("/api/health")
        except httpx.ConnectError:
            hint = "无法连接后端：请先运行 uv run uvicorn main:app --app-dir src --port 8000"
            raise SystemExit(hint) from None
        _assert(response.status_code == 200, "① 健康检查 HTTP 200")
        _assert(response.json()["status"] == "ok", f"① 服务状态：{response.json()}")

        # ② 模型列表（三个可选模型 + 默认模型在列）
        models = (await client.get("/api/models")).json()
        expected = {"qwen3.7-plus", "deepseek-v3", "glm-5"}
        _assert(set(models["models"]) == expected, f"② 模型列表返回三个：{models['models']}")
        _assert(models["default_model"] in expected, f"② 默认模型：{models['default_model']}")

        # ③ 普通对话（问候，不需工具）
        greeting = await _chat(client, "你好")
        preview = greeting["output"][:60].replace("\n", " ")
        _assert(bool(greeting["output"].strip()), f"③ 问候回复（{greeting['model_name']}）：{preview}…")

        # ④ 知识库：清空 → 上传测试文档（04-经营数据.md，含退货率与成本数据）
        clear = await client.delete("/api/knowledge/clear")
        _assert(clear.status_code == 200, "④ 前置清空知识库")
        fixtures = create_test_docs()
        data_doc: Path = fixtures[3]
        with data_doc.open("rb") as handle:
            uploaded = await client.post(
                "/api/knowledge/upload",
                files={"file": (data_doc.name, handle, "application/octet-stream")},
            )
        _assert(uploaded.status_code == 200, f"④ 上传 {data_doc.name} HTTP 200")
        info = uploaded.json()
        _assert(info["chunks"] > 0, f"④ 入库完成：{info['filename']} 共 {info['chunks']} 个片段")
        listing = (await client.get("/api/knowledge/list")).json()
        _assert(
            [doc["name"] for doc in listing["documents"]] == [data_doc.name],
            f"④ 列表可见：{[doc['name'] for doc in listing['documents']]}",
        )

        # ⑤ RAG 问答（需触发 search_knowledge 检索知识库）
        rag = await _chat(client, "知识库中，2025 年产品退货率是多少？")
        tools = [step["tool"] for step in rag["intermediate_steps"]]
        _assert("search_knowledge" in tools, f"⑤ 触发检索工具：{tools}")
        _assert("4.5" in rag["output"], f"⑤ 回答含退货率 4.5%：{rag['output'][:80]}…")

        # ⑥ 多步推理（先检索事实，再计算：4.5% → 2.5% 共 2 个百分点 × 12,000 元 = 24,000 元）
        multi = await _chat(
            client,
            "请根据知识库中的经营数据计算：如果 2026 年把产品退货率从 4.5% 降到经营目标 2.5%，"
            "可以节省多少退货处理成本？",
        )
        steps = multi["intermediate_steps"]
        tool_names = [step["tool"] for step in steps]
        _assert(len(steps) >= 1, f"⑥ 多步推理发生工具调用：{tool_names}")
        normalized = _normalize(multi["output"])
        _assert(
            "24000" in normalized or "2.4万" in normalized,
            f"⑥ 计算结果正确（应为 24000 元）：{multi['output'][:100]}…",
        )

        # 收尾：清空知识库
        await client.delete("/api/knowledge/clear")
        listing = (await client.get("/api/knowledge/list")).json()
        _assert(listing["documents"] == [], "收尾：知识库已清空")

    print("[OK] 端到端联调全部通过")


if __name__ == "__main__":
    asyncio.run(_main())
