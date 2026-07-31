import unittest

from src.analysis_runner import execute_analysis_plan
from src.data_loader import load_file
from src.query_engine import ReadOnlyQueryEngine


def valid_plan() -> dict[str, object]:
    return {
        "user_goal": "比较各地区销售额",
        "steps": [
            {
                "tool": "get_data_overview",
                "parameters": {"include_quality_issues": True},
                "expected_output": "数据概览和质量问题",
            },
            {
                "tool": "group_by_summary",
                "parameters": {
                    "group_by": "地区",
                    "metric": "销售额",
                    "aggregation": "sum",
                    "sort_direction": "desc",
                    "limit": 10,
                },
                "expected_output": "各地区销售额汇总",
            },
        ],
        "report_focus": ["最高地区", "销售额"],
    }


class AnalysisRunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = load_file(
            "runner.csv",
            "地区,销售额,订单数\n华东,1200,12\n华南,900,9\n华东,500,5\n".encode("utf-8"),
        )
        self.engine = ReadOnlyQueryEngine(
            self.dataset.dataframe,
            max_rows=100,
            max_result_bytes=100_000,
            timeout_seconds=2,
        )

    def tearDown(self) -> None:
        self.engine.close()

    def test_executes_valid_plan_and_collects_evidence(self) -> None:
        result = execute_analysis_plan(valid_plan(), self.dataset, self.engine)

        self.assertTrue(result.success)
        self.assertEqual(len(result.steps), 2)
        self.assertTrue(all(step.success for step in result.steps))
        self.assertEqual(result.steps[1].result["rows"][0]["分组值"], "华东")
        self.assertEqual([item["step"] for item in result.to_evidence()], [1, 2])

    def test_revalidates_unknown_field_before_running_any_step(self) -> None:
        plan = valid_plan()
        plan["steps"][1]["parameters"]["group_by"] = "不存在字段"

        result = execute_analysis_plan(plan, self.dataset, self.engine)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.steps, [])
        self.assertIn("字段不存在", result.error_message)

    def test_stops_after_runtime_tool_error(self) -> None:
        plan = valid_plan()
        plan["steps"] = [
            plan["steps"][0],
            {
                "tool": "run_readonly_sql",
                "parameters": {"sql": "SELECT * FROM uploaded_data"},
                "expected_output": "查询结果",
            },
            plan["steps"][1],
        ]

        limited_engine = ReadOnlyQueryEngine(
            self.dataset.dataframe,
            max_rows=1,
            max_result_bytes=100_000,
            timeout_seconds=2,
        )
        try:
            result = execute_analysis_plan(plan, self.dataset, limited_engine)
        finally:
            limited_engine.close()

        self.assertFalse(result.success)
        self.assertEqual(result.status, "error")
        self.assertEqual(len(result.steps), 2)
        self.assertTrue(result.steps[0].success)
        self.assertFalse(result.steps[1].success)
        self.assertIn("停止后续步骤", result.error_message)


if __name__ == "__main__":
    unittest.main()
