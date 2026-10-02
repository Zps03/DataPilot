"""DataPilot 后端入口：FastAPI 应用、CORS、路由注册、全局异常处理。

启动：cd back && uv run uvicorn main:app --app-dir src --reload --port 8000
Swagger：http://127.0.0.1:8000/docs
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import APP_VERSION
from api import router as api_router
from config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 运行时目录（data/、chroma_db/、uploads/、datasets/）；向量库与 Agent 均惰性初始化
    settings.ensure_dirs()
    logger.info(
        "DataPilot 启动：默认模型=%s，向量库目录=%s", settings.default_model, settings.chroma_dir
    )
    yield


app = FastAPI(title="DataPilot API", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局兜底：未捕获异常统一 500 JSON（细节只进日志，不外传）。"""
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请查看后端日志"})
