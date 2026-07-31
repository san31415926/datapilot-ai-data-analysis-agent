"""DataPilot 的 Ollama 本地模型客户端。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

GENERATION_CAPABILITIES = frozenset({"completion", "tools", "thinking"})
EMBEDDING_NAME_MARKERS = ("embed", "embedding")


class OllamaClientError(RuntimeError):
    """可直接转成页面提示的 Ollama 错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OllamaModel:
    name: str
    parameter_size: str | None = None
    capabilities: tuple[str, ...] = ()
    size_bytes: int = 0


@dataclass(frozen=True)
class ModelListResult:
    """模型发现结果，同时保留服务状态和过滤后的生成模型。"""

    available: bool
    models: tuple[OllamaModel, ...] = ()
    all_models: tuple[OllamaModel, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ChatResult:
    model: str
    content: str
    thinking: str
    elapsed_ms: int


class OllamaClient:
    """通过 Ollama HTTP API 发现模型并执行非流式聊天请求。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        timeout_seconds: float = 120.0,
        temperature: float = 0.2,
        max_output_tokens: int = 800,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0 or temperature < 0 or max_output_tokens <= 0:
            raise ValueError("Ollama 配置必须满足超时大于 0、温度不小于 0、输出长度大于 0")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def list_models(self) -> ModelListResult:
        """读取已安装模型，并过滤仅用于 embedding 的模型。"""

        try:
            response = self._client.get("/api/tags", headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            return _model_error("OLLAMA_TIMEOUT", "连接 Ollama 超时，请确认服务正在运行。")
        except httpx.HTTPError:
            return _model_error("OLLAMA_UNAVAILABLE", "无法连接 Ollama，请先启动 Ollama 服务。")

        if response.status_code >= 400:
            return _model_error(
                "OLLAMA_HTTP_ERROR",
                f"Ollama 返回 HTTP {response.status_code}，暂时无法读取模型列表。",
            )
        try:
            payload = response.json()
        except ValueError:
            return _model_error("INVALID_RESPONSE", "Ollama 模型列表不是有效 JSON。")
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return _model_error("INVALID_RESPONSE", "Ollama 模型列表格式不正确。")

        all_models = tuple(
            model
            for raw_model in payload["models"]
            if (model := _parse_model(raw_model)) is not None
        )
        generation_models = tuple(model for model in all_models if _is_generation_model(model))
        message = None
        if not generation_models:
            message = "Ollama 服务可用，但没有检测到可用于生成文字的模型。"
        return ModelListResult(
            available=True,
            models=generation_models,
            all_models=all_models,
            error_message=message,
        )

    def chat(
        self,
        model: str,
        messages: Iterable[dict[str, str]],
        *,
        response_format: str | dict[str, Any] | None = None,
        think: bool | None = None,
    ) -> ChatResult:
        """调用本地聊天模型，返回最终内容和可选思考字段。"""

        model_name = model.strip() if isinstance(model, str) else ""
        if not model_name or len(model_name) > 200:
            raise OllamaClientError("INVALID_MODEL", "模型名称不能为空且不能超过 200 个字符。")
        normalized_messages = _normalize_messages(messages)
        started_at = time.perf_counter()
        payload = {
            "model": model_name,
            "messages": normalized_messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_output_tokens,
            },
        }
        if response_format is not None:
            payload["format"] = response_format
        if think is not None:
            payload["think"] = think
        try:
            response = self._client.post(
                "/api/chat",
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise OllamaClientError("OLLAMA_TIMEOUT", "模型生成超时，请缩短问题或提高超时时间。") from exc
        except httpx.HTTPError as exc:
            raise OllamaClientError("OLLAMA_UNAVAILABLE", "无法连接 Ollama，请先启动 Ollama 服务。") from exc

        if response.status_code == 404:
            raise OllamaClientError("MODEL_NOT_FOUND", f"本机没有找到模型“{model_name}”。")
        if response.status_code >= 400:
            raise OllamaClientError(
                "OLLAMA_HTTP_ERROR",
                f"Ollama 返回 HTTP {response.status_code}，模型调用失败。",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OllamaClientError("INVALID_RESPONSE", "Ollama 返回内容不是有效 JSON。") from exc
        if not isinstance(payload, dict):
            raise OllamaClientError("INVALID_RESPONSE", "Ollama 返回内容格式不正确。")

        message = payload.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        content = str(message.get("content") or payload.get("response") or "").strip()
        thinking = str(message.get("thinking") or "").strip()
        if not content:
            raise OllamaClientError("EMPTY_RESPONSE", "模型没有返回可用的回答内容。")
        return ChatResult(
            model=str(payload.get("model") or model_name),
            content=content,
            thinking=thinking,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )


def _parse_model(raw_model: Any) -> OllamaModel | None:
    if not isinstance(raw_model, dict):
        return None
    name = str(raw_model.get("name") or raw_model.get("model") or "").strip()
    if not name:
        return None
    raw_capabilities = raw_model.get("capabilities") or []
    capabilities = tuple(str(item).lower() for item in raw_capabilities if str(item).strip())
    details = raw_model.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    try:
        size_bytes = int(raw_model.get("size") or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    return OllamaModel(
        name=name,
        parameter_size=str(details.get("parameter_size")) if details.get("parameter_size") else None,
        capabilities=capabilities,
        size_bytes=size_bytes,
    )


def _is_generation_model(model: OllamaModel) -> bool:
    if model.capabilities and not GENERATION_CAPABILITIES.intersection(model.capabilities):
        return False
    name = model.name.lower()
    return not any(marker in name for marker in EMBEDDING_NAME_MARKERS)


def _normalize_messages(messages: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    if isinstance(messages, (str, bytes)):
        raise OllamaClientError("INVALID_MESSAGES", "消息必须是带 role 和 content 的列表。")
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise OllamaClientError("INVALID_MESSAGES", "消息必须是带 role 和 content 的对象。")
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if role not in {"system", "user", "assistant"} or not content:
            raise OllamaClientError("INVALID_MESSAGES", "消息 role 或 content 不合法。")
        if len(content) > 100_000:
            raise OllamaClientError("CONTEXT_TOO_LARGE", "单条消息不能超过 100000 个字符。")
        normalized.append({"role": role, "content": content})
    if not normalized:
        raise OllamaClientError("INVALID_MESSAGES", "消息列表不能为空。")
    return normalized


def _model_error(code: str, message: str) -> ModelListResult:
    return ModelListResult(available=False, error_code=code, error_message=message)
