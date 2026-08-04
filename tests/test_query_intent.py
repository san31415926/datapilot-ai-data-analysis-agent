import unittest

import pandas as pd

from src.ollama_client import ChatResult
from src.planner import StructuredPlanner, normalize_plan_fields
from src.query_engine import ReadOnlyQueryEngine
from src.query_intent import (
    QueryIntent,
    QueryIntentAnalyzer,
    build_intent_prompt,
    normalize_intent_fields,
    parse_intent_text,
)


class FakeIntentClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, model: str, messages: list[dict[str, str]], **kwargs: object) -> ChatResult:
        self.calls.append(messages)
        return ChatResult(model=model, content=self.response, thinking="", elapsed_ms=1)


class QueryIntentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = [
            {"name": "地区", "logical_type": "text", "role": "category", "sample_values": ["华东"]},
            {"name": "销售额", "logical_type": "numeric", "role": "measure", "sample_values": ["100"]},
            {"name": "成本", "logical_type": "numeric", "role": "measure", "sample_values": ["60"]},
            {"name": "渠道", "logical_type": "text", "role": "category", "sample_values": ["电商平台"]},
        ]

    def test_parse_accepts_common_small_model_aliases(self) -> None:
        result = parse_intent_text(
            '{"user_goal":"比较地区","analysis_type":"比较",'
            '"group_by":"column_0","metrics":["column_1"],"aggregation":"sum"}'
        )

        self.assertTrue(result.success)
        self.assertEqual(result.intent.intent_type, "group_summary")
        self.assertEqual(result.intent.dimensions, ["column_0"])
        self.assertEqual(result.intent.measures, ["column_1"])

    def test_profit_question_uses_revenue_and_cost(self) -> None:
        intent = QueryIntent(
            user_goal="能赚多少钱",
            intent_type="group_summary",
            measures=["利润"],
            aggregation="sum",
        )

        normalized = normalize_intent_fields(intent, self.schema)

        self.assertEqual(normalized.measures, ["column_1", "column_2"])
        self.assertEqual(normalized.calculation, "销售额减成本")

    def test_semantic_and_joined_field_references_are_resolved(self) -> None:
        intent = QueryIntent(
            user_goal="比较收入",
            intent_type="group_summary",
            dimensions=["区域"],
            measures=["收入"],
            aggregation="sum",
        )
        normalized = normalize_intent_fields(intent, self.schema)
        self.assertEqual(normalized.dimensions, ["column_0"])
        self.assertEqual(normalized.measures, ["column_1"])

        plan = {
            "user_goal": "比较地区",
            "steps": [
                {
                    "tool": "group_by_summary",
                    "parameters": {
                        "group_by": "column_0,column_3",
                        "metric": "column_1,column_2",
                        "aggregation": "sum",
                    },
                    "expected_output": "地区汇总",
                }
            ],
            "report_focus": ["最高地区"],
        }
        normalized_plan = normalize_plan_fields(plan, self.schema)
        self.assertEqual(normalized_plan.steps[0].parameters["group_by"], "地区")
        self.assertEqual(normalized_plan.steps[0].parameters["metric"], "销售额")

    def test_analyzer_calls_model_before_planning(self) -> None:
        client = FakeIntentClient(
            '{"user_goal":"比较各地区销售额","intent_type":"group_summary",'
            '"dimensions":["column_0"],"measures":["column_1"],"aggregation":"sum",'
            '"filters":[],"calculation":""}'
        )
        result = QueryIntentAnalyzer(client, "qwen2.5:3b").analyze("哪个区域收入最高？", self.schema)

        self.assertTrue(result.success)
        self.assertEqual(result.intent.dimensions, ["column_0"])
        self.assertEqual(result.intent.measures, ["column_1"])
        self.assertEqual(len(client.calls), 1)
        self.assertIn("哪个区域收入最高", client.calls[0][1]["content"])
        self.assertIn("samples=华东", build_intent_prompt("哪个区域收入最高？", self.schema))

    def test_planner_field_metadata_remains_safe_for_actual_query_engine(self) -> None:
        engine = ReadOnlyQueryEngine(
            pd.DataFrame({"地区": ["华东"], "销售额": [100.0], "成本": [60.0], "渠道": ["电商"]}),
            max_rows=100,
            max_result_bytes=100_000,
            timeout_seconds=2,
        )
        try:
            self.assertIn("地区", engine.column_names)
            self.assertTrue(engine.is_numeric_column("销售额"))
        finally:
            engine.close()

    def test_profit_intent_builds_and_executes_readonly_plan(self) -> None:
        engine = ReadOnlyQueryEngine(
            pd.DataFrame({"销售额": [100.0, 80.0], "成本": [60.0, 50.0]}),
            max_rows=100,
            max_result_bytes=100_000,
            timeout_seconds=2,
        )
        client = FakeIntentClient("不会被调用")
        intent = QueryIntent(
            user_goal="能赚多少钱",
            intent_type="detail",
            measures=["column_0", "column_1"],
            aggregation="sum",
            calculation="销售额减成本",
        )
        try:
            result = StructuredPlanner(client, "qwen2.5:3b").create_plan(
                "能赚多少钱",
                engine,
                [
                    {"name": "销售额", "logical_type": "numeric", "role": "measure"},
                    {"name": "成本", "logical_type": "numeric", "role": "measure"},
                ],
                intent=intent,
            )

            self.assertTrue(result.success)
            self.assertEqual(len(client.calls), 0)
            response = engine.execute(result.plan.steps[0].parameters["sql"])
            self.assertTrue(response.success)
            self.assertEqual(response.dataframe.iloc[0]["利润"], 70.0)
        finally:
            engine.close()

    def test_attendance_question_builds_filtered_detail_plan(self) -> None:
        schema = [
            {"name": "员工编号", "logical_type": "text", "role": "category", "sample_values": ["EMP-0001"]},
            {"name": "部门", "logical_type": "text", "role": "category", "sample_values": ["销售部"]},
            {"name": "日期", "logical_type": "date", "role": "category", "sample_values": ["2025-06-17"]},
            {"name": "出勤状态", "logical_type": "text", "role": "category", "sample_values": ["请假", "正常"]},
        ]
        client = FakeIntentClient(
            '{"user_goal":"谁没上班","intent_type":"unknown",'
            '"dimensions":[],"measures":[],"aggregation":"unknown",'
            '"filters":[],"calculation":""}'
        )
        intent_result = QueryIntentAnalyzer(client, "qwen2.5:3b").analyze("谁没上班", schema)
        self.assertTrue(intent_result.success)
        self.assertEqual(intent_result.intent.filters, ["column_3 != 正常"])
        self.assertEqual(intent_result.intent.calculation, "筛选出勤状态")

        engine = ReadOnlyQueryEngine(
            pd.DataFrame(
                {
                    "员工编号": ["EMP-0001", "EMP-0002", "EMP-0003"],
                    "部门": ["销售部", "产品部", "财务部"],
                    "日期": ["2025-06-17", "2025-06-25", "2025-06-11"],
                    "出勤状态": ["请假", "正常", "缺勤"],
                }
            ),
            max_rows=100,
            max_result_bytes=100_000,
            timeout_seconds=2,
        )
        try:
            result = StructuredPlanner(client, "qwen2.5:3b").create_plan(
                "谁没上班",
                engine,
                schema,
                intent=intent_result.intent,
            )
            self.assertTrue(result.success)
            self.assertEqual(len(client.calls), 1)
            response = engine.execute(result.plan.steps[0].parameters["sql"])
            self.assertTrue(response.success)
            self.assertEqual(response.dataframe["出勤状态"].tolist(), ["请假", "缺勤"])
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()
