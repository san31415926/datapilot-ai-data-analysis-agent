import os
import unittest
from unittest.mock import patch

from config import Settings


class SettingsTestCase(unittest.TestCase):
    def test_default_settings_are_local_and_positive(self) -> None:
        settings = Settings()

        self.assertEqual(settings.ollama_base_url, "http://127.0.0.1:11434")
        self.assertEqual(settings.default_model, "qwen2.5:3b")
        self.assertGreater(settings.max_upload_mb, 0)
        self.assertGreater(settings.max_rows, 0)
        self.assertGreater(settings.max_query_rows, 0)
        self.assertGreater(settings.max_query_result_mb, 0)
        self.assertGreater(settings.query_timeout_seconds, 0)
        self.assertGreater(settings.ollama_timeout_seconds, 0)
        self.assertGreaterEqual(settings.ollama_temperature, 0)
        self.assertGreater(settings.ollama_max_output_tokens, 0)

    def test_environment_values_are_loaded(self) -> None:
        values = {
            "DATAPILOT_APP_NAME": "测试工作台",
            "OLLAMA_BASE_URL": "http://localhost:11434/",
            "DATAPILOT_DEFAULT_MODEL": "qwen3:4b",
            "DATAPILOT_MAX_UPLOAD_MB": "8",
            "DATAPILOT_MAX_ROWS": "2000",
            "DATAPILOT_MAX_QUERY_ROWS": "80",
            "DATAPILOT_MAX_QUERY_RESULT_MB": "2",
            "DATAPILOT_QUERY_TIMEOUT_SECONDS": "1.5",
            "DATAPILOT_OLLAMA_TIMEOUT_SECONDS": "60",
            "DATAPILOT_OLLAMA_TEMPERATURE": "0.4",
            "DATAPILOT_OLLAMA_MAX_OUTPUT_TOKENS": "500",
        }

        with patch.dict(os.environ, values, clear=False):
            settings = Settings.from_env()

        self.assertEqual(settings.app_name, "测试工作台")
        self.assertEqual(settings.ollama_base_url, "http://localhost:11434")
        self.assertEqual(settings.default_model, "qwen3:4b")
        self.assertEqual(settings.max_upload_mb, 8)
        self.assertEqual(settings.max_rows, 2000)
        self.assertEqual(settings.max_query_rows, 80)
        self.assertEqual(settings.max_query_result_mb, 2)
        self.assertEqual(settings.query_timeout_seconds, 1.5)
        self.assertEqual(settings.ollama_timeout_seconds, 60)
        self.assertEqual(settings.ollama_temperature, 0.4)
        self.assertEqual(settings.ollama_max_output_tokens, 500)

    def test_invalid_positive_integers_use_defaults(self) -> None:
        values = {
            "DATAPILOT_MAX_UPLOAD_MB": "not-a-number",
            "DATAPILOT_MAX_ROWS": "0",
            "DATAPILOT_MAX_QUERY_ROWS": "-1",
            "DATAPILOT_MAX_QUERY_RESULT_MB": "bad",
            "DATAPILOT_QUERY_TIMEOUT_SECONDS": "0",
            "DATAPILOT_OLLAMA_TIMEOUT_SECONDS": "bad",
            "DATAPILOT_OLLAMA_TEMPERATURE": "-0.1",
            "DATAPILOT_OLLAMA_MAX_OUTPUT_TOKENS": "0",
        }

        with patch.dict(os.environ, values, clear=False):
            settings = Settings.from_env()

        self.assertEqual(settings.max_upload_mb, Settings.max_upload_mb)
        self.assertEqual(settings.max_rows, Settings.max_rows)
        self.assertEqual(settings.max_query_rows, Settings.max_query_rows)
        self.assertEqual(settings.max_query_result_mb, Settings.max_query_result_mb)
        self.assertEqual(settings.query_timeout_seconds, Settings.query_timeout_seconds)
        self.assertEqual(settings.ollama_timeout_seconds, Settings.ollama_timeout_seconds)
        self.assertEqual(settings.ollama_temperature, Settings.ollama_temperature)
        self.assertEqual(settings.ollama_max_output_tokens, Settings.ollama_max_output_tokens)


if __name__ == "__main__":
    unittest.main()
