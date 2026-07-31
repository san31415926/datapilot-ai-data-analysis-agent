import unittest

import pandas as pd
from pydantic import ValidationError

from src.data_loader import load_file
from src.query_engine import ReadOnlyQueryEngine
from src.tools import (
    AnomalyRequest,
    ChartRequest,
    GroupBySummaryRequest,
    build_chart,
    detect_anomalies,
    get_data_overview,
    group_by_summary,
    run_readonly_sql,
)


class ToolsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ReadOnlyQueryEngine(
            pd.DataFrame(
                {
                    "地区": ["华东", "华南", "华东", "华北"],
                    "销售额": [100.0, 80.0, 120.0, 99999.99],
                    "数量": [1, 2, 1, 1],
                }
            ),
            max_rows=100,
            max_result_bytes=100_000,
            timeout_seconds=2,
        )

    def tearDown(self) -> None:
        self.engine.close()

    def test_data_overview_tool_returns_quality_summary(self) -> None:
        dataset = load_file(
            "overview.csv",
            "订单编号,地区,销售额\nORD-1,华东,100\nORD-2,,200\n".encode("utf-8"),
        )

        result = get_data_overview(dataset)

        self.assertTrue(result.success)
        self.assertEqual(result.overview.row_count, 2)
        self.assertEqual(result.overview.column_count, 3)
        self.assertEqual(result.overview.missing_cell_count, 1)
        self.assertEqual(result.record.tool_name, "get_data_overview")

    def test_readonly_sql_tool_returns_structured_result_and_record(self) -> None:
        result = run_readonly_sql(
            self.engine,
            {"sql": "SELECT 地区, 销售额 FROM uploaded_data ORDER BY 销售额 DESC LIMIT 2"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.row_count, 2)
        self.assertEqual(result.record.tool_name, "run_readonly_sql")
        self.assertEqual(result.record.result_summary["row_count"], 2)
        self.assertEqual(len(result.record.input_summary["sql_sha256_12"]), 12)

    def test_group_summary_validates_fields_before_query(self) -> None:
        result = group_by_summary(
            self.engine,
            {
                "group_by": "地区",
                "metric": "销售额",
                "aggregation": "sum",
                "limit": 2,
            },
        )

        self.assertTrue(result.success)
        self.assertEqual(result.columns, ["分组值", "销售额合计"])
        self.assertEqual(result.rows[0]["分组值"], "华北")
        self.assertEqual(result.record.tool_name, "group_by_summary")

        invalid = group_by_summary(self.engine, {"group_by": "不存在字段"})
        self.assertFalse(invalid.success)
        self.assertEqual(invalid.record.error_code, "INVALID_FIELD")

    def test_group_summary_requires_numeric_metric(self) -> None:
        with self.assertRaises(ValidationError):
            GroupBySummaryRequest(group_by="地区", aggregation="sum")

        result = group_by_summary(
            self.engine,
            {"group_by": "地区", "metric": "地区", "aggregation": "sum"},
        )
        self.assertFalse(result.success)
        self.assertEqual(result.record.error_code, "METRIC_NOT_NUMERIC")

    def test_iqr_anomaly_tool_finds_outlier(self) -> None:
        result = detect_anomalies(self.engine, {"metric": "销售额"})

        self.assertTrue(result.success)
        self.assertEqual(result.record.tool_name, "detect_anomalies")
        self.assertEqual(result.rows[0]["销售额"], 99999.99)
        self.assertTrue(result.rows[0]["是否异常"])

    def test_chart_tool_validates_and_returns_data_spec(self) -> None:
        frame = pd.DataFrame({"地区": ["华东", "华南"], "销售额": [220.0, 80.0]})
        result = build_chart(
            frame,
            {"chart_type": "bar", "x_field": "地区", "y_field": "销售额", "title": "地区销售额"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.chart.chart_type, "bar")
        self.assertEqual(len(result.chart.data), 2)

        invalid = build_chart(
            frame,
            {"chart_type": "line", "x_field": "地区", "y_field": "不存在"},
        )
        self.assertFalse(invalid.success)
        self.assertEqual(invalid.record.error_code, "INVALID_FIELD")

    def test_pydantic_rejects_unknown_tool_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            ChartRequest(
                chart_type="bar",
                x_field="地区",
                y_field="销售额",
                extra_argument=True,
            )
        with self.assertRaises(ValidationError):
            AnomalyRequest(metric="销售额", iqr_multiplier=20)


if __name__ == "__main__":
    unittest.main()
