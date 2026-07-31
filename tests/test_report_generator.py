import unittest

from src.analysis_runner import execute_analysis_plan
from src.data_loader import load_file
from src.ollama_client import ChatResult, OllamaClientError
from src.query_engine import ReadOnlyQueryEngine
from src.report_generator import ReportGenerator, build_report_prompt, parse_report_text


PLAN = {
    "user_goal": "比较各地区销售额",
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
    "report_focus": ["最高地区", "销售额"],
}


class FakeReportClient:
    def __init__(self, responses: list[str] | None = None, error: Exception | None = None) -> None:
        self.responses = responses or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def chat(self, model: str, messages: list[dict[str, str]], **kwargs: object) -> ChatResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return ChatResult(model=model, content=self.responses.pop(0), thinking="", elapsed_ms=1)


class ReportGeneratorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        dataset = load_file(
            "report.csv",
            "地区,销售额\n华东,1200\n华南,900\n".encode("utf-8"),
        )
        self.dataset = dataset
        self.engine = ReadOnlyQueryEngine(
            dataset.dataframe,
            max_rows=100,
            max_result_bytes=100_000,
            timeout_seconds=2,
        )
        self.execution = execute_analysis_plan(PLAN, dataset, self.engine)

    def tearDown(self) -> None:
        self.engine.close()

    def test_generates_report_from_tool_evidence(self) -> None:
        client = FakeReportClient(
            [
                '{"title":"地区销售额分析","summary":"华东销售额最高。",'
                '"findings":["步骤 1 的分组统计显示华东最高"],'
                '"limitations":["数据为样例数据"],"evidence_steps":[1]}'
            ]
        )

        result = ReportGenerator(client, "qwen2.5:3b").generate(
            "哪个地区销售额最高？", self.execution
        )

        self.assertTrue(result.success)
        self.assertEqual(result.backend, "ollama")
        self.assertEqual(result.report.evidence_steps, [1])
        self.assertEqual(client.calls[0]["response_format"], "json")
        self.assertFalse(client.calls[0]["think"])

    def test_invalid_evidence_causes_safe_fallback(self) -> None:
        invalid = (
            '{"title":"分析","summary":"有结论。","findings":[],'
            '"limitations":[],"evidence_steps":[99]}'
        )
        client = FakeReportClient([invalid, invalid])

        result = ReportGenerator(client, "qwen2.5:3b").generate("分析销售额", self.execution)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "fallback")
        self.assertEqual(result.backend, "fallback")
        self.assertIn("模型报告未生成", result.report.summary)
        self.assertEqual(len(client.calls), 2)

    def test_model_error_does_not_create_report_claim(self) -> None:
        client = FakeReportClient(
            error=OllamaClientError("OLLAMA_TIMEOUT", "模型生成超时。")
        )

        result = ReportGenerator(client, "qwen2.5:3b").generate("分析销售额", self.execution)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "fallback")
        self.assertEqual(result.report.findings, [])
        self.assertIn("模型生成超时", result.error_message)

    def test_failed_execution_does_not_call_report_model(self) -> None:
        failed = self.execution.model_copy(update={"status": "error"})
        client = FakeReportClient(["{}"])

        result = ReportGenerator(client, "qwen2.5:3b").generate("分析销售额", failed)

        self.assertEqual(result.status, "error")
        self.assertEqual(result.backend, "none")
        self.assertEqual(client.calls, [])

    def test_parser_accepts_scalar_lists_and_rejects_extra_fields(self) -> None:
        result = parse_report_text(
            '{"title":"分析","summary":"结论","findings":"事实",'
            '"limitations":"限制","evidence_steps":1}'
        )
        self.assertEqual(result.findings, ["事实"])
        self.assertEqual(result.limitations, ["限制"])
        self.assertEqual(result.evidence_steps, [1])
        self.assertIsNone(parse_report_text('{"title":"分析","unknown":1}'))

    def test_report_prompt_contains_only_structured_evidence(self) -> None:
        prompt = build_report_prompt("哪个地区销售额最高？", self.execution)

        self.assertIn("华东", prompt)
        self.assertIn("成功工具证据", prompt)
        self.assertNotIn("Ollama 地址", prompt)


if __name__ == "__main__":
    unittest.main()
