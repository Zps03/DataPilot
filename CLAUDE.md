# CLAUDE.md

> DataPilot 项目的 Claude Code 上下文基础文件：后续所有会话开工前，先读「重要约束」与「代码规范」。
> 当前状态：llm 工厂、RAG（含检索测试接口，cosine 度量）、工具集、Agent 核心与 API 路由层（对话 / 知识库 / 模型 / 健康检查）均已实现并联网验证通过；pytest 测试套件（back/tests/，17 条用例 = 离线 9 + 联网 8，conftest 自动拉起后端）全绿，GitHub Actions CI（backend-ci：ruff + pytest）已接入；前端脚手架、对话页（ChatView：SSE 流式 / 推理面板 / 会话管理）与知识库页（KnowledgeView：拖拽上传 / 文档列表 / 检索测试 / 删除清空，Edge headless 浏览器全流程验证通过）已实现；前后端联调通过（pytest 端到端六项链路 + 浏览器场景：流式回复 / 上传后检索 / 模型切换）；设置页为空壳。

## 1. 项目概述

DataPilot 是基于 LangChain Agent + RAG 的智能数据分析助手（单机单用户，无登录、无多租户）。核心功能：

- **知识库问答**：上传 PDF / Word / TXT / Markdown → 切分入库（Chroma）→ Agent 调检索工具回答，给出引用来源
- **数据分析**：上传 CSV / Excel → Agent 调用 pandas 工具做统计、筛选、聚合，返回 ECharts 图表 JSON 由前端渲染
- **流式对话**：SSE 推送 token / 工具调用 / 引用来源 / 图表事件，前端实时渲染（Markdown + 代码高亮）
- **管理界面**：知识库与数据集管理、模型与参数设置

## 2. 技术栈清单

| 层 | 选型 |
|---|---|
| 后端 | Python 3.13、FastAPI、uvicorn、uv 管理；`back/src/` 为**源代码根**（模块扁平、无包前缀） |
| Agent | langchain 1.x `create_agent`（LangGraph 状态图）+ AsyncSqliteSaver 会话持久化 |
| 大模型 | `langchain-openai` 的 `ChatOpenAI` → 阿里云百炼 OpenAI 兼容接口；模型：`qwen3.7-plus`（默认）/ `deepseek-v3` / `glm-5` |
| Embedding | `text-embedding-v3`（百炼兼容接口，v3/v4 向量空间不兼容） |
| 向量库 | `chromadb` + `langchain-chroma`（本地持久化） |
| 文档解析 | `pypdfium2` + `pymupdf`（PDF）；`python-docx`（Word，待补装）、`pandas`（CSV/Excel，待补装） |
| 文本分割 | `langchain-text-splitters` |
| 前端 | Vue 3 + TypeScript + Vite 8 + Element Plus + Pinia + vue-router + ECharts + marked / highlight.js |
| 流式 | SSE（服务端 sse-starlette；客户端 @microsoft/fetch-event-source，EventSource 不支持 POST） |
| 包管理 | 后端 uv（阿里云 PyPI 镜像）；前端 npm |

## 3. 目录结构说明

