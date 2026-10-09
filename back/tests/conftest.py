"""pytest 全局配置：自动拉起被测后端、公共 fixture 与联网用例跳过规则。

- base_url（会话级、同步）：动态选空闲端口，以 uvicorn 子进程拉起
  `python -m uvicorn main:app --app-dir src`，轮询 /api/health 就绪后交付；会话结束终止。
  后端已在运行时，可用环境变量 DATAPILOT_BASE_URL 指向该实例（跳过自动拉起）。
- client：指向 base_url 的异步 httpx 客户端（超时 180s，与旧脚本口径一致）。
- seeded_kb（会话级、同步）：清空知识库 → 上传 3 个测试文档（TXT / Markdown / 经营数据）
  → 返回 {文件名: 片段数}，会话结束清空。用同步 httpx 实现，避免会话级异步 fixture
  与函数级事件循环的作用域冲突。
- 未配置 DASHSCOPE_API_KEY 时（settings.dashscope_api_key 为空，如 CI 未配 Secret），
  online 标记的用例自动跳过；离线用例不依赖 Key，可独立通过。
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

from config import settings
from test_rag import create_test_docs

BACK_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BACK_DIR / "src"
_STARTUP_TIMEOUT_SECONDS = 60.0


def _free_port() -> int:
    """向系统申请一个空闲端口（绑 0 端口探测后立刻释放）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(base_url: str, process: subprocess.Popen[bytes]) -> None:
    """轮询 /api/health 直到后端就绪；子进程提前退出或超时则报错。"""
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            message = f"后端启动失败（exit={process.returncode}）；可手动启动查看日志"
            raise RuntimeError(message)
        try:
            if httpx.get(f"{base_url}/api/health", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"后端启动超时（{_STARTUP_TIMEOUT_SECONDS:.0f}s）：{base_url}")


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    """被测后端地址：默认自动拉起 uvicorn 子进程；DATAPILOT_BASE_URL 指向已有实例。"""
    external = os.getenv("DATAPILOT_BASE_URL")
    if external:
        yield external.rstrip("/")
        return
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--app-dir",
        str(SRC_DIR),
        "--port",
        str(port),
    ]
    process = subprocess.Popen(
        command,
        cwd=BACK_DIR,
        stdout=subprocess.DEVNULL,  # 丢弃 uvicorn 访问日志；启动失败由轮询逻辑报错
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_until_ready(url, process)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture
async def client(base_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """异步 HTTP 客户端（超时 180s；SSE 长流由服务端 15s 心跳保活）。"""
    async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as http_client:
        yield http_client


@pytest.fixture(scope="session")
def seeded_kb(base_url: str) -> Iterator[dict[str, int]]:
    """种子知识库：清空 → 上传 3 个测试文档 → yield {文件名: 片段数} → 会话末清空。"""
    txt_path, md_path, _pdf_path, data_path = create_test_docs()
    uploaded: dict[str, int] = {}
    with httpx.Client(base_url=base_url, timeout=120.0) as http_client:
        cleared = http_client.delete("/api/knowledge/clear")
        assert cleared.status_code == 200, f"前置清空失败：{cleared.text}"
        listing = http_client.get("/api/knowledge/list").json()
        assert listing["documents"] == [], "清空后列表应为空"
        for path in (txt_path, md_path, data_path):
            with path.open("rb") as handle:
                response = http_client.post(
                    "/api/knowledge/upload",
                    files={"file": (path.name, handle, "application/octet-stream")},
                )
            assert response.status_code == 200, f"种子上传失败：{path.name} → {response.text}"
            chunks = response.json()["chunks"]
            assert chunks > 0, f"{path.name} 入库片段数应为正"
            uploaded[path.name] = chunks
        yield uploaded
        http_client.delete("/api/knowledge/clear")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """未配置 DASHSCOPE_API_KEY 时自动跳过 online 标记用例（CI 无 Secret 场景）。"""
    if settings.dashscope_api_key:
        return
    skip_online = pytest.mark.skip(reason="未配置 DASHSCOPE_API_KEY，跳过联网用例")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)
