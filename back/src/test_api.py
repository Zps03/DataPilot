"""API 路由测试：健康检查 / Swagger / 模型列表与切换 / 知识库上传-列表-检索-删除-清空 /
对话（普通 + SSE）。

运行：cd back && uv run python src/test_api.py
前置：另开终端启动后端（uv run uvicorn main:app --app-dir src --port 8000），
      并已配置 DASHSCOPE_API_KEY（上传入库、检索与对话需联网）。脚本会清空并重建知识库，
      结束时知识库为空。
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from config import settings
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


async def _upload(client: httpx.AsyncClient, path: Path) -> dict[str, Any]:
    """上传本地文件到 /api/knowledge/upload，返回响应 JSON。"""
    with path.open("rb") as handle:
        response = await client.post(
            "/api/knowledge/upload",
            files={"file": (path.name, handle, "application/octet-stream")},
        )
    _assert(response.status_code == 200, f"上传 {path.name} → {response.status_code}")
    return response.json()


async def _read_sse(
    client: httpx.AsyncClient, payload: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """POST /api/chat/stream 并按 SSE 协议解析为 (event, data) 列表。"""
    events: list[tuple[str, dict[str, Any]]] = []
    current = ""
    async with client.stream("POST", "/api/chat/stream", json=payload) as response:
        _assert(response.status_code == 200, f"SSE 状态码 {response.status_code}")
        content_type = response.headers["content-type"]
        _assert(content_type.startswith("text/event-stream"), f"Content-Type：{content_type}")
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                events.append((current, json.loads(line.removeprefix("data:").strip())))
    return events


async def _main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180.0) as client:
        # 0) 服务可达性 + 健康检查
        try:
            response = await client.get("/api/health")
        except httpx.ConnectError:
            hint = "无法连接后端：请先运行 uv run uvicorn main:app --app-dir src --port 8000"
            raise SystemExit(hint) from None
        _assert(response.status_code == 200, "健康检查 HTTP 200")
        health = response.json()
        _assert(health["status"] == "ok", f"健康状态：{health}")

        # 1) Swagger / OpenAPI 覆盖
        docs = await client.get("/docs")
        _assert(docs.status_code == 200 and "swagger" in docs.text.lower(), "/docs 页面可访问")
        openapi = (await client.get("/openapi.json")).json()
        expected = {
            "/api/chat",
            "/api/chat/stream",
            "/api/knowledge/upload",
            "/api/knowledge/list",
            "/api/knowledge/search",
            "/api/knowledge/document",
            "/api/knowledge/clear",
            "/api/models",
            "/api/models/switch",
            "/api/health",
        }
        _assert(expected <= set(openapi["paths"]), f"OpenAPI 覆盖全部 {len(expected)} 条路由")

        # 2) 模型列表与切换
        models = (await client.get("/api/models")).json()
        _assert(
            set(models["models"]) == {"qwen3.7-plus", "deepseek-v3", "glm-5"},
            f"模型列表：{models['models']}",
        )
        original = models["default_model"]
        switched = await client.post("/api/models/switch", json={"model_name": "deepseek-v3"})
        _assert(
            switched.status_code == 200 and switched.json()["default_model"] == "deepseek-v3",
            "切换默认模型 → deepseek-v3",
        )
        bad = await client.post("/api/models/switch", json={"model_name": "gpt-99"})
        _assert(bad.status_code == 400, f"非法模型名返回 400：{bad.json()['detail']}")
        back = await client.post("/api/models/switch", json={"model_name": original})
        _assert(back.status_code == 200, f"恢复默认模型：{original}")

        # 3) 知识库生命周期：清空 → 上传（幂等）→ 列表
        _assert((await client.delete("/api/knowledge/clear")).status_code == 200, "清空知识库")
        listing = (await client.get("/api/knowledge/list")).json()
        _assert(listing["documents"] == [], "清空后列表为空")

        fixtures = create_test_docs()
        for fixture in (fixtures[0], fixtures[1], fixtures[3]):  # txt / md / 经营数据 md
            uploaded = await _upload(client, fixture)
            print(f"  [upload] {uploaded['filename']} → {uploaded['chunks']} 个片段")
            _assert(uploaded["chunks"] > 0, f"{fixture.name} 入库片段数 > 0")

        before = (await client.get("/api/knowledge/list")).json()
        _assert(
            len(before["documents"]) == 3,
            f"列表 3 个文档：{[d['name'] for d in before['documents']]}",
        )
        first = next(d for d in before["documents"] if d["name"] == fixtures[0].name)
        _assert(
            first["size"] == fixtures[0].stat().st_size, f"列表含文件大小：{first['size']} 字节"
        )
        _assert(
            isinstance(first["upload_time"], str) and "T" in first["upload_time"],
            f"列表含上传时间：{first['upload_time']}",
        )
        await _upload(client, fixtures[3])  # 重复上传同名文件
        after = (await client.get("/api/knowledge/list")).json()
        # 重传会刷新文件 mtime（upload_time 变化），幂等只看文档集合与片段数
        _assert(
            [(d["name"], d["chunks"]) for d in before["documents"]]
            == [(d["name"], d["chunks"]) for d in after["documents"]],
            "重复上传同名文件幂等（片段数不变）",
        )

        # 3.5) 检索测试（余弦相似度分数 + k 限制）
        search = (await client.post("/api/knowledge/search", json={"query": "退货率是多少"})).json()
        _assert(bool(search["results"]), f"检索返回 {len(search['results'])} 条结果")
        top = search["results"][0]
        _assert(top["doc"] == fixtures[3].name, f"最相关片段来自经营数据：{top['doc']}")
        _assert(0.0 < top["score"] <= 1.0, f"余弦相似度在 (0, 1]：{top['score']}")
        _assert(bool(top["snippet"]) and len(top["snippet"]) <= 200, "片段文本非空且截断 200 字符")
        limited = (
            await client.post("/api/knowledge/search", json={"query": "产品与服务", "k": 2})
        ).json()
        _assert(len(limited["results"]) == 2, f"k=2 返回 2 条（实际 {len(limited['results'])}）")
        scores = [item["score"] for item in limited["results"]]
        _assert(scores == sorted(scores, reverse=True), f"结果按相似度降序：{scores}")

        bad_type = await client.post(
            "/api/knowledge/upload", files={"file": ("bad.xyz", b"hello", "text/plain")}
        )
        _assert(bad_type.status_code == 400, f"不支持的类型 400：{bad_type.json()['detail']}")
        empty = await client.post(
            "/api/knowledge/upload", files={"file": ("empty.txt", b"", "text/plain")}
        )
        _assert(empty.status_code == 400, "空文件 400")

        # 4) 普通对话（走默认模型 + 检索工具）
        response = await client.post("/api/chat", json={"message": "DataPilot 支持哪些文档格式？"})
        _assert(response.status_code == 200, "普通对话 HTTP 200")
        chat = response.json()
        print(f"  [chat] {chat['output'][:80].replace(chr(10), ' ')}…")
        _assert(bool(chat["output"]), f"返回最终回答（model={chat['model_name']}）")
        tools_used = [step["tool"] for step in chat["intermediate_steps"]]
        _assert("search_knowledge" in tools_used, f"步骤含 search_knowledge：{tools_used}")

        invalid = await client.post("/api/chat", json={"message": "你好", "model_name": "gpt-99"})
        _assert(invalid.status_code == 400, "非法模型名对话 400（未触发调用）")

        # 5) SSE 流式对话
        events = await _read_sse(
            client, {"message": "知识库中产品退货率是多少？", "session_id": "api-test"}
        )
        kinds = [name for name, _ in events]
        _assert(kinds[0] == "meta", f"首个事件为 meta（序列：{kinds[0]} … {kinds[-1]}）")
        _assert(kinds[-1] == "done", "末个事件为 done")
        meta = events[0][1]
        _assert(meta == {"session_id": "api-test", "model": original}, f"meta 内容：{meta}")
        tokens = "".join(data["content"] for name, data in events if name == "token")
        _assert("4.5" in tokens, "流式正文包含检索到的退货率 4.5%")
        sources = [data["sources"] for name, data in events if name == "sources"]
        _assert(
            bool(sources) and bool(sources[0]),
            f"sources 事件（{len(sources[0]) if sources else 0} 条）",
        )
        tool_names = [data["name"] for name, data in events if name == "tool_start"]
        _assert("search_knowledge" in tool_names, f"工具事件：{tool_names}")
        _assert(isinstance(events[-1][1].get("usage"), dict), f"done 携带 usage：{events[-1][1]}")

        # 6) 删除单个文档（同时清理 uploads 中的落盘文件）
        deleted = await client.delete("/api/knowledge/document", params={"name": fixtures[3].name})
        _assert(deleted.status_code == 200, "删除文档 HTTP 200")
        _assert(
            deleted.json()["deleted_chunks"] > 0, f"删除片段数：{deleted.json()['deleted_chunks']}"
        )
        remaining = (await client.get("/api/knowledge/list")).json()
        _assert(len(remaining["documents"]) == 2, "删除后列表剩 2 个文档")
        _assert(
            not (settings.upload_dir / fixtures[3].name).exists(),
            "uploads 目录中同名文件已清理",
        )
        missing = await client.delete("/api/knowledge/document", params={"name": "不存在.txt"})
        _assert(missing.status_code == 404, f"删除不存在的文档返回 404：{missing.json()['detail']}")

        # 7) 收尾：清空知识库
        await client.delete("/api/knowledge/clear")
        listing = (await client.get("/api/knowledge/list")).json()
        _assert(listing["documents"] == [] and listing["total_chunks"] == 0, "收尾清空知识库")

    print("[OK] 全部验证通过")


if __name__ == "__main__":
    asyncio.run(_main())
