import unittest

import pandas as pd

from src.ollama_client import ChatResult
from src.planner import (
    AnalysisPlan,
    StructuredPlanner,
    build_planner_prompt,
    normalize_plan_fields,
    parse_plan_text,
    validate_analysis_plan,
)
from src.query_engine import ReadOnlyQueryEngine


def valid_plan() -> dict[str, object]:
    return {
        "user_goal": "比较不同地区的销售额",
        "steps": [
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
            }
        ],
        "report_focus": ["最高地区", "地区差异"],
    }


class FakePlannerClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, model: str, messages: list[dict[str, str]], **kwargs: object) -> ChatResult:
        self.calls.append(messages)
        return ChatResult(model=model, content=self.responses.pop(0), thinking="", elapsed_ms=1)


class PlannerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ReadOnlyQueryEngine(
            pd.DataFrame(
                {
                    "地区": ["华东", "华南"],
                    "销售额": [100.0, 80.0],
                    "数量": [1, 2],
                }
            ),
            max_rows=100,
            max_result_bytes=100_000,
            timeout_seconds=2,
        )
        self.schema = [
            {"name": "地区", "logical_type": "text", "role": "category"},
            {"name": "销售额", "logical_type": "numeric", "role": "measure"},
            {"name": "数量", "logical_type": "numeric", "role": "measure"},
        ]

    def tearDown(self) -> None:
        self.engine.close()

    def test_parser_accepts_markdown_code_block_and_extra_text(self) -> None:
        plan_json = '{"user_goal":"比较地区","steps":[{"tool":"get_data_overview","parameters":{},"expected_output":"字段概览"}],"report_focus":["字段"]}'
        result = parse_plan_text(f"下面是计划：\n```json\n{plan_json}\n```\n请校验。")

        self.assertTrue(result.success)
        self.assertEqual(result.plan.steps[0].tool, "get_data_overview")
        self.assertIsNotNone(result.normalized_json)

    def test_parser_rejects_invalid_schema_and_json(self) -> None:
        invalid_json = parse_plan_text("模型暂时无法生成计划")
        self.assertFalse(invalid_json.success)
        self.assertEqual(invalid_json.problems[0].code, "INVALID_JSON")

        invalid_schema = parse_plan_text('{"user_goal":"x","steps":[],"report_focus":[]}')
        self.assertFalse(invalid_schema.success)
        self.assertEqual(invalid_schema.problems[0].code, "PLAN_SCHEMA_INVALID")

    def test_parser_coerces_small_model_shape_variants(self) -> None:
        text = (
            '{"user_goal":"比较地区","steps":[{"tool":"group_by_summary",'
            '"parameters":{"group_by":["column_0"],"aggregation":["sum(column_1)"],'
            '"sort_direction":"desc"},"expected_output":"汇总"}],'
            '"report_focus":"最高地区"}'
        )

        result = parse_plan_text(text)

        self.assertTrue(result.success)
        self.assertEqual(result.plan.report_focus, ["最高地区"])
        self.assertEqual(result.plan.steps[0].parameters["group_by"], "column_0")
        self.assertEqual(result.plan.steps[0].parameters["aggregation"], "sum")
        self.assertEqual(result.plan.steps[0].parameters["metric"], "column_1")

    def test_parser_fills_non_execution_output_description(self) -> None:
        text = (
            '{"user_goal":"比较地区","steps":[{"tool":"get_data_overview",'
            '"parameters":{}}],"report_focus":["数据质量"]}'
        )

        result = parse_plan_text(text)

        self.assertTrue(result.success)
        self.assertEqual(result.plan.steps[0].expected_output, "get_data_overview 执行结果")

    def test_validation_rejects_unknown_field_and_unsafe_sql_without_execution(self) -> None:
        unknown_field = valid_plan()
        unknown_field["steps"][0]["parameters"]["group_by"] = "不存在字段"
        result = validate_analysis_plan(unknown_field, self.engine)
        self.assertFalse(result.success)
        self.assertEqual(result.problems[0].code, "INVALID_FIELD")

        unsafe = valid_plan()
        unsafe["steps"] = [
            {
                "tool": "run_readonly_sql",
                "parameters": {"sql": "DELETE FROM uploaded_data"},
                "expected_output": "不应执行",
            }
        ]
        result = validate_analysis_plan(unsafe, self.engine)
        self.assertFalse(result.success)
        self.assertEqual(result.problems[0].code, "READ_ONLY_REQUIRED")

    def test_missing_sql_is_rejected_without_crashing(self) -> None:
        plan = valid_plan()
        plan["steps"] = [
            {
                "tool": "run_readonly_sql",
                "parameters": {},
                "expected_output": "查询结果",
            }
        ]

        normalized = normalize_plan_fields(plan, self.schema)
        result = validate_analysis_plan(normalized, self.engine)

        self.assertFalse(result.success)
        self.assertEqual(result.problems[0].code, "TOOL_PARAMETERS_INVALID")

    def test_validation_accepts_valid_plan(self) -> None:
        result = validate_analysis_plan(valid_plan(), self.engine)

        self.assertTrue(result.success)
        self.assertIsInstance(result.plan, AnalysisPlan)

    def test_planner_repairs_once_then_returns_valid_plan(self) -> None:
        client = FakePlannerClient([
            "不是 JSON",
            '{"user_goal":"比较地区","steps":[{"tool":"group_by_summary","parameters":{"group_by":"地区","metric":"销售额","aggregation":"sum"},"expected_output":"地区汇总"}],"report_focus":["最高地区"]}',
        ])
        planner = StructuredPlanner(client, "qwen2.5:3b", max_repairs=1)

        result = planner.create_plan("哪个地区销售额最高？", self.engine, self.schema)

        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(client.calls), 2)
        self.assertIn("上一次输出未通过系统校验", client.calls[1][1]["content"])

    def test_planner_stops_after_one_repair(self) -> None:
        client = FakePlannerClient(["不是 JSON", "还是不是 JSON"])
        planner = StructuredPlanner(client, "qwen2.5:3b", max_repairs=1)

        result = planner.create_plan("分析销售额", self.engine, self.schema)

        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(client.calls), 2)

    def test_prompt_contains_question_and_schema_without_rows(self) -> None:
        prompt = build_planner_prompt("哪个地区销售额最高？", self.schema)

        self.assertIn("哪个地区销售额最高", prompt)
        self.assertIn("销售额", prompt)
        self.assertIn("column_1", prompt)
        self.assertNotIn("100.0", prompt)

    def test_normalizes_ascii_field_aliases_to_real_column_names(self) -> None:
        plan = valid_plan()
        plan["steps"][0]["parameters"]["group_by"] = "column_0"
        plan["steps"][0]["parameters"]["metric"] = "column_1"

        normalized = normalize_plan_fields(plan, self.schema)

        self.assertEqual(normalized.steps[0].parameters["group_by"], "地区")
        self.assertEqual(normalized.steps[0].parameters["metric"], "销售额")


if __name__ == "__main__":
    unittest.main()
