"""接口测试（pytest）：健康检查 / Swagger / 模型 / 知识库（上传 / 列表 / 检索 / 删除 / 清空）
/ 对话（普通 + SSE）。

运行：cd back && uv run pytest
      uv run pytest -m "not online"   # 仅离线用例（无需 API Key）
后端由 conftest 的 base_url fixture 自动拉起（或 DATAPILOT_BASE_URL 指向已有实例）；
联网用例共用会话级 seeded_kb（3 个测试文档），会话结束自动清空。
"""

import pytest

from config import settings
from test_rag import create_test_docs
from utils import read_sse, upload_file

EXPECTED_ROUTES = {
    "/api/chat",
    "/api/chat/stream",
    "/api/health",
    "/api/knowledge/clear",
    "/api/knowledge/document",
    "/api/knowledge/list",
    "/api/knowledge/search",
    "/api/knowledge/upload",
    "/api/models",
    "/api/models/switch",
}

EXPECTED_MODELS = {"qwen3.7-plus", "deepseek-v3", "glm-5"}

DATA_DOC_NAME = "04-经营数据.md"  # create_test_docs() 返回的经营数据文档名


# ------------------------------------------------------------------ 离线用例


async def test_health(client) -> None:
    """服务存活检查。"""
    response = await client.get("/api/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok", body


async def test_docs_and_openapi_routes(client) -> None:
    """Swagger 页面可访问，OpenAPI 覆盖全部 10 条路由。"""
    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()
    openapi = (await client.get("/openapi.json")).json()
    missing = EXPECTED_ROUTES - set(openapi["paths"])
    assert not missing, f"OpenAPI 缺少路由：{missing}"


async def test_models_list(client) -> None:
    """模型列表返回三个可选模型，默认模型在列表内。"""
    models = (await client.get("/api/models")).json()
    assert set(models["models"]) == EXPECTED_MODELS, models["models"]
    assert models["default_model"] in EXPECTED_MODELS, models["default_model"]


async def test_models_switch_and_restore(client) -> None:
    """切换默认模型 → 非法模型 400 → 恢复原模型（进程内切换，不调用 LLM）。"""
    original = (await client.get("/api/models")).json()["default_model"]
    target = next(model for model in EXPECTED_MODELS if model != original)

    switched = await client.post("/api/models/switch", json={"model_name": target})
    assert switched.status_code == 200, switched.text
    assert switched.json()["default_model"] == target

    bad = await client.post("/api/models/switch", json={"model_name": "gpt-99"})
    assert bad.status_code == 400, bad.text

    restored = await client.post("/api/models/switch", json={"model_name": original})
    assert restored.status_code == 200, restored.text
    assert restored.json()["default_model"] == original


async def test_chat_invalid_model_400(client) -> None:
    """非法模型名在调用 LLM 前即被拒绝。"""
    response = await client.post("/api/chat", json={"message": "你好", "model_name": "gpt-99"})
    assert response.status_code == 400, response.text


async def test_upload_rejects_unsupported_type(client) -> None:
    """不支持的文件类型前置校验 400（不进入入库流程）。"""
    response = await client.post(
        "/api/knowledge/upload", files={"file": ("bad.xyz", b"hello", "text/plain")}
    )
    assert response.status_code == 400, response.text
    assert "不支持的文件类型" in response.json()["detail"]


async def test_upload_rejects_empty_file(client) -> None:
    """空文件前置校验 400。"""
    response = await client.post(
        "/api/knowledge/upload", files={"file": ("empty.txt", b"", "text/plain")}
    )
    assert response.status_code == 400, response.text


async def test_upload_rejects_oversize(client) -> None:
    """超过 MAX_UPLOAD_MB 的文件在读取阶段即被 413 拒绝（服务端强制，不可绕过）。"""
    payload = b"x" * (settings.max_upload_bytes + 1)
    response = await client.post(
        "/api/knowledge/upload", files={"file": ("big.txt", payload, "text/plain")}
    )
    assert response.status_code == 413, response.text


# ------------------------------------------------------------------ 联网用例


@pytest.mark.online
async def test_knowledge_list_matches_uploads(client, seeded_kb: dict[str, int]) -> None:
    """列表与种子上传一致：文件名集合与各片段数吻合，附带文件信息。"""
    listing = (await client.get("/api/knowledge/list")).json()
    listed = {doc["name"]: doc["chunks"] for doc in listing["documents"]}
    assert listed == seeded_kb, f"列表 {listed} ≠ 种子 {seeded_kb}"
    assert listing["total_chunks"] == sum(seeded_kb.values())
    for doc in listing["documents"]:
        assert doc["upload_time"], f"{doc['name']} 应带上传时间"


@pytest.mark.online
async def test_knowledge_upload_idempotent(client, seeded_kb: dict[str, int]) -> None:
    """同名重传幂等：先删同源旧片段再重建，片段数与列表均不变。"""
    data_doc = create_test_docs()[3]
    response = await upload_file(client, data_doc)
    assert response.status_code == 200, response.text
    assert response.json()["chunks"] == seeded_kb[data_doc.name]

    listing = (await client.get("/api/knowledge/list")).json()
    listed = {doc["name"]: doc["chunks"] for doc in listing["documents"]}
    assert listed == seeded_kb, f"重传后列表应不变：{listed}"


@pytest.mark.online
async def test_knowledge_search(client, seeded_kb: dict[str, int]) -> None:
    """检索测试接口：命中经营数据文档（含 4.5%），按相似度降序返回。"""
    assert DATA_DOC_NAME in seeded_kb
    response = await client.post(
        "/api/knowledge/search", json={"query": "产品退货率是多少", "k": 5}
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results, "检索结果不应为空"
    hit = next((item for item in results if item["doc"] == DATA_DOC_NAME), None)
    assert hit is not None, f"应命中 {DATA_DOC_NAME}：{[item['doc'] for item in results]}"
    assert "4.5" in hit["snippet"]
    scores = [item["score"] for item in results]
    assert scores == sorted(scores, reverse=True), f"应按相似度降序：{scores}"
    assert all(-1.0 <= score <= 1.0 for score in scores), scores


@pytest.mark.online
async def test_knowledge_delete_and_restore(client, seeded_kb: dict[str, int]) -> None:
    """删除单个文档（含不存在 404），随后重传恢复种子状态。"""
    deleted = await client.delete("/api/knowledge/document", params={"name": DATA_DOC_NAME})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_chunks"] == seeded_kb[DATA_DOC_NAME]

    listing = (await client.get("/api/knowledge/list")).json()
    names = {doc["name"] for doc in listing["documents"]}
    assert DATA_DOC_NAME not in names, f"删除后不应再出现：{names}"

    missing = await client.delete("/api/knowledge/document", params={"name": "不存在的文档.md"})
    assert missing.status_code == 404, missing.text

    restored = await upload_file(client, create_test_docs()[3])
    assert restored.status_code == 200, restored.text
    listing = (await client.get("/api/knowledge/list")).json()
    assert {doc["name"]: doc["chunks"] for doc in listing["documents"]} == seeded_kb


@pytest.mark.online
async def test_chat_with_tools(client, seeded_kb: dict[str, int]) -> None:
    """普通对话：Agent 触发知识库检索并给出回答。"""
    assert "02-常见问题.md" in seeded_kb
    response = await client.post("/api/chat", json={"message": "DataPilot 支持哪些文档格式？"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["output"].strip(), "回答不应为空"
    tools = [step["tool"] for step in body["intermediate_steps"]]
    assert "search_knowledge" in tools, f"步骤应含 search_knowledge：{tools}"


@pytest.mark.online
async def test_chat_stream_protocol(client, seeded_kb: dict[str, int]) -> None:
    """SSE 流式对话：事件序列、token 拼接、sources、工具事件与 usage（完整契约）。"""
    assert DATA_DOC_NAME in seeded_kb
    default_model = (await client.get("/api/models")).json()["default_model"]
    payload = {"message": "知识库中产品退货率是多少？", "session_id": "api-test"}
    events = await read_sse(client, payload)

    kinds = [name for name, _ in events]
    assert kinds[0] == "meta", f"首个事件应为 meta：{kinds[:3]}"
    assert kinds[-1] == "done", f"末个事件应为 done：{kinds[-3:]}"
    assert events[0][1] == {"session_id": "api-test", "model": default_model}

    tokens = "".join(data["content"] for name, data in events if name == "token")
    assert "4.5" in tokens, f"流式正文应包含检索到的退货率 4.5%：{tokens[:80]}"

    sources = [data["sources"] for name, data in events if name == "sources"]
    assert sources, "应有 sources 事件"
    assert sources[0], "sources 内容不应为空"
    tool_names = [data["name"] for name, data in events if name == "tool_start"]
    assert "search_knowledge" in tool_names, f"工具事件：{tool_names}"
    assert isinstance(events[-1][1].get("usage"), dict), f"done 应携带 usage：{events[-1][1]}"
