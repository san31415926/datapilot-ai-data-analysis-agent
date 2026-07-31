"""DataPilot 的已校验分析计划执行器。"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.data_loader import LoadedDataset
from src.planner import AnalysisPlan, AnalysisStep, ToolName, validate_analysis_plan
from src.query_engine import ReadOnlyQueryEngine
from src.tools import (
    ToolExecutionRecord,
    ToolStatus,
    build_chart,
    detect_anomalies,
    get_data_overview,
    group_by_summary,
    run_readonly_sql,
)


class StepExecution(BaseModel):
    """一个计划步骤的执行结果和审计记录。"""

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(ge=0)
    tool: ToolName
    expected_output: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: ToolStatus
    record: ToolExecutionRecord
    result: dict[str, Any] = Field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "success"


class PlanExecutionResult(BaseModel):
    """一次计划执行的整体结果。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "rejected", "error"]
    plan: AnalysisPlan | None = None
    steps: list[StepExecution] = Field(default_factory=list)
    error_message: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "success"

    def to_evidence(self) -> list[dict[str, Any]]:
        """返回报告模型可以读取的有限证据，不包含完整执行日志堆栈。"""

        evidence: list[dict[str, Any]] = []
        for step in self.steps:
            if not step.success:
                continue
            result = dict(step.result)
            rows = result.get("rows")
            if isinstance(rows, list):
                result["rows"] = rows[:20]
            chart = result.get("chart")
            if isinstance(chart, dict) and isinstance(chart.get("data"), list):
                chart = dict(chart)
                chart["data"] = chart["data"][:20]
                result["chart"] = chart
            evidence.append(
                {
                    "step": step.step_index + 1,
                    "tool": step.tool,
                    "expected_output": step.expected_output,
                    "result": result,
                }
            )
        return evidence


def execute_analysis_plan(
    plan: AnalysisPlan | dict[str, Any],
    dataset: LoadedDataset,
    engine: ReadOnlyQueryEngine,
) -> PlanExecutionResult:
    """二次校验后按顺序执行计划，任一步失败即停止后续工具。"""

    try:
        parsed_plan = AnalysisPlan.model_validate(plan)
    except ValidationError:
        return PlanExecutionResult(status="rejected", error_message="分析计划结构不合法，未执行任何工具。")

    validation = validate_analysis_plan(parsed_plan, engine)
    if not validation.success or validation.plan is None:
        message = "；".join(problem.message for problem in validation.problems)
        return PlanExecutionResult(
            status="rejected",
            plan=parsed_plan,
            error_message=f"分析计划未通过执行前校验：{message}",
        )

    executions: list[StepExecution] = []
    for index, step in enumerate(validation.plan.steps):
        execution = _execute_step(index, step, dataset, engine)
        executions.append(execution)
        if not execution.success:
            return PlanExecutionResult(
                status="error",
                plan=validation.plan,
                steps=executions,
                error_message=f"第 {index + 1} 步工具执行失败，已停止后续步骤。",
            )

    return PlanExecutionResult(status="success", plan=validation.plan, steps=executions)


def _execute_step(
    index: int,
    step: AnalysisStep,
    dataset: LoadedDataset,
    engine: ReadOnlyQueryEngine,
) -> StepExecution:
    started_at = time.perf_counter()
    try:
        if step.tool == "get_data_overview":
            response = get_data_overview(dataset, step.parameters)
        elif step.tool == "run_readonly_sql":
            response = run_readonly_sql(engine, step.parameters)
        elif step.tool == "group_by_summary":
            response = group_by_summary(engine, step.parameters)
        elif step.tool == "detect_anomalies":
            response = detect_anomalies(engine, step.parameters)
        else:
            response = build_chart(dataset.dataframe, step.parameters)
    except Exception:
        record = ToolExecutionRecord(
            tool_name=step.tool,
            status="error",
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            input_summary=step.parameters,
            error_code="TOOL_EXECUTION_ERROR",
            error_message="工具执行过程中发生未处理错误。",
        )
        return StepExecution(
            step_index=index,
            tool=step.tool,
            expected_output=step.expected_output,
            parameters=step.parameters,
            status="error",
            record=record,
        )

    return StepExecution(
        step_index=index,
        tool=step.tool,
        expected_output=step.expected_output,
        parameters=step.parameters,
        status=response.status,
        record=response.record,
        result=response.model_dump(),
    )
