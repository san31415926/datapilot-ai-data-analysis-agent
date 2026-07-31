"""固定中文评估集的加载和结构化计划评分。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.planner import AnalysisPlan, ToolName

EvaluationCategory = Literal["总量", "分组", "趋势", "异常", "组合条件", "图表", "无关", "不可回答"]
ChartType = Literal["bar", "line", "pie"]


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=20)
    category: EvaluationCategory
    question: str = Field(min_length=1, max_length=300)
    expected_tools: list[ToolName] = Field(max_length=5)
    required_fields: list[str] = Field(default_factory=list, max_length=10)
    expected_result: dict[str, Any] = Field(default_factory=dict)
    should_refuse: bool
    expected_chart_type: ChartType | None = None
    acceptance_criteria: list[str] = Field(min_length=1, max_length=6)


class CaseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    passed: bool
    tools_match: bool
    fields_match: bool
    refusal_match: bool
    chart_match: bool
    actual_tools: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EvaluationDataError(ValueError):
    """固定评估集格式或内容不合法。"""


def load_evaluation_cases(path: str | Path | None = None) -> list[EvaluationCase]:
    """加载固定评估集，并检查数量、编号和分类覆盖。"""

    case_path = Path(path) if path is not None else _default_case_path()
    try:
        payload = json.loads(case_path.read_text(encoding="utf-8"))
        cases = [EvaluationCase.model_validate(item) for item in payload]
    except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise EvaluationDataError("固定评估集文件格式不合法。") from exc
    ids = [case.case_id for case in cases]
    if len(cases) != 20:
        raise EvaluationDataError(f"固定评估集必须包含 20 条问题，当前为 {len(cases)} 条。")
    if len(set(ids)) != len(ids):
        raise EvaluationDataError("固定评估集存在重复 case_id。")
    categories = {case.category for case in cases}
    required_categories = {"总量", "分组", "趋势", "异常", "组合条件", "图表", "无关", "不可回答"}
    if not required_categories.issubset(categories):
        missing = sorted(required_categories - categories)
        raise EvaluationDataError(f"固定评估集缺少分类：{', '.join(missing)}。")
    return cases


def evaluate_plan(
    case: EvaluationCase,
    plan: AnalysisPlan | dict[str, Any] | None,
    *,
    planning_success: bool,
) -> CaseEvaluation:
    """按工具、字段、拒答和图表类型分别检查一个计划。"""

    parsed_plan = AnalysisPlan.model_validate(plan) if plan is not None else None
    actual_tools = [step.tool for step in parsed_plan.steps] if parsed_plan else []
    serialized = json.dumps(parsed_plan.model_dump(), ensure_ascii=False) if parsed_plan else ""
    tools_match = all(tool in actual_tools for tool in case.expected_tools)
    fields_match = all(field in serialized for field in case.required_fields)
    refusal_match = (not planning_success) if case.should_refuse else planning_success and parsed_plan is not None
    chart_match = (
        case.expected_chart_type is None
        or any(
            step.tool == "build_chart"
            and step.parameters.get("chart_type") == case.expected_chart_type
            for step in parsed_plan.steps
        )
    )
    notes: list[str] = []
    if not tools_match:
        notes.append(f"期望工具：{case.expected_tools or '无'}；实际工具：{actual_tools or '无'}。")
    if not fields_match:
        notes.append(f"缺少关键字段：{', '.join(field for field in case.required_fields if field not in serialized)}。")
    if not refusal_match:
        notes.append("拒答状态与评估集预期不一致。")
    if not chart_match:
        notes.append(f"期望图表类型：{case.expected_chart_type}。")
    return CaseEvaluation(
        case_id=case.case_id,
        passed=tools_match and fields_match and refusal_match and chart_match,
        tools_match=tools_match,
        fields_match=fields_match,
        refusal_match=refusal_match,
        chart_match=chart_match,
        actual_tools=actual_tools,
        notes=notes,
    )


def summarize_evaluations(results: list[CaseEvaluation]) -> dict[str, Any]:
    """生成不把主观文本质量伪装成准确率的结构化汇总。"""

    passed = sum(result.passed for result in results)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "plan_contract_pass_rate": (passed / len(results)) if results else 0.0,
        "failed_cases": [result.case_id for result in results if not result.passed],
    }


def _default_case_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "evaluation_cases.json"
