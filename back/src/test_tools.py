"""工具模块测试：逐个调用 calculator / python_executor / get_current_time / search_knowledge。

运行：cd back && uv run python src/test_tools.py
前置：search_knowledge 需已配置 DASHSCOPE_API_KEY（back/.env），会先重建测试知识库
（复用 test_rag 的样例文档）；其余三个工具为纯本地，无需 Key。
"""

import asyncio
import sys
from datetime import datetime

from config import settings
from rag import RagService
from test_rag import create_test_docs
from tools import calculator, get_current_time, python_executor, search_knowledge

# Windows 控制台默认 GBK 会乱码，统一强制 UTF-8 输出（PyCharm / Windows Terminal 均适用）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[FAIL] {message}")
    print(f"[OK] {message}")


async def _check_calculator() -> None:
    print("--- calculator ---")
    for expression, expected in [
        ("(1+2)*3", "9"),
        ("2**10", "1024"),
        ("sqrt(16)", "4"),
        ("round(pi, 4)", "3.1416"),
        ("10 % 3", "1"),
    ]:
        result = await calculator.ainvoke({"expression": expression})
        _assert(result == expected, f"calculator({expression!r}) → {result}")

    for expression in ["1/0", "__import__('os')", "2**100000", "1+"]:
        result = await calculator.ainvoke({"expression": expression})
        _assert(
            result.startswith("计算失败"), f"calculator({expression!r}) 拒绝非法输入 → {result}"
        )


async def _check_python_executor() -> None:
    print("--- python_executor ---")
    result = await python_executor.ainvoke({"code": "sum([1, 2, 3]) + 4"})
    _assert(result == "10", f"末行表达式回显 → {result}")

    result = await python_executor.ainvoke({"code": "print('你好')\nlen('DataPilot')"})
    _assert(result == "你好\n9", f"print 输出 + 回显 → {result!r}")

    result = await python_executor.ainvoke({"code": "pd.Series([1, 2, 3]).tolist()"})
    _assert(result == "[1, 2, 3]", f"pandas 预置可用 → {result}")

    result = await python_executor.ainvoke({"code": "np.arange(3).mean().item()"})
    _assert(result == "1.0", f"numpy 预置可用 → {result}")

    result = await python_executor.ainvoke({"code": "import os"})
    _assert(result.startswith("执行失败") and "import" in result, f"import 被拒绝 → {result}")

    result = await python_executor.ainvoke({"code": "().__class__.__base__"})
    _assert(result.startswith("执行失败"), f"双下划线属性被拒绝 → {result}")

    result = await python_executor.ainvoke({"code": "1/0"})
    _assert("ZeroDivisionError" in result, f"运行错误转为文本 → {result}")


async def _check_time() -> None:
    print("--- get_current_time ---")
    result = await get_current_time.ainvoke({})
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    _assert(result.startswith(today) and "星期" in result, f"返回当前日期与星期 → {result}")


async def _check_search() -> None:
    print("--- search_knowledge ---")
    if not settings.dashscope_api_key:
        print("[SKIP] 未配置 DASHSCOPE_API_KEY：跳过检索工具（联网）验证。")
        return

    create_test_docs()
    service = RagService()
    service.clear()
    count = service.add_directory()
    print(f"  [KB] 测试知识库就绪：{count} 个片段")

    # 传 tool_call_id 时输出为 ToolMessage（含 artifact）——与 ToolNode 调用路径一致
    message = await search_knowledge.arun(
        {"query": "DataPilot 支持哪些文档格式？"}, tool_call_id="test-call"
    )
    _assert("【片段 1】来源：" in message.content, "content 为带来源标注的拼接文本")
    _assert(
        "02-常见问题.md" in message.content or "01-产品介绍.txt" in message.content,
        "content 含检索到的文档名",
    )
    sources = message.artifact.get("sources") if isinstance(message.artifact, dict) else None
    _assert(bool(sources), f"artifact 携带结构化 sources（{len(sources or [])} 条）")
    first = (sources or [{}])[0]
    _assert(
        set(first) == {"doc", "page", "snippet"},
        f"sources 字段与 SSE 契约一致：{sorted(first)}",
    )


async def _main() -> None:
    await _check_calculator()
    await _check_python_executor()
    await _check_time()
    await _check_search()
    print("\n[OK] 全部验证通过")


if __name__ == "__main__":
    asyncio.run(_main())