```
DataPilot/
├── CLAUDE.md                      # 本文件（会话上下文基础）
├── .gitignore
├── .github/workflows/backend-ci.yml  # GitHub Actions CI：ruff + pytest（联网用例无 Secret 自动跳过）
├── back/                          # 后端（uv 项目根，解释器为 back/.venv）
│   ├── pyproject.toml  uv.lock  .python-version
│   ├── .env.example               # 环境变量模板（.env 不入库，需复制后填 Key）
│   ├── test_api.http              # PyCharm HTTP Client 调试脚本
│   ├── knowledge_docs/            # 批量导入的默认文档目录（git 忽略）
│   ├── chroma_db/                 # Chroma 向量库持久化目录（git 忽略）
│   ├── data/                      # 运行时数据（git 忽略，lifespan 启动自动创建）
│   ├── src/                       # 源代码根（Sources Root；非包、无 __init__.py）
│       ├── main.py                # FastAPI 入口：lifespan / CORS / 路由注册 / 全局异常处理
│       ├── config.py              # pydantic-settings 统一配置（唯一配置入口；SRC_DIR=back/src，BASE_DIR=back/）
│       ├── test_llm.py            # LLM 连通性测试脚本（需 .env 中的 DASHSCOPE_API_KEY）
│       ├── test_rag.py            # RAG 测试脚本（解析/切分离线验证 + 入库/检索联网验证）
│       ├── test_agent.py          # Agent 测试脚本（多步推理事件流 + run 契约 + 多轮记忆）
│       ├── test_tools.py          # 工具测试脚本（计算器 / 代码执行 / 时间 / 知识库检索）
│       ├── llm/                   # 模型工厂：get_model / get_embeddings（已实现）
│       ├── rag/                   # RAG：pypdfium2 解析 → 切分 → Chroma 入库与检索（RagService，已实现）
│       ├── agent/                 # create_agent 组装 + DataAnalysisAgent 类封装 + 系统提示词；events.py 事件流适配（均已实现）
│       ├── tools/                 # Agent 工具：检索 / 计算器 / Python 执行 / 时间（已实现）；图表工具待补
│       ├── api/                   # 路由层（已实现）：对话 / 知识库 / 模型 / 健康检查；汇总为 api.router
│       └── models/                # pydantic 请求响应模型（XxxRequest / XxxResponse，已实现）
│   └── tests/                     # pytest 测试套件（conftest 自动拉起 uvicorn；联网用例无 Key 自动跳过）
│       ├── conftest.py            # base_url（自动起后端 / DATAPILOT_BASE_URL 覆盖）/ client / seeded_kb fixture + online 跳过规则
│       ├── utils.py               # 公共工具：SSE 解析 / 文件上传 / 数字归一化
│       ├── test_api.py            # 接口用例：离线（健康 / Swagger / 模型 / 上传校验）+ 联网（知识库生命周期 / 对话 / SSE 契约）
│       └── test_e2e.py            # 端到端六项链路：健康 / 模型 / 对话 / 上传 / RAG 问答 / 多步推理
└── front/                         # 前端（Vue 3 + TypeScript + Vite）
    ├── package.json  package-lock.json  tsconfig.json  index.html  vite.config.ts
    └── src/
        ├── main.ts  App.vue  vite-env.d.ts
        ├── router/index.ts        # /（对话）、/knowledge（知识库）、/settings（设置）
        ├── api/request.ts         # axios 实例（baseURL /api + 拦截器）
        ├── api/chat.ts            # SSE 客户端（fetch-event-source；事件契约解析集中于此）
        ├── api/knowledge.ts       # 知识库接口封装（上传进度 / 列表 / 检索 / 删除 / 清空）
        ├── api/models.ts          # GET /api/models 封装
        ├── stores/chat.ts         # pinia：会话 / 消息 / 流式状态（会话由前端自管）
        ├── utils/markdown.ts      # marked + highlight.js 渲染（原始 HTML 转义）
        ├── components/            # SessionList / MessageItem / ReasoningPanel / ChatInput
        └── views/                 # ChatView / KnowledgeView（已实现）；SettingsView（空壳）
```

注意：`back/src/` 下是扁平模块（`main.py`、`config.py` 与各包平级；src 为源代码根、不是包，不要加 `__init__.py`），导入仍直接写 `from config import settings`、`from llm import get_model`；运行时数据与 `.env` 留在 `back/` 根（`config.BASE_DIR`）。新增顶层模块时避免与第三方包重名。

## 4. 常用命令

```bash
# ===== 后端 =====
cd back
cp .env.example .env                          # 首次：填入 DASHSCOPE_API_KEY
uv sync
uv run uvicorn main:app --app-dir src --reload --port 8000  # 后端启动（http://127.0.0.1:8000）
uv run ruff check .                           # lint（也可 uv run ruff format .）
uv tree | grep -i community                   # 必须为空（langchain-community 检查）
uv run python src/test_llm.py                 # LLM 连通性测试（需先配置 .env 并填 Key）
uv run python src/test_rag.py                 # RAG 测试（解析/切分离线验证；入库/检索需 Key）
uv run python src/test_agent.py               # Agent 测试（多步推理 + run 契约 + 多轮记忆，需 Key）
uv run python src/test_tools.py               # 工具测试（仅检索需 Key，其余离线）
uv run pytest                                 # pytest 测试套件（自动拉起后端；联网用例需 Key，未配置自动跳过）
uv run pytest -m "not online"                 # 仅离线用例（无需 Key；等价 CI 无 Secret 场景）
# 后端已在运行时可指向已有实例：DATAPILOT_BASE_URL=http://127.0.0.1:8000 uv run pytest

# ===== 前端 =====
cd front
npm install
npm run dev         # 前端启动（http://localhost:5173，/api 已代理到 8000）
npm run type-check  # vue-tsc 类型检查
npm run build       # 生产构建
```

