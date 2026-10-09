"""Agent 工具集：知识库检索 / 计算器 / Python 代码执行 / 当前时间。

工具返回约定：
- search_knowledge 使用 `response_format="content_and_artifact"`：content 为拼接后的检索
  片段文本（供模型阅读作答），artifact 为 {"sources": [{"doc", "page", "snippet"}], "count"}；
  agent/events.py 读取 artifact 产出 SSE `sources` 事件（前后端契约见 CLAUDE.md §6.2）。
  直接调用时 `ainvoke` 返回 (content, artifact) 元组，而非字符串。
- 其余工具直接返回结果文本；失败时返回以「计算失败」/「执行失败」开头的说明文本。
- 检索与代码执行为异步工具（同步 IO 用 asyncio.to_thread 包装）；纯计算的 calculator /
  get_current_time 保持同步（LangGraph 在异步图里经工作线程调用同步工具）。

安全边界：python_executor 是「受限命名空间 + 名称级封堵」，**不是安全沙箱**：
- builtins 白名单（无 open / eval / exec / compile / getattr / format，`__import__` 换成
  只放行 numpy / pandas 的 _guarded_import）
- AST 层拒绝 import、双下划线名称与属性（阻断 __class__ / __subclasses__ 一类逃逸）
- 按名字拒绝 pandas / numpy 的文件读写与反序列化入口（见 _FORBIDDEN_ATTRS）
- 15 秒执行上限 + 独立守护线程执行器（不占用 asyncio 默认池，超时不阻塞进程退出）

封堵是黑名单式的，理论上仍存在未覆盖的 IO 路径，仅用于执行模型生成的分析代码，
不得作为安全机制依赖。**超时也是 best-effort**：纯 Python 死循环（求值循环会周期性
释放 GIL）能准时中止，但单次持有 GIL 的 C 级运算（如 `9**20000000` 这类巨型大整数
乘幂）会连事件循环一起冻住，定时器回调无法执行，超时不会生效——此时整个服务会卡住
该运算的时长。需要彻底隔离应改为子进程执行（可强制 kill + 资源限额）。
"""

import ast
import asyncio
import builtins
import datetime
import io
import math
import operator
import queue
import threading
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

# builtins 白名单：不要加入 open / eval / exec / compile / getattr / format 等
# （format 不经 AST 检查即可读出任意对象双下划线属性的文本表示，见 _FORBIDDEN_ATTRS；
#   f-string 的数值格式化走 FORMAT_VALUE 字节码，不受此白名单影响，仍可用）
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

# 执行上限（best-effort，见 python_executor）与专用执行器
_EXEC_TIMEOUT_SECONDS = 15.0
_EXEC_MAX_WORKERS = 2


def _deliver(
    loop: asyncio.AbstractEventLoop,
    future: asyncio.Future[str],
    value: str | BaseException,
    *,
    is_exception: bool = False,
) -> None:
    """把工作线程的结果投递回事件循环；循环已关闭（进程退出中）时静默丢弃。"""
    setter = _set_exception_if_pending if is_exception else _set_result_if_pending
    try:
        loop.call_soon_threadsafe(setter, future, value)
    except RuntimeError:
        pass  # 事件循环已关闭，结果无处投递


def _set_result_if_pending(future: asyncio.Future[str], value: str) -> None:
    if not future.done():
        future.set_result(value)


def _set_exception_if_pending(future: asyncio.Future[str], exc: BaseException) -> None:
    if not future.done():
        future.set_exception(exc)


