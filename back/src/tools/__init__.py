"""Agent 工具集：知识库检索 / 计算器 / Python 代码执行 / 当前时间。

工具返回约定：
- search_knowledge 使用 `response_format="content_and_artifact"`：content 为拼接后的检索
  片段文本（供模型阅读作答），artifact 为 {"sources": [{"doc", "page", "snippet"}], "count"}；
  agent/events.py 读取 artifact 产出 SSE `sources` 事件（前后端契约见 CLAUDE.md §6.2）。
  直接调用时 `ainvoke` 返回 (content, artifact) 元组，而非字符串。
- 其余工具直接返回结果文本；失败时返回以「计算失败」/「执行失败」开头的说明文本。
- 检索与代码执行为异步工具（同步 IO 用 asyncio.to_thread 包装）；纯计算的 calculator /
  get_current_time 保持同步（LangGraph 在异步图里经工作线程调用同步工具）。

安全边界：python_executor 只是受限命名空间（builtins 白名单 + 禁 import / 双下划线名称
与属性），不是安全沙箱、无超时控制，仅用于执行模型生成的分析代码，不得作为安全机制依赖。
"""

import ast
import asyncio
import builtins
import datetime
import io
import math
import operator
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from langchain_core.documents import Document
from langchain_core.tools import BaseTool, tool

from config import settings
from rag import get_rag_service  # 与 API 路由共用同一 RagService 单例

__all__: list[str] = [
    "SEARCH_TOOL_NAME",
    "calculator",
    "get_current_time",
    "get_rag_service",
    "get_tools",
    "python_executor",
    "search_knowledge",
]

# 工具名常量：SSE 适配层（agent/events.py）按此名称识别 sources 来源，改名需同步
SEARCH_TOOL_NAME = "search_knowledge"


# ------------------------------------------------------------------ 知识库检索

def _to_source(document: Document, max_len: int = 200) -> dict[str, str | int | None]:
    """检索片段 → sources 条目（snippet 为折叠空白后的摘要，供前端来源卡片展示）。"""
    snippet = " ".join(document.page_content[:max_len].split())
    return {
        "doc": Path(document.metadata["source"]).name,
        "page": document.metadata.get("page"),
        "snippet": snippet,
    }


def _format_context(document: Document, rank: int) -> str:
    """检索片段 → 供模型阅读的文本块（含来源标注，格式与引用要求一致）。"""
    name = Path(document.metadata["source"]).name
    page = document.metadata.get("page")
    label = f"{name} 第 {page} 页" if page else name
    return f"【片段 {rank}】来源：{label}\n{document.page_content}"


@tool(response_format="content_and_artifact")
async def search_knowledge(query: str, k: int = settings.top_k) -> tuple[str, dict[str, Any]]:
    """当需要从私有知识库中查找信息时使用，输入查询问题。

    返回按相关度排序的文档片段，每段带「文件名 第 N 页」来源标注；
    请基于返回内容作答，并按同样格式标注引用来源。
    """
    results = await asyncio.to_thread(get_rag_service().search, query, k)
    if not results:
        return "知识库中没有检索到与该问题相关的片段。", {"sources": [], "count": 0}
    context = "\n\n".join(
        _format_context(document, rank) for rank, document in enumerate(results, start=1)
    )
    sources = [_to_source(document) for document in results]
    return context, {"sources": sources, "count": len(sources)}


# ---------------------------------------------------------------------- 计算器

# 计算器白名单：仅允许以下常量与函数参与求值
_CALC_NAMESPACE: dict[str, float | Callable[..., float]] = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "floor": math.floor,
    "ceil": math.ceil,
    "pi": math.pi,
    "e": math.e,
}

_CALC_BINOP_FUNCS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CALC_UNARYOPS = (ast.UAdd, ast.USub)
_MAX_POW_EXPONENT = 1000


