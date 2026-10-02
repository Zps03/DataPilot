"""Agent 组装与类式封装：langchain 1.x `create_agent`（LangGraph 状态图）+ 系统提示词 + 工具。

对外接口（唯一入口约定，api 层勿绕过）：
- `get_agent()`：Agent 组装唯一入口（模型按 model_name 切换 + 工具 + checkpointer）。
- `stream_agent_events()`：事件生产唯一入口（定义于 events.py，包级已再导出）。
- `DataAnalysisAgent`：类式封装（run 一次性返回 / stream 事件流），内部仅委托以上两入口，
  不复制事件过滤逻辑；供 API 阶段按会话构建实例使用。

会话记忆：checkpointer 默认 InMemorySaver，同一 session_id（= thread_id）实现多轮；
持久化需补装 langgraph-checkpoint-sqlite（API 阶段换 AsyncSqliteSaver）。
硬性规则：禁止 AgentExecutor / initialize_agent / create_react_agent（已废弃或移除）。
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from agent.events import stream_agent_events
from config import settings
from llm import get_model
from tools import get_tools

__all__: list[str] = [
    "SYSTEM_PROMPT",
    "DataAnalysisAgent",
    "get_agent",
    "stream_agent_events",
]

SYSTEM_PROMPT = """你是 DataPilot——一个智能数据分析助手，可以调用工具来帮助用户完成复杂任务。

工作流程：
1. 先理解用户意图，判断需要哪些信息。
2. 需要查资料时，先用 search_knowledge 工具检索本地知识库，禁止凭空编造。
3. 需要数学计算时，用 calculator 工具精确计算，不要心算。
4. 需要数据处理、统计分析时，用 python_executor 工具（已预置 pandas / numpy）。
5. 需要当前日期时间时，用 get_current_time 工具。
6. 综合所有工具结果后给出最终回答。

回答要求：
- 默认使用中文回答（用户用其他语言提问时跟随用户语言）。
- 引用知识库内容时标注来源，格式：【文件名 第 N 页】（无页码时只写文件名）。
- 检索无结果或内容不相关时，如实说明「知识库中没有找到相关内容」，不要强行作答。
- 回答结构清晰、数据准确；不要编造知识库中不存在的文档名或数据。"""


def get_agent(
    model_name: str | None = None,
    *,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: Checkpointer = None,
    system_prompt: str = SYSTEM_PROMPT,
) -> CompiledStateGraph:
    """构建 Agent 实例（每次调用新建；对话模型按 model_name 切换，见 llm.get_model）。

    Args:
        model_name: 对话模型名；为空时使用 DEFAULT_MODEL。
        tools: 工具列表；为空时使用 tools.get_tools()（后续阶段注入数据集工具）。
        checkpointer: 会话持久化；同一 session_id（thread_id）实现多轮记忆。
        system_prompt: 系统提示词；默认使用本模块的 SYSTEM_PROMPT。
    """
    return create_agent(
        model=get_model(model_name),
        tools=list(tools) if tools is not None else get_tools(),
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )


_STEP_OUTPUT_MAX_LEN = 500


def _message_text(message: Any) -> str:
    """取消息文本（content 可能是字符串或内容块列表）。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return str(content)


def _extract_steps(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """提取工具步骤 [{tool, input, output}]：按 tool_call_id 配对调用与结果，输出截断。"""
    observations: dict[str, str] = {}
    for message in messages:
        if isinstance(message, ToolMessage):
            observations[message.tool_call_id] = _message_text(message)
    steps: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                output = observations.get(call["id"], "")
                if len(output) > _STEP_OUTPUT_MAX_LEN:
                    output = f"{output[:_STEP_OUTPUT_MAX_LEN]}…"
                steps.append(
                    {"tool": call["name"], "input": call["args"], "output": output}
                )
    return steps


class DataAnalysisAgent:
    """Agent 类式封装：run() 一次性返回，stream() 产出 SSE 契约事件 (event, data) 元组。

    内部委托 get_agent() 组装、stream_agent_events() 产事件（唯一入口约定），
    不复制事件过滤逻辑。run() 语义：
    - 不传 chat_history：走会话记忆（同一 session_id 累积多轮，依赖 checkpointer）；
    - 传 chat_history：一次性无状态调用（临时 thread_id，不读写会话记忆）。
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        session_id: str = "default",
        checkpointer: Checkpointer = None,
    ) -> None:
        """初始化 LLM、工具集与 Agent 实例。

        Args:
            model_name: 对话模型名；为空时使用 DEFAULT_MODEL。
            session_id: 会话 ID（thread_id），同一实例复用实现多轮记忆。
            checkpointer: 会话持久化；为空时使用 InMemorySaver（API 阶段可换 AsyncSqliteSaver）。
        """
        self.model_name = model_name or settings.default_model
        self.session_id = session_id
        self.llm = get_model(self.model_name)
        self.tools: list[BaseTool] = get_tools()
        self.checkpointer: Checkpointer = (
            checkpointer if checkpointer is not None else InMemorySaver()
        )
        self._agent: CompiledStateGraph = get_agent(
            self.model_name, tools=self.tools, checkpointer=self.checkpointer
        )

    async def run(
        self, user_input: str, chat_history: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """执行一轮对话，返回 {"output": 最终回答, "intermediate_steps": 工具步骤列表}。"""
        messages: list[dict[str, Any]] = [
            *(chat_history or []),
            {"role": "user", "content": user_input},
        ]
        if chat_history:
            thread_id = f"{self.session_id}-oneshot-{uuid.uuid4().hex[:8]}"
        else:
            thread_id = self.session_id
        result = await self._agent.ainvoke(
            {"messages": messages}, config={"configurable": {"thread_id": thread_id}}
        )

        all_messages = result["messages"]
        output = ""
        for message in reversed(all_messages):
            if isinstance(message, AIMessage) and not message.tool_calls:
                output = _message_text(message)
                break
        return {"output": output, "intermediate_steps": _extract_steps(all_messages)}

    async def stream(self, user_input: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """流式输出：逐条产出契约事件 (event, data)，api 层直接套 EventSourceResponse。"""
        async for event, data in stream_agent_events(
            self._agent, user_input, session_id=self.session_id, model=self.model_name
        ):
            yield event, data