class _DaemonExecutor:
    """固定守护线程 + 无界队列的执行器（替代 ThreadPoolExecutor）。

    为什么不用 ThreadPoolExecutor：它的工作线程是**非守护线程**，且 concurrent.futures
    注册了 atexit join——一旦有计算超时后仍在跑（线程无法强杀，见 python_executor），
    进程将**永远无法退出**（Ctrl+C / uvicorn 关闭时卡死，已实测复现）。
    守护线程随解释器退出被强制回收，代价是超时任务不会跑完，换取进程可正常关闭。
    """

    def __init__(self, max_workers: int, thread_name_prefix: str) -> None:
        self._max_workers = max_workers
        self._prefix = thread_name_prefix
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                job()
            finally:
                self._queue.task_done()

    def _ensure_workers(self) -> None:
        with self._lock:
            alive = sum(1 for thread in self._threads if thread.is_alive())
            for index in range(alive, self._max_workers):
                thread = threading.Thread(
                    target=self._worker, name=f"{self._prefix}-{index}", daemon=True
                )
                thread.start()
                self._threads.append(thread)

    def run(self, func: Callable[..., str], *args: Any) -> asyncio.Future[str]:
        """在守护线程中执行 func(*args)，返回可 await 的 Future（不阻塞事件循环）。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._ensure_workers()

        def job() -> None:
            if future.cancelled():  # 已超时放弃等待，无需再算
                return
            try:
                result = func(*args)
            except BaseException as exc:  # noqa: BLE001 — 原样转交给等待方判定
                _deliver(loop, future, exc, is_exception=True)
            else:
                _deliver(loop, future, result)

        self._queue.put(job)
        return future


# 独立于 asyncio 默认池（Chroma / 文档解析 / 文件写入共用）：卡死时只影响
# python_executor 自身，不会拖垮其他接口
_EXEC_POOL = _DaemonExecutor(_EXEC_MAX_WORKERS, "py-exec")


class RestrictedCodeError(RuntimeError):
    """代码包含受限语法（import / 双下划线名称或属性 / 文件与反序列化入口）。"""


# 按属性名封堵的 IO 与反序列化入口（不限 base 对象）。
# 受限命名空间收不回 pandas / numpy 自带的文件与网络能力，而它们的入口属性名都不含
# 双下划线、可被 AST 检查直接看到，故按名字拒绝。pandas / numpy 的磁盘 IO 与
# pickle 反序列化（pd.read_pickle / np.load(allow_pickle=True)）可读取宿主任意文件
# （含 back/.env 中的 API Key）乃至执行任意代码，必须堵死。
_FORBIDDEN_ATTRS = frozenset(
    {
        # pandas 读
        "read_csv",
        "read_table",
        "read_fwf",
        "read_excel",
        "read_json",
        "read_html",
        "read_xml",
        "read_parquet",
        "read_feather",
        "read_orc",
        "read_hdf",
        "read_sas",
        "read_spss",
        "read_stata",
        "read_clipboard",
        "read_gbq",
        "read_sql",
        "read_sql_query",
        "read_sql_table",
        "read_pickle",
        # pandas 写
        "to_csv",
        "to_excel",
        "to_json",
        "to_html",
        "to_xml",
        "to_latex",
        "to_markdown",
        "to_parquet",
        "to_feather",
        "to_orc",
        "to_hdf",
        "to_stata",
        "to_clipboard",
        "to_gbq",
        "to_sql",
        "to_pickle",
        # numpy IO / 反序列化
        "load",
        "loadtxt",
        "genfromtxt",
        "fromfile",
        "memmap",
        "open_memmap",
        "save",
        "savez",
        "savez_compressed",
        "savetxt",
        "tofile",
        # 反射入口：str.format 会渲染出任意对象双下划线属性的文本表示，
        # 绕过上面的双下划线属性检查（f-string 不受影响）
        "format",
    }
)

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
    """拒绝 import、双下划线名称/属性，以及 IO / 反序列化 / 反射入口（_FORBIDDEN_ATTRS）。

    属性检查不限 base 对象：pandas 与 numpy 的文件、网络与反序列化入口（read_* / to_* /
    load / save 等）均按名字封堵；用户代码也触碰不到真正的文件对象。
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise RestrictedCodeError("不允许 import；pandas（pd）与 numpy（np）已预置")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise RestrictedCodeError(f"不允许使用名称：{node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise RestrictedCodeError(f"不允许访问属性：{node.attr}")
            if node.attr in _FORBIDDEN_ATTRS:
                raise RestrictedCodeError(
                    f"不允许使用：{node.attr}（禁止文件读写、网络请求、反序列化与反射）"
                )


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

    已预置 pandas（pd）与 numpy（np），不能 import；文件读写、网络请求与反序列化入口
    （read_* / to_* / load / save 等属性）已被拒绝，请只用内存中的数据结构完成分析。
    结果用 print 输出，或作为最后一行表达式（会回显取值）；例如：
    pd.Series([1, 2, 3]).mean().item()
    代码有 15 秒执行上限（对死循环有效），请避免超大计算（如 9**20000000 这类巨型大整数）。
    """
    try:
        # 专用守护线程执行：不占用 asyncio 默认池（Chroma / 文档解析 / 文件写入共用），
        # 卡死时只影响 python_executor 自身，不会拖垮其他接口
        return await asyncio.wait_for(
            _EXEC_POOL.run(_run_exec, code), timeout=_EXEC_TIMEOUT_SECONDS
        )
    except TimeoutError:
        # 线程无法强杀：超时的计算仍会在守护线程中继续跑，直到进程退出被回收。
        # 注意超时是 best-effort——持有 GIL 的单次 C 级运算（如巨型大整数乘幂）会冻住
        # 事件循环，定时器回调无法执行，此时超时不会生效（详见模块 docstring）。
        return (
            f"执行失败：代码运行超过 {_EXEC_TIMEOUT_SECONDS:.0f} 秒已中止"
            "（可能是死循环或超大计算），请缩小计算规模后重试。"
        )
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