PyCharm 提示：解释器指向 `back/.venv`；后端运行配置 module 填 `main:app`、Working directory 填 `back/src`（等价于 `--app-dir src`）、端口 8000。`back/src/` 已标记为 **Sources Root**（`.idea/DataPilot.iml` 的 sourceFolder）——若 IDE 里 `from tools import ...` 等顶层模块报「无法解析」而命令行正常，是源根配置问题而非代码问题。

## 5. 代码规范

### Python（PEP8 + 类型注解）

- 遵循 PEP8，行宽 100（已配 ruff：`uv run ruff check .` / `uv run ruff format .`）
- 所有函数带完整类型注解；ruff 检查必须零告警
- 分层：`api/` 只做参数校验与响应组装，业务逻辑在 `rag/`、`agent/`、`tools/`；`models/` 只放 pydantic schema
- 每个包的 `__init__.py` 必须声明 `__all__`（公开接口清单）；跨包引用只使用 `__all__` 中的名字
- 全异步：路由与 IO 用 async；同步库（pypdfium2 / pymupdf / pandas / sqlite3）用 `asyncio.to_thread` 包装，避免阻塞事件循环
- 配置一律走 `config.settings`，禁止业务代码里散落 `os.getenv`
- 日志用 loguru（补装后），禁止 `print`；标识符英文，注释与提示词中文皆可
- 测试一律写 pytest 用例放 `back/tests/`（pyproject 已配 `pythonpath=src` / `testpaths=tests` / `--strict-markers`）；涉及 LLM / Embedding 的用例加 `@pytest.mark.online`（未配 Key 自动跳过）；`src/test_*.py` 是可独立运行的验证脚本、非 pytest 用例

### Vue（Composition API + TypeScript）

- 一律 `<script setup lang="ts">` 组合式 API；全局状态进 pinia（`stores/`），组件不直接发请求
- 请求集中在 `src/api/`；SSE 解析只在 `api/chat.ts` 实现（@microsoft/fetch-event-source，EventSource 不支持 POST）
- 组件文件 PascalCase（`MessageItem.vue`），视图 `XxxView.vue`；Element Plus 全量引入（tsconfig 已配 `element-plus/global` 类型），图标按需 `import`，不引入其他 UI 库
- 样式 scoped；图表统一走 `ChartCard` 组件；提交前跑 `npm run type-check` 与 `npm run build`
- TypeScript 保持 ^5（vue-tsc 3.x 不兼容 TS 7）

## 6. 关键约定

1. **所有 API 以 `/api` 为前缀**（含健康检查 `/api/health`）。已实现路由（`api/__init__.py`，汇总为 `api.router`，main.py 注册）：

   ```
   POST   /api/chat                             # 普通对话（回答 + 工具步骤）
   POST   /api/chat/stream                      # SSE 流式对话（事件协议见下）
   POST   /api/knowledge/upload                 # multipart 上传 PDF/TXT/MD；同名重传幂等
   GET    /api/knowledge/list                   # 已入库文档（按源文件聚合，含大小 / 上传时间 / 片段数）
   POST   /api/knowledge/search                 # 检索测试（直接查向量库，返回片段 + 余弦相似度；不经 Agent）
   DELETE /api/knowledge/document?name=...      # 删除单个文档（同时清理 uploads 中的源文件）
   DELETE /api/knowledge/clear                  # 清空向量库
   GET    /api/models                           # 可用模型列表
   POST   /api/models/switch                    # 切换默认模型（进程内，重启回 .env）
   GET    /api/health
   ```

   规划中：`/api/chat/sessions`（会话列表 / 删除）、`/api/datasets`、`/api/settings/test-connection`；单 collection（`knowledge`）→ 多知识库 `kb_{id}` 的升级推迟到知识库管理阶段。

