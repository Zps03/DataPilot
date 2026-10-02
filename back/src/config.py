"""DataPilot 全局配置：唯一配置入口（pydantic-settings，读取 back/.env）。"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 源码在 back/src/（源代码根）；运行时数据与 .env 以 back/ 为基准
SRC_DIR = Path(__file__).resolve().parent
BASE_DIR = SRC_DIR.parent


class Settings(BaseSettings):
    """环境变量与默认配置。字段名对应同名大写环境变量（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 阿里云百炼（OpenAI 兼容接口） ----
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # ---- 模型 ----
    default_model: str = "qwen3.7-plus"
    available_models: str = "qwen3.7-plus,deepseek-v3,glm-5"  # 逗号分隔，见 model_list
    embedding_model: str = "text-embedding-v3"  # v3/v4 向量空间不兼容，切换须重建索引

    # ---- RAG 参数 ----
    chunk_size: int = 700
    chunk_overlap: int = 100
    top_k: int = 5
    knowledge_docs_dir: Path = BASE_DIR / "knowledge_docs"  # 批量导入的默认目录

    # ---- 运行时路径（相对路径基于 back/ 解析） ----
    data_dir: Path = BASE_DIR / "data"
    chroma_dir: Path = BASE_DIR / "chroma_db"
    upload_dir: Path = BASE_DIR / "data" / "uploads"
    datasets_dir: Path = BASE_DIR / "data" / "datasets"
    sqlite_path: Path = BASE_DIR / "data" / "datapilot.db"

    # ---- 开发 ----
    cors_origins: str = "http://localhost:5173"  # 逗号分隔

    @field_validator(
        "data_dir",
        "chroma_dir",
        "knowledge_docs_dir",
        "upload_dir",
        "datasets_dir",
        "sqlite_path",
        mode="after",
    )
    @classmethod
    def _resolve_relative(cls, value: Path) -> Path:
        """环境变量中的相对路径统一基于 back/ 解析。"""
        return value if value.is_absolute() else BASE_DIR / value

    @property
    def model_list(self) -> list[str]:
        """设置页可选模型列表。"""
        return [item.strip() for item in self.available_models.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def ensure_dirs(self) -> None:
        """创建运行时目录（幂等，lifespan 启动时调用）。"""
        for directory in (
            self.data_dir,
            self.chroma_dir,
            self.knowledge_docs_dir,
            self.upload_dir,
            self.datasets_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
