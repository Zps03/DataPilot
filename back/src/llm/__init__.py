"""LLM 工厂：ChatOpenAI / OpenAIEmbeddings（langchain-openai）→ 阿里云百炼兼容接口。

模型实例的唯一入口：`get_model`（对话）、`get_embeddings`（向量化）。
业务代码禁止直接实例化 ChatOpenAI / OpenAIEmbeddings，否则无法按会话切换模型。
"""

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config import settings

__all__: list[str] = ["get_embeddings", "get_model"]


def get_model(model_name: str | None = None) -> ChatOpenAI:
    """按模型名获取 ChatOpenAI 实例（百炼 OpenAI 兼容接口）。

    Args:
        model_name: 模型名称；为空时使用配置中的 DEFAULT_MODEL。

    Raises:
        ValueError: 模型名不在 AVAILABLE_MODELS 列表中。
    """
    name = model_name or settings.default_model
    supported = settings.model_list
    if name not in supported:
        raise ValueError(f"不支持的模型：{name}，支持的模型：{', '.join(supported)}")

    # qwen3 系列思考模式与工具调用 / 非流式调用冲突，统一关闭（见 CLAUDE.md 已知坑）
    extra_body = {"enable_thinking": False} if name.startswith("qwen3") else None

    return ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=name,
        temperature=0.1,
        streaming=True,
        extra_body=extra_body,
    )


def get_embeddings(model_name: str | None = None) -> OpenAIEmbeddings:
    """按模型名获取 OpenAIEmbeddings 实例（百炼 OpenAI 兼容接口）。

    注意：未配置 DASHSCOPE_API_KEY 时构造即抛错，调用方应按需惰性创建
    （RagService 的向量库连接即为此设计）。

    Args:
        model_name: 向量模型名称；为空时使用配置中的 EMBEDDING_MODEL。
    """
    return OpenAIEmbeddings(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=model_name or settings.embedding_model,
        # 百炼兼容接口三个必要参数（见 CLAUDE.md「已知坑」）：
        check_embedding_ctx_length=False,  # 直接发送原文本，跳过 tiktoken 分词
        chunk_size=10,  # 兼容接口单次请求最多 10 条文本
        model_kwargs={"encoding_format": "float"},  # 避免返回 base64 导致解析失败
    )
