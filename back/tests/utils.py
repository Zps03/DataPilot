"""pytest 用例公共工具：SSE 解析、文件上传、数字归一化（口径与旧脚本一致）。"""

import json
import re
from pathlib import Path
from typing import Any

import httpx

__all__: list[str] = ["normalize_number", "read_sse", "upload_file"]


async def upload_file(client: httpx.AsyncClient, path: Path) -> httpx.Response:
    """以 multipart/form-data 上传文档，返回原始响应（状态码断言交给用例）。"""
    with path.open("rb") as handle:
        return await client.post(
            "/api/knowledge/upload",
            files={"file": (path.name, handle, "application/octet-stream")},
        )


async def read_sse(
    client: httpx.AsyncClient, payload: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """POST /api/chat/stream 并按 SSE 协议解析为 (event, data) 列表。

    只解析 event: / data: 行（忽略 ": ping" 注释行）；服务端 data 为单行 JSON
    （json.dumps(ensure_ascii=False)），据此按行解析即可。
    """
    events: list[tuple[str, dict[str, Any]]] = []
    current = ""
    async with client.stream("POST", "/api/chat/stream", json=payload) as response:
        assert response.status_code == 200, f"SSE 状态码 {response.status_code}"
        content_type = response.headers["content-type"]
        assert content_type.startswith("text/event-stream"), f"Content-Type：{content_type}"
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                events.append((current, json.loads(line.removeprefix("data:").strip())))
    return events


def normalize_number(text: str) -> str:
    """去掉千分位逗号与空白，便于数字断言（如「24,000 元」→「24000元」）。"""
    return re.sub(r"[,\s]", "", text)
