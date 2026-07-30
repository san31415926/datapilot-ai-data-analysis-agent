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

    def test_environment_values_are_loaded(self) -> None:
        values = {
            "DATAPILOT_APP_NAME": "测试工作台",
            "OLLAMA_BASE_URL": "http://localhost:11434/",
            "DATAPILOT_DEFAULT_MODEL": "qwen3:4b",
            "DATAPILOT_MAX_UPLOAD_MB": "8",
            "DATAPILOT_MAX_ROWS": "2000",
        }

        with patch.dict(os.environ, values, clear=False):
            settings = Settings.from_env()

        self.assertEqual(settings.app_name, "测试工作台")
        self.assertEqual(settings.ollama_base_url, "http://localhost:11434")
        self.assertEqual(settings.default_model, "qwen3:4b")
        self.assertEqual(settings.max_upload_mb, 8)
        self.assertEqual(settings.max_rows, 2000)

    def test_invalid_positive_integers_use_defaults(self) -> None:
        values = {
            "DATAPILOT_MAX_UPLOAD_MB": "not-a-number",
            "DATAPILOT_MAX_ROWS": "0",
        }

        with patch.dict(os.environ, values, clear=False):
            settings = Settings.from_env()

        self.assertEqual(settings.max_upload_mb, Settings.max_upload_mb)
        self.assertEqual(settings.max_rows, Settings.max_rows)


if __name__ == "__main__":
    unittest.main()