2. **SSE 流式输出**：链路为 `前端 fetch POST /api/chat/stream → agent.astream_events → 服务端过滤 → SSE 推送`。事件协议是前后端契约，改名/改字段必须双端同步并更新下表；langgraph 单轮事件可达上千条，禁止透传原始事件。响应统一带 `Cache-Control: no-cache`（生产反代另需 `X-Accel-Buffering: no`）。

   | event | data | 说明 |
   |---|---|---|
   | `meta` | `{session_id, model}` | 流开始，回传会话与模型 |
   | `token` | `{content}` | 正文增量（打字机） |
   | `reasoning` | `{content}` | 思考过程增量（有则折叠展示） |
   | `tool_start` | `{id, name, input}` | 工具开始 → 显示进行中卡片 |
   | `tool_end` | `{id, name, status, summary}` | 工具结束（success / error） |
   | `sources` | `{sources: [{doc, page, snippet}]}` | RAG 引用来源卡片 |
   | `chart` | `{option}` | ECharts option → 图表卡片渲染 |
   | `error` | `{message}` | 错误 |
   | `done` | `{usage}` | 流结束 |

3. **模型切换通过 `get_model` 函数**：`llm/` 模块的 `get_model(model: str | None = None)` 是获取 ChatOpenAI 实例的唯一入口（`get_embeddings()` 同理）；业务代码禁止直接实例化 `ChatOpenAI`，否则无法按会话切换模型。

4. **其他实现约定**：
   - Agent 组装唯一入口 `agent.get_agent()`；事件生产唯一入口 `agent.stream_agent_events()`（定义于 agent/events.py，包级已再导出；api 层只做 SSE 包装，不碰 astream_events）。类式封装 `agent.DataAnalysisAgent`（run / stream）内部仅委托上述入口；`run` 语义：不传 chat_history 走会话记忆（同一 session_id 累积），传 chat_history 为无状态单次调用（临时 thread_id，不读写会话记忆）
   - 对话持久化：AsyncSqliteSaver（待补装）；当前 `/api/chat` 为无状态单轮（每请求新建 InMemorySaver），`session_id` 仅在 SSE `meta` 事件回传，跨请求记忆待会话阶段接入
   - 每个知识库一个 Chroma collection（`kb_{id}`；当前单库为 `knowledge`，cosine 距离度量），chunk 元数据含 `source / page / doc_id / kb_id`
   - 分析工具（tools.python_executor）在受限命名空间执行 pandas：禁 import / 双下划线名称与属性，并按名字拒绝文件读写与反序列化入口（`_FORBIDDEN_ATTRS`：`read_*` / `to_*` / `load` / `save` / `format` 等）；15 秒执行上限 + 独立守护线程执行器（边界与局限见附 2）
   - 图表由工具返回 ECharts option JSON（不生成图片），前端 `ChartCard` 渲染

## 7. 环境变量说明

位置：`back/.env`（复制 `back/.env.example` 后填写；`.env` 不入库）。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | — | 百炼 API Key（必填） |
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容接口地址（国际站为 dashscope-intl） |
| `DEFAULT_MODEL` | `qwen3.7-plus` | 默认对话模型 |
| `AVAILABLE_MODELS` | `qwen3.7-plus,deepseek-v3,glm-5` | 设置页可选模型（逗号分隔） |
| `EMBEDDING_MODEL` | `text-embedding-v3` | 向量模型（v3/v4 向量空间不兼容，切换须重建索引） |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `700` / `100` | 文本切分参数 |
| `TOP_K` | `5` | 检索片段数 |
| `MAX_UPLOAD_MB` | `20` | 上传单文件大小上限（服务端强制，超限 413） |
| `AGENT_TIMEOUT_SECONDS` | `180` | 单轮对话总时长上限（超时普通对话 504 / 流式 error 事件） |
| `CHROMA_DIR` | `chroma_db/`（基于 back/ 解析） | 向量库持久化目录 |
| `KNOWLEDGE_DOCS_DIR` | `knowledge_docs/`（基于 back/ 解析） | 批量导入的默认文档目录 |
| `UPLOAD_DIR` / `DATASETS_DIR` / `SQLITE_PATH` | `data/...` | 运行时数据路径 |
| `CORS_ORIGINS` | `http://localhost:5173` | 开发跨域来源 |

