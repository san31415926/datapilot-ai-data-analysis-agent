"""DataPilot 的运行配置。"""

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
    max_query_rows: int = 1_000
    max_query_result_mb: int = 1
    query_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            app_name=os.getenv("DATAPILOT_APP_NAME", cls.app_name),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url).rstrip("/"),
            default_model=os.getenv("DATAPILOT_DEFAULT_MODEL", cls.default_model),
            max_upload_mb=_read_positive_int("DATAPILOT_MAX_UPLOAD_MB", cls.max_upload_mb),
            max_rows=_read_positive_int("DATAPILOT_MAX_ROWS", cls.max_rows),
            max_query_rows=_read_positive_int(
                "DATAPILOT_MAX_QUERY_ROWS", cls.max_query_rows
            ),
            max_query_result_mb=_read_positive_int(
                "DATAPILOT_MAX_QUERY_RESULT_MB", cls.max_query_result_mb
            ),
            query_timeout_seconds=_read_positive_float(
                "DATAPILOT_QUERY_TIMEOUT_SECONDS", cls.query_timeout_seconds
            ),
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


def _read_positive_float(name: str, fallback: float) -> float:
    value = os.getenv(name)
    if value is None:
        return fallback
    try:
        parsed = float(value)
    except ValueError:
        return fallback
    return parsed if parsed > 0 else fallback


def get_settings() -> Settings:
    """返回当前进程使用的不可变配置。"""

    return Settings.from_env()
