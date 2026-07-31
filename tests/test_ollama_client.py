import json
import unittest

import httpx

from src.ollama_client import OllamaClient, OllamaClientError


class OllamaClientTestCase(unittest.TestCase):
    def test_lists_generation_models_and_filters_embedding_models(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/tags")
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen2.5:3b", "details": {"parameter_size": "3.1B"}},
                        {"name": "embeddinggemma:300m", "details": {"parameter_size": "307M"}},
                        {"name": "custom-embed", "capabilities": ["embedding"]},
                    ]
                },
            )

        with OllamaClient(transport=httpx.MockTransport(handler)) as client:
            result = client.list_models()

        self.assertTrue(result.available)
        self.assertEqual([model.name for model in result.models], ["qwen2.5:3b"])
        self.assertEqual(len(result.all_models), 3)

    def test_chat_sends_local_model_options_and_parses_content(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "model": "qwen2.5:3b",
                    "message": {"role": "assistant", "content": "华东销售额最高。"},
                    "done": True,
                },
            )

        with OllamaClient(
            temperature=0.4,
            max_output_tokens=500,
            transport=httpx.MockTransport(handler),
        ) as client:
            result = client.chat("qwen2.5:3b", [{"role": "user", "content": "哪个地区销售额最高？"}])

        payload = json.loads(requests[0].content)
        self.assertEqual(requests[0].url.path, "/api/chat")
        self.assertEqual(payload["model"], "qwen2.5:3b")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["num_predict"], 500)
        self.assertEqual(result.content, "华东销售额最高。")

    def test_reports_service_unavailable_without_exposing_transport_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with OllamaClient(transport=httpx.MockTransport(handler)) as client:
            result = client.list_models()

        self.assertFalse(result.available)
        self.assertEqual(result.error_code, "OLLAMA_UNAVAILABLE")
        self.assertIn("启动 Ollama", result.error_message)

    def test_reports_model_not_found_and_empty_response(self) -> None:
        def not_found(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model not found"})

        with OllamaClient(transport=httpx.MockTransport(not_found)) as client:
            with self.assertRaises(OllamaClientError) as context:
                client.chat("missing:1b", [{"role": "user", "content": "测试"}])
        self.assertEqual(context.exception.code, "MODEL_NOT_FOUND")

        def empty(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"role": "assistant", "content": ""}})

        with OllamaClient(transport=httpx.MockTransport(empty)) as client:
            with self.assertRaises(OllamaClientError) as context:
                client.chat("qwen2.5:3b", [{"role": "user", "content": "测试"}])
        self.assertEqual(context.exception.code, "EMPTY_RESPONSE")

    def test_rejects_invalid_messages_before_http_call(self) -> None:
        with OllamaClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
            with self.assertRaises(OllamaClientError) as context:
                client.chat("qwen2.5:3b", [{"role": "unknown", "content": "测试"}])
        self.assertEqual(context.exception.code, "INVALID_MESSAGES")


if __name__ == "__main__":
    unittest.main()