## 8. 重要约束（硬性规则，违反视为 bug）

1. **禁止使用 `langchain-community`**：该包已 sunset（2026-06 GitHub 归档，终版 0.4.2），任何 `import`、任何层级的传递依赖都不允许。替代方案：

   | 原社区功能 | 替代 |
   |---|---|
   | 向量库集成 | `langchain-chroma` |
   | 文本分割 | `langchain-text-splitters` |
   | LLM / Embedding | `langchain-openai` |
   | Document / @tool | `langchain_core` |
   | 文档加载器 | 本项目 `rag/` 自行实现，禁止引入任何 loader 包 |

   验证命令（两条输出都必须为空）：`cd back && uv tree | grep -i community` 和 `grep -i langchain-community uv.lock`

2. **文档解析用 `pypdfium2` / `pymupdf` 直接实现**（在 `rag/` 中），不依赖任何 provider/loader 包
3. **向量库用 `langchain-chroma`**（+ `chromadb` 本体），不使用其他集成
4. **文本分割用 `langchain-text-splitters`** 独立包
5. **Agent 用 `langchain.agents.create_agent`**（LangGraph 状态图）；禁止 `AgentExecutor` / `initialize_agent`（已从 langchain 1.x 移除）
6. 依赖只通过 `uv add`（后端）/ `npm install`（前端）管理；禁止 pip 直装、禁止手改 `uv.lock` 与 `package-lock.json`

## 附 1：依赖现状与补装计划

- 已装（后端，2026-09 经阿里云镜像解析）：fastapi、uvicorn[standard]、pydantic-settings、python-dotenv、python-multipart、langchain（1.4.x）、langchain-core、langchain-openai、langchain-chroma、langchain-text-splitters、chromadb、pypdfium2、pymupdf、httpx、httpx-sse、numpy；dev：pytest、pytest-asyncio、ruff。langgraph 随 langchain 传递安装
- 2026-10 增补：`pandas`（3.x）+ `openpyxl`（工具阶段：python_executor 数据分析）、`sse-starlette`（3.5.x，API 阶段：SSE 推送）
- 待补装（进入对应阶段时 `uv add`）：`loguru`（日志）、`python-docx`（Word 解析）、`langgraph-checkpoint-sqlite`（会话持久化）

## 附 2：已知坑（血泪教训，勿踩）

