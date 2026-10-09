"""端到端联调测试（pytest）：健康 → 模型 → 对话 → 上传 → RAG 问答 → 多步推理。

运行：cd back && uv run pytest tests/test_e2e.py（联网用例需 .env 中的 DASHSCOPE_API_KEY）
知识库链路会先清空并上传 04-经营数据.md，结束后清空（与接口测试的约定一致）。
"""

from typing import Any

import httpx
import pytest

from test_rag import create_test_docs
from utils import normalize_number, upload_file

MULTI_STEP_QUESTION = (
    "请根据知识库中的经营数据计算：如果 2026 年把产品退货率从 4.5% 降到经营目标 2.5%，"
    "可以节省多少退货处理成本？"
)


async def _chat(client: httpx.AsyncClient, message: str) -> dict[str, Any]:
    """POST /api/chat（普通对话）并返回响应 JSON。"""
    response = await client.post("/api/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


async def test_health_and_models_chain(client) -> None:
    """① 健康检查 ② 模型列表（链路前两环，离线可验证）。"""
    health = await client.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["status"] == "ok", health.json()

    models = (await client.get("/api/models")).json()
    expected = {"qwen3.7-plus", "deepseek-v3", "glm-5"}
    assert set(models["models"]) == expected, models["models"]
    assert models["default_model"] in expected, models["default_model"]


@pytest.mark.online
async def test_greeting_chat(client) -> None:
    """③ 普通对话（问候，不需工具）。"""
    greeting = await _chat(client, "你好")
    assert greeting["output"].strip(), greeting


@pytest.mark.online
async def test_knowledge_rag_and_multi_step(client) -> None:
    """④ 上传 ⑤ RAG 问答 ⑥ 多步推理（检索 + 计算 = 24000 元）；结束清空知识库。"""
    data_doc = create_test_docs()[3]
    try:
        cleared = await client.delete("/api/knowledge/clear")
        assert cleared.status_code == 200, cleared.text

        uploaded = await upload_file(client, data_doc)
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["chunks"] > 0

        listing = (await client.get("/api/knowledge/list")).json()
        assert [doc["name"] for doc in listing["documents"]] == [data_doc.name]

        rag = await _chat(client, "知识库中，2025 年产品退货率是多少？")
        tools = [step["tool"] for step in rag["intermediate_steps"]]
        assert "search_knowledge" in tools, f"⑤ 应触发检索工具：{tools}"
        assert "4.5" in rag["output"], f"⑤ 回答应含退货率 4.5%：{rag['output'][:80]}"

        multi = await _chat(client, MULTI_STEP_QUESTION)
        steps = multi["intermediate_steps"]
        assert steps, "⑥ 多步推理应发生工具调用"
        normalized = normalize_number(multi["output"])
        assert "24000" in normalized or "2.4万" in normalized, (
            f"⑥ 结果应为 24000 元：{multi['output'][:100]}"
        )
    finally:
        await client.delete("/api/knowledge/clear")
