"""DataPilot 基于真实工具证据的中文报告生成。"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.analysis_runner import PlanExecutionResult
from src.ollama_client import ChatResult, OllamaClient, OllamaClientError

REPORT_SYSTEM_PROMPT = """你是 DataPilot 的中文数据分析报告生成器。
只能根据用户问题和工具返回的真实证据写报告，不要使用证据之外的数据，不要编造数值。
只输出一个 JSON 对象，不要输出 Markdown 代码块、解释或思考过程。
JSON 必须包含 title、summary、findings、limitations、evidence_steps。
summary 是简短结论；findings 是事实列表，每条尽量标注“步骤 N”；limitations 写数据不足、工具失败或不能推断的部分；evidence_steps 是实际使用的成功工具步骤编号数组。
如果证据不能支持用户问题，summary 必须明确说明“当前数据无法支持该结论”，不要猜测。
"""


class AnalysisReport(BaseModel):
    """可展示、可检查的中文报告结构。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    findings: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=6)
    evidence_steps: list[int] = Field(min_length=1, max_length=8)


class ReportGenerationResult(BaseModel):
    """报告模型结果，区分真实模型报告和安全降级结果。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "fallback", "error"]
    backend: Literal["ollama", "fallback", "none"]
    model: str | None = None
    attempts: int = Field(ge=0)
    report: AnalysisReport | None = None
    error_message: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "success"


def build_report_prompt(question: str, execution: PlanExecutionResult) -> str:
    """只把成功工具的结构化结果传给报告模型。"""

    evidence = json.dumps(execution.to_evidence(), ensure_ascii=False, indent=2)
    focus = execution.plan.report_focus if execution.plan else []
    return (
        f"用户问题：{question.strip()}\n"
        f"报告重点：{json.dumps(focus, ensure_ascii=False)}\n"
        f"成功工具证据：\n{evidence}\n\n"
        "请根据以上证据生成 JSON 报告。evidence_steps 只能填写上面实际存在的步骤编号。"
    )


class ReportGenerator:
    """调用本地模型生成报告，失败时只返回明确的安全降级状态。"""

    def __init__(self, client: OllamaClient, model: str, *, max_repairs: int = 1) -> None:
        if max_repairs < 0 or max_repairs > 1:
            raise ValueError("阶段 10 最多允许修复一次")
        self.client = client
        self.model = model
        self.max_repairs = max_repairs

    def generate(self, question: str, execution: PlanExecutionResult) -> ReportGenerationResult:
        if not execution.success:
            return ReportGenerationResult(
                status="error",
                backend="none",
                model=self.model,
                attempts=0,
                error_message="工具执行未成功，不能生成基于证据的报告。",
            )

        prompt = build_report_prompt(question, execution)
        repair_context = ""
        error_message = "报告模型输出未通过结构化校验。"
        attempts = 0
        for attempt in range(self.max_repairs + 1):
            attempts = attempt + 1
            messages = [
                {"role": "system", "content": REPORT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt + repair_context},
            ]
            try:
                result = self.client.chat(
                    self.model,
                    messages,
                    response_format="json",
                    think=False,
                )
            except OllamaClientError as exc:
                error_message = exc.message
                break

            parsed = parse_report_text(result.content)
            if parsed is not None:
                invalid_steps = _invalid_evidence_steps(parsed, execution)
                if not invalid_steps:
                    return ReportGenerationResult(
                        status="success",
                        backend="ollama",
                        model=result.model,
                        attempts=attempt + 1,
                        report=parsed,
                    )
                error_message = f"报告引用了不存在的工具步骤：{invalid_steps}。"
            else:
                error_message = "报告模型输出不符合报告 JSON 结构。"

            if attempt < self.max_repairs:
                repair_context = (
                    "\n\n上一次报告未通过校验。请只输出修复后的 JSON。"
                    f"失败原因：{error_message}"
                )

        return ReportGenerationResult(
            status="fallback",
            backend="fallback",
            model=self.model,
            attempts=attempts,
            report=_fallback_report(execution, error_message),
            error_message=error_message,
        )


def parse_report_text(text: str) -> AnalysisReport | None:
    """从模型文本中提取报告 JSON，并执行 Pydantic 校验。"""

    payload = _extract_json_object(text)
    if payload is None:
        return None
    if isinstance(payload.get("findings"), str):
        payload["findings"] = [payload["findings"]]
    if isinstance(payload.get("limitations"), str):
        payload["limitations"] = [payload["limitations"]]
    if isinstance(payload.get("evidence_steps"), int):
        payload["evidence_steps"] = [payload["evidence_steps"]]
    try:
        return AnalysisReport.model_validate(payload)
    except ValidationError:
        return None


def _invalid_evidence_steps(report: AnalysisReport, execution: PlanExecutionResult) -> list[int]:
    valid_steps = {step.step_index + 1 for step in execution.steps if step.success}
    return sorted(set(report.evidence_steps) - valid_steps)


def _fallback_report(execution: PlanExecutionResult, error_message: str) -> AnalysisReport:
    successful_steps = [step.step_index + 1 for step in execution.steps if step.success]
    return AnalysisReport(
        title="数据分析结果（模型报告未生成）",
        summary="工具已执行成功，但本地模型报告未生成；请以页面中的真实工具结果为准。",
        limitations=["本次没有生成可信的模型分析结论。", error_message],
        evidence_steps=successful_steps,
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str) or not text.strip():
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for index, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return None
