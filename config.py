"""DataPilot 的阶段性运行配置。

当前阶段只提供配置读取和启动页需要的默认值，模型调用会在后续阶段接入。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """集中保存不会依赖 Streamlit 状态的应用配置。"""

    app_name: str = "DataPilot"
    ollama_base_url: str = "http://127.0.0.1:11434"
    default_model: str = "qwen2.5:3b"
    max_upload_mb: int = 20
    max_rows: int = 100_000

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            app_name=os.getenv("DATAPILOT_APP_NAME", cls.app_name),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url).rstrip("/"),
            default_model=os.getenv("DATAPILOT_DEFAULT_MODEL", cls.default_model),
            max_upload_mb=_read_positive_int("DATAPILOT_MAX_UPLOAD_MB", cls.max_upload_mb),
            max_rows=_read_positive_int("DATAPILOT_MAX_ROWS", cls.max_rows),
        )


def _read_positive_int(name: str, fallback: int) -> int:
    value = os.getenv(name)
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except ValueError:
        return fallback
    return parsed if parsed > 0 else fallback


def get_settings() -> Settings:
    """返回当前进程使用的不可变配置。"""

    return Settings.from_env()