def _eval_calc_node(node: ast.AST) -> float | int:
    """白名单递归求值：数字、+ - * / // % **、括号与白名单函数，其余语法一律拒绝。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("仅支持数字常量")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _CALC_UNARYOPS):
        value = _eval_calc_node(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and type(node.op) in _CALC_BINOP_FUNCS:
        left = _eval_calc_node(node.left)
        right = _eval_calc_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXPONENT:
            raise ValueError(f"指数不能超过 {_MAX_POW_EXPONENT}（防止卡死）")
        return _CALC_BINOP_FUNCS[type(node.op)](left, right)
    if isinstance(node, ast.Name):
        value = _CALC_NAMESPACE.get(node.id)
        if isinstance(value, (int, float)):
            return value
        raise ValueError(f"未知名称：{node.id}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        func = _CALC_NAMESPACE.get(node.func.id)
        if not callable(func):
            raise ValueError(f"未知函数：{node.func.id}")
        return func(*(_eval_calc_node(arg) for arg in node.args))
    raise ValueError("表达式中含不支持的语法")


@tool
def calculator(expression: str) -> str:
    """当需要进行数学计算时使用，输入数学表达式字符串。

    支持 + - * / // % **、括号，以及 sqrt/log/exp/sin/cos/tan/floor/ceil 等函数和 pi、e 常量。
    示例："(1+2)*3"、"sqrt(16)"、"2**10"。
    """
    try:
        result = _eval_calc_node(ast.parse(expression, mode="eval").body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return str(result)
    except (SyntaxError, ValueError, TypeError, ArithmeticError) as exc:
        return f"计算失败：{exc}"


# --------------------------------------------------------------- Python 代码执行

# builtins 白名单：不要加入 open / eval / exec / compile / getattr 等
# （__import__ 不在列表中，由 _run_exec 注入受限版本 _guarded_import）
_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "pow": pow,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}

_MAX_EXEC_RESULT_LEN = 3000


class RestrictedCodeError(RuntimeError):
    """代码包含受限语法（import / 双下划线名称或属性）。"""


# 仅放行 numpy / pandas 及其依赖的内部懒加载；用户代码触碰不到该函数（见 _guarded_import）
_IMPORT_ALLOWED_ROOTS = frozenset({"numpy", "pandas"})
_REAL_IMPORT = builtins.__import__


def _guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
    """受限 `__import__`：仅放行 numpy / pandas 子模块。

    numpy 2.x 等 C 扩展会在**调用帧**内通过帧 builtins 查找 `__import__` 做懒加载
    （CPython PyImport_ImportModule 的行为），缺失即报 `KeyError: '__import__'`。
    用户代码的 import 语句与对 `__import__` 的引用已在 AST 层被 _validate_exec_code
    直接拒绝，本函数只约束受限帧内 C 扩展的懒加载目标。
    """
    if name.split(".")[0] not in _IMPORT_ALLOWED_ROOTS:
        raise RestrictedCodeError(f"不允许 import：{name}")
    return _REAL_IMPORT(name, *args, **kwargs)


def _validate_exec_code(tree: ast.AST) -> None:
    """拒绝 import 与双下划线名称/属性（阻断 __class__、__subclasses__ 一类逃逸写法）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise RestrictedCodeError("不允许 import；pandas（pd）与 numpy（np）已预置")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise RestrictedCodeError(f"不允许使用名称：{node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise RestrictedCodeError(f"不允许访问属性：{node.attr}")


def _make_print(buffer: io.StringIO) -> Callable[..., None]:
    """构建写入内存缓冲的 print；不重定向全局 sys.stdout，避免影响其他线程。"""

    def _print(*args: Any, sep: str = " ", end: str = "\n") -> None:
        buffer.write(sep.join(str(arg) for arg in args) + end)

    return _print


def _run_exec(code: str) -> str:
    """在受限命名空间执行代码：捕获 print 输出，末行表达式回显其取值。"""
    tree = ast.parse(code, mode="exec")
    _validate_exec_code(tree)

    buffer = io.StringIO()
    safe_builtins = dict(_SAFE_BUILTINS)
    safe_builtins["print"] = _make_print(buffer)
    safe_builtins["__import__"] = _guarded_import
    namespace: dict[str, Any] = {"__builtins__": safe_builtins, "pd": pd, "np": np}

    body = list(tree.body)
    tail = body.pop() if body and isinstance(body[-1], ast.Expr) else None
    if body:
        module = ast.Module(body=body, type_ignores=[])
        # 受限命名空间执行（白名单 builtins），安全边界见模块 docstring
        exec(compile(module, "<python_executor>", "exec"), namespace)  # noqa: S102
    parts = [buffer.getvalue().strip()]
    if tail is not None:
        value = eval(compile(ast.Expression(tail.value), "<python_executor>", "eval"), namespace)
        parts.append(repr(value))

    output = "\n".join(part for part in parts if part)
    if not output:
        output = "（执行完成，无输出）"
    if len(output) > _MAX_EXEC_RESULT_LEN:
        output = f"{output[:_MAX_EXEC_RESULT_LEN]}…（输出过长已截断）"
    return output


@tool
async def python_executor(code: str) -> str:
    """当需要进行数据处理、分析、可视化时使用，输入 Python 代码。

    已预置 pandas（pd）与 numpy（np），不能 import，不能读写文件与网络。
    结果用 print 输出，或作为最后一行表达式（会回显取值）；例如：
    pd.Series([1, 2, 3]).mean().item()
    """
    try:
        return await asyncio.to_thread(_run_exec, code)
    except Exception as exc:  # noqa: BLE001 — 模型生成代码的错误类型不可枚举，统一转为提示文本
        return f"执行失败：{type(exc).__name__}: {exc}"


# -------------------------------------------------------------------- 当前时间

_WEEKDAY_NAMES = "一二三四五六日"


@tool
def get_current_time() -> str:
    """当需要获取当前日期时间时使用。"""
    now = datetime.datetime.now().astimezone()
    return f"{now:%Y-%m-%d %H:%M:%S} 星期{_WEEKDAY_NAMES[now.weekday()]}"


def get_tools() -> list[BaseTool]:
    """当前可用工具列表（供 create_agent 使用）。"""
    return [search_knowledge, calculator, python_executor, get_current_time]