- 百炼 Embedding：`OpenAIEmbeddings` 必须 `check_embedding_ctx_length=False` + `encoding_format="float"` + `chunk_size=10`，否则报 `contents is neither str nor list of str` 或 base64 不兼容（已封装在 `llm.get_embeddings()`）
- `text-embedding-v3` 与 `text-embedding-v4` 向量空间不兼容：更换 `EMBEDDING_MODEL` 后必须 `RagService.clear()` 重建索引，否则检索结果错乱
- Chroma collection 的距离度量创建后不可更改：`get_or_create_collection` 带不同 `configuration` 会**静默沿用**旧配置（已实测）；`knowledge` collection 固定 cosine（`collection_configuration={"hnsw": {"space": "cosine"}}`）——默认 l2 空间下 langchain 的 `1 - distance/√2` 相关性公式假设单位向量，对未归一化向量 + 平方距离会算出负数垃圾分，故检索相似度必须走 cosine（`相似度 = 1 - 余弦距离`）；更换度量 / embedding 模型须清空重建（`RagService.clear()` 或删 `chroma_db/`）
- qwen3 系列：非流式调用与工具调用需 `extra_body={"enable_thinking": False}`（思考模式与 Function Calling / json mode 冲突）
- 百炼已发布 2026-10-10 大批量模型下线公告（含 `deepseek-v3` 等第三方模型）：模型 ID 一律以百炼控制台为准，`AVAILABLE_MODELS` 可随时调整；第三方模型（glm-5 等）Function Calling 支持需实测
- `pypdfium2` 只做文本抽取（无 OCR），扫描版 PDF 用 `pymupdf` 相关能力或后续加 OCR
- vue-tsc 3.x 与 TypeScript 7 不兼容：typescript 保持 ^5，升级前先跑 `npm run type-check`
- uv 发现上层目录有 pyproject.toml 会尝试加入其工作区：根目录已无 pyproject.toml，`back/` 为独立项目，不要在上层再放
- src 布局：`back/src` 是源代码根（非包，不要加 `__init__.py`）。运行脚本时 `sys.path[0]` 是脚本所在目录，故测试脚本必须放在 `src/` 下（`uv run python src/test_xxx.py`）；uvicorn 需 `--app-dir src`（或把 Working directory 设为 `back/src`），否则找不到 `main:app`
- ruff BLE001：`except Exception` 处理器「记日志（`exc_info=True`）或总是 raise」时自动豁免，其余情况需 `# noqa: BLE001`（多余的 noqa 会被 RUF100 反向报错）
- `astream_events` 必须显式 `version="v2"`（langchain-core 1.6 起有实验性 v3 事件流，勿依赖默认值）；事件过滤层只保留契约内事件
- `search_knowledge` 用 `response_format="content_and_artifact"`（langchain-core 1.x）：`invoke/ainvoke` 不带 tool_call_id 时只返回 content 字符串；ToolNode 总会传 tool_call_id → 输出 `ToolMessage(content=..., artifact=...)`，events.py 从 artifact 取 sources（复现需 `arun(..., tool_call_id="x")`）
- `create_agent` 的 `ainvoke`/`astream` 返回 LangGraph 状态（`{"messages": [...]}`），没有 AgentExecutor 式的 `intermediate_steps`：工具步骤需自行从消息序列提取（按 id 配对 AIMessage.tool_calls 与 ToolMessage，见 `agent._extract_steps`；输出为 `[{tool, input, output}]`）
- 知识库上传接口的幂等与列表实现：同名文件重传先 `RagService.delete_document`（`store.get(where={"source": ...})` 取 ids → `store.delete(ids=ids)`）再入库；列表聚合用 `store.get(include=["metadatas"])`——两者均为 langchain-chroma 公开接口（勿碰 `_collection` 私有属性）
- SSE 裸报文格式：`EventSourceResponse` 输出 `event:` / `data:` 行 + 空行分隔，`data` 需自行 `json.dumps(ensure_ascii=False)`；默认每 15s 发 `: ping` 注释行（前端解析需忽略 `:` 开头行）。Windows Git Bash 向系统 curl 传中文 JSON 参数会乱码（报 FastAPI「error parsing the body」）：改用 `printf + --data-binary @-`，或直接用 httpx（tests/ 的 pytest 用例）验证
- `@microsoft/fetch-event-source`：`onerror` 回调必须 `throw`（返回非 undefined 会被当作重试间隔，POST 流式请求重放会导致消息重复）；`openWhenHidden: true` 防止标签页隐藏时 abort；自定义 `onopen` 会替换默认的 content-type 检查；signal abort 后 promise 是 resolve 而非 reject，调用方需以 `signal.aborted` 区分「手动停止」与异常
- Vue 响应式：`push` 进 reactive 数组后的对象必须重新从数组读取（拿代理）再修改，否则流式增量更新不触发重渲染
- `python_executor` 是受限命名空间而非安全沙箱：白名单 builtins + AST 拒绝 import / 双下划线名称与属性；numpy/pandas 的 C 扩展会在调用帧内懒加载 import（帧内无 `__import__` 会报 `KeyError: '__import__'`），故注入 `_guarded_import` 仅放行 numpy/pandas 子模块。**实测教训**：单靠 AST 拦不住 pandas 自带的 IO——`pd.read_csv('.env')` 可读到 `back/.env` 里的 API Key、`np.save` 可向任意路径写文件、`pd.read_pickle`/`np.load(allow_pickle=True)` 是反序列化入口，而 `'{0.__class__}'.format(pd)` 会绕过双下划线属性检查（属性名藏在字符串里）。故新增 `_FORBIDDEN_ATTRS` 按属性名封堵并移除 `format`（f-string 走 FORMAT_VALUE 字节码，不受影响）。这仍是黑名单式封堵，不是能力隔离；pandas 注入后其内部 IO 不受语言层限制，数据集阶段需在工具层约束路径
- `python_executor` 超时是 **best-effort**：15 秒上限用 `asyncio.wait_for` 实现，纯 Python 死循环（求值循环周期性释放 GIL）能准时中止；但**单次持有 GIL 的 C 级运算**（如 `9**20000000` 巨型大整数乘幂，实测 22 秒）会连事件循环一起冻住，定时器回调无法执行、超时根本不生效，整个服务卡住该运算的时长。彻底隔离需改为子进程（可强制 kill + 资源限额）
- `python_executor` 用自建守护线程执行器（`_DaemonExecutor`）而非 `asyncio.to_thread`/`ThreadPoolExecutor`：后者的工作线程是**非守护线程**且 `concurrent.futures` 注册了 atexit join——一旦超时后的计算仍在跑（线程无法强杀），**进程将永远无法退出**（Ctrl+C / uvicorn 关闭时卡死，实测 `exit=124`）。守护线程随解释器退出被回收；投递结果用 `loop.call_soon_threadsafe` 并吞掉循环已关闭时的 `RuntimeError`。另：专用执行器与 asyncio 默认池隔离（默认池同时承载 Chroma / 文档解析 / 文件写入）
- 前端 Markdown 渲染：marked 自带的 `cleanUrl` **只做 `encodeURI`、不校验协议**（v18 实测），故 `[x](javascript:alert(1))` 会渲染成可点击的 `javascript:` 链接，在 `v-html` 场景下是 XSS（大小写混写、前缀空格均可绕过）。`utils/markdown.ts` 必须自行重写 `link`/`image` 渲染器做协议白名单（http/https/mailto/tel + 相对路径），不合法时降级为纯文本；原始 HTML 的转义由 `html` 渲染器负责
- 上传大小限制必须服务端强制：前端 `KnowledgeView` 的 20MB 校验只是提示，用 curl/httpx 可零成本绕过；`api._read_upload_limited` 分块读取并累加计数，超限 413（`MAX_UPLOAD_MB`）
- 在 `back/.venv` 内检索已装库源码时，Grep 工具会因 `.gitignore` 忽略 `.venv/` 而搜不到：改用 Bash `grep` 或指定具体文件路径（指定文件不受忽略规则影响）
- Windows 控制台默认 GBK 编码，中文输出乱码：入口脚本已对 stdout/stderr 强制 UTF-8（`sys.stdout.reconfigure`）；后续新增打印中文的脚本照做，终端仍乱码则用 `chcp 65001` 或 Windows Terminal
- pytest 化踩坑（2026-10 实测）：① 会话级异步 fixture 会撞事件循环作用域（异步 fixture 默认函数级循环），跨用例共享的 `seeded_kb` 改用**同步 httpx.Client** 实现绕开；② conftest 自动拉起后端时 `python -m uvicorn` 的 stdout/stderr 必须置 `DEVNULL`（管道写满会阻塞子进程），Windows 下 `terminate()` 即可干净退出（无 `--reload`）；③ ruff isort 以 `src` 目录判 first-party，`tests/utils.py` 会被错判为第三方报 I001，已在 `[tool.ruff.lint.isort] known-first-party` 加入 `"utils"`

## 附 3：CI（GitHub Actions）

- `.github/workflows/backend-ci.yml`：push / PR / 手动触发（workflow_dispatch）；步骤 = `uv sync --frozen`（`uv.lock` 必须随提交，否则 CI 失败）→ ruff check + format --check → pytest（conftest 自动拉起后端，无需预启服务）
- 联网用例需仓库 Secret `DASHSCOPE_API_KEY`（Settings → Secrets and variables → Actions → New repository secret）；未配置时 env 为空串 → online 用例自动跳过，离线用例仍全量运行，CI 保持绿色
- 本地模拟无 Key 场景：`DASHSCOPE_API_KEY= uv run pytest`（空环境变量会覆盖 .env，已实测）
- action 版本（2026-10 经 WebSearch 核对）：`actions/checkout@v6`、`astral-sh/setup-uv@v10`
