import unittest

import pandas as pd

from src.query_engine import ReadOnlyQueryEngine


class ReadOnlyQueryEngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ReadOnlyQueryEngine(
            pd.DataFrame(
                {
                    "地区": ["华东", "华南", "华东"],
                    "销售额": [100.0, 80.0, 120.0],
                }
            ),
            max_rows=2,
            max_result_bytes=10_000,
            timeout_seconds=2,
        )

    def tearDown(self) -> None:
        self.engine.close()

    def test_executes_readonly_query_and_records_result(self) -> None:
        response = self.engine.execute(
            "SELECT 地区, SUM(销售额) AS 总销售额 FROM uploaded_data GROUP BY 地区 ORDER BY 总销售额 DESC LIMIT 2"
        )

        self.assertTrue(response.success)
        self.assertEqual(response.execution.status, "success")
        self.assertEqual(response.execution.row_count, 2)
        self.assertEqual(list(response.dataframe.columns), ["地区", "总销售额"])
        self.assertGreaterEqual(response.execution.elapsed_ms, 0)

    def test_rejected_query_does_not_execute(self) -> None:
        response = self.engine.execute("DELETE FROM uploaded_data")

        self.assertFalse(response.success)
        self.assertEqual(response.execution.status, "rejected")
        self.assertEqual(response.execution.error_code, "READ_ONLY_REQUIRED")
        self.assertIsNone(response.dataframe)

    def test_limits_result_rows(self) -> None:
        response = self.engine.execute("SELECT * FROM uploaded_data")

        self.assertFalse(response.success)
        self.assertEqual(response.execution.error_code, "RESULT_TOO_LARGE")
        self.assertIsNone(response.dataframe)

    def test_returns_safe_error_for_invalid_reference(self) -> None:
        response = self.engine.execute("SELECT 不存在字段 FROM uploaded_data")

        self.assertFalse(response.success)
        self.assertEqual(response.execution.error_code, "INVALID_REFERENCE")
        self.assertIn("字段", response.execution.error_message)

    def test_limits_result_bytes(self) -> None:
        engine = ReadOnlyQueryEngine(
            pd.DataFrame({"内容": ["x" * 100, "y" * 100]}),
            max_rows=10,
            max_result_bytes=20,
        )
        try:
            response = engine.execute("SELECT * FROM uploaded_data")
        finally:
            engine.close()

        self.assertFalse(response.success)
        self.assertEqual(response.execution.error_code, "RESULT_TOO_LARGE")

    def test_interrupts_long_running_query(self) -> None:
        engine = ReadOnlyQueryEngine(
            pd.DataFrame({"value": [1] * 1_000}),
            max_rows=10,
            timeout_seconds=0.05,
        )
        try:
            response = engine.execute(
                "SELECT SUM(a.value) AS total FROM uploaded_data a "
                "CROSS JOIN uploaded_data b CROSS JOIN uploaded_data c "
                "CROSS JOIN uploaded_data d CROSS JOIN uploaded_data e"
            )
        finally:
            engine.close()

        self.assertFalse(response.success)
        self.assertEqual(response.execution.error_code, "QUERY_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
