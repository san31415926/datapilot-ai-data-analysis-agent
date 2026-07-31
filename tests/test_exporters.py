from __future__ import annotations

import unittest

from src.analysis_runner import PlanExecutionResult, StepExecution
from src.exporters import build_markdown_report, rows_to_csv_bytes
from src.report_generator import AnalysisReport, ReportGenerationResult
from src.tools import ToolExecutionRecord


class ExportersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        record = ToolExecutionRecord(
            tool_name="group_by_summary",
            status="success",
            elapsed_ms=12,
            input_summary={"group_by": "地区"},
            result_summary={"row_count": 2},
        )
        self.execution = PlanExecutionResult(
            status="success",
            steps=[
                StepExecution(
                    step_index=0,
                    tool="group_by_summary",
                    expected_output="各地区销售额汇总",
                    parameters={"group_by": "地区"},
                    status="success",
                    record=record,
                    result={
                        "status": "success",
                        "rows": [
                            {"分组值": "华东", "销售额合计": 1200.0},
                            {"分组值": "华南", "销售额合计": 900.0},
                        ],
                    },
                )
            ],
        )
        self.report_result = ReportGenerationResult(
            status="success",
            backend="ollama",
            model="qwen2.5:3b",
            attempts=1,
            report=AnalysisReport(
                title="地区销售额分析",
                summary="华东销售额最高。",
                findings=["步骤 1 的分组统计显示华东最高。"],
                limitations=["数据为合成样例。"],
                evidence_steps=[1],
            ),
        )

    def test_rows_csv_is_excel_compatible(self) -> None:
        result = rows_to_csv_bytes(
            [{"地区": "华东", "销售额": 1200}],
            columns=["地区", "销售额"],
        )

        self.assertTrue(result.startswith(b"\xef\xbb\xbf"))
        self.assertIn("华东,1200", result.decode("utf-8-sig"))

    def test_markdown_contains_report_evidence_and_audit(self) -> None:
        markdown = build_markdown_report(
            "哪个地区销售额最高？",
            self.execution,
            self.report_result,
        )

        self.assertIn("地区销售额分析", markdown)
        self.assertIn("华东", markdown)
        self.assertIn("报告引用工具步骤：1", markdown)
        self.assertIn("耗时：12 毫秒", markdown)
        self.assertIn("销售额合计", markdown)


if __name__ == "__main__":
    unittest.main()
