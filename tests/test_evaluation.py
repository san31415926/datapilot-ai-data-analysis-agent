from __future__ import annotations

import unittest

from src.evaluation import evaluate_plan, load_evaluation_cases, summarize_evaluations
from src.planner import AnalysisPlan


class EvaluationTestCase(unittest.TestCase):
    def test_loads_twenty_cases_with_required_coverage(self) -> None:
        cases = load_evaluation_cases()

        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case.case_id for case in cases}), 20)
        self.assertEqual(
            {case.category for case in cases},
            {"总量", "分组", "趋势", "异常", "组合条件", "图表", "无关", "不可回答"},
        )

    def test_scores_valid_group_plan_and_chart_plan(self) -> None:
        case = next(case for case in load_evaluation_cases() if case.case_id == "E02")
        plan = AnalysisPlan(
            user_goal="比较地区销售额",
            steps=[
                {
                    "tool": "group_by_summary",
                    "parameters": {
                        "group_by": "地区",
                        "metric": "销售额",
                        "aggregation": "sum",
                        "sort_direction": "desc",
                        "limit": 10,
                    },
                    "expected_output": "地区销售额汇总",
                }
            ],
            report_focus=["最高地区", "销售额"],
        )

        result = evaluate_plan(case, plan, planning_success=True)

        self.assertTrue(result.passed)
        self.assertEqual(result.actual_tools, ["group_by_summary"])

    def test_scores_expected_refusal(self) -> None:
        case = next(case for case in load_evaluation_cases() if case.case_id == "E17")

        result = evaluate_plan(case, None, planning_success=False)

        self.assertTrue(result.passed)
        self.assertEqual(result.actual_tools, [])

    def test_summary_keeps_failed_case_ids(self) -> None:
        case = next(case for case in load_evaluation_cases() if case.case_id == "E02")
        result = evaluate_plan(case, None, planning_success=False)
        summary = summarize_evaluations([result])

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failed_cases"], ["E02"])


if __name__ == "__main__":
    unittest.main()
