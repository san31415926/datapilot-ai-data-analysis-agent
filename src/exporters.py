"""DataPilot 的 CSV 和 Markdown 导出。"""

from __future__ import annotations

import json
from typing import Any, Sequence

import pandas as pd

from src.analysis_runner import PlanExecutionResult
from src.report_generator import ReportGenerationResult


def rows_to_csv_bytes(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[str] | None = None,
) -> bytes:
    """把工具返回的结构化行数据导出为 Excel 兼容的 UTF-8 CSV。"""

    frame = pd.DataFrame(list(rows))
    if columns is not None:
        frame = frame.reindex(columns=list(columns))
    return frame.to_csv(index=False).encode("utf-8-sig")


def build_markdown_report(
    question: str,
    execution: PlanExecutionResult,
    report_result: ReportGenerationResult | None = None,
) -> str:
    """生成包含报告正文、工具证据和审计摘要的 Markdown 文件。"""

    lines = [
        "# DataPilot 分析报告",
        "",
        f"- 分析问题：{question.strip() or '未提供'}",
        f"- 执行状态：{execution.status}",
        "",
    ]
    if report_result is not None and report_result.report is not None:
        report = report_result.report
        lines.extend([f"## {report.title}", "", report.summary, ""])
        if report.findings:
            lines.extend(["### 关键事实", "", *[f"- {item}" for item in report.findings], ""])
        if report.limitations:
            lines.extend(["### 数据限制", "", *[f"- {item}" for item in report.limitations], ""])
        lines.extend([f"- 报告引用工具步骤：{_join_steps(report.evidence_steps)}", ""])
    else:
        lines.extend(
            [
                "## 报告未生成",
                "",
                "当前没有可导出的模型报告，下面只保留真实工具执行记录，不能据此补充未经验证的结论。",
                "",
            ]
        )
        if report_result is not None and report_result.error_message:
            lines.extend([f"- 失败原因：{report_result.error_message}", ""])

    lines.extend(["## 工具证据", ""])
    if not execution.steps:
        lines.extend(["暂无工具执行结果。", ""])
    for step in execution.steps:
        lines.extend(
            [
                f"### 步骤 {step.step_index + 1}：{step.tool}",
                "",
                f"- 状态：{step.status}",
                f"- 预期输出：{step.expected_output}",
                f"- 耗时：{step.record.elapsed_ms} 毫秒",
            ]
        )
        if step.record.error_message:
            lines.append(f"- 错误：{step.record.error_message}")
        rows = step.result.get("rows")
        if isinstance(rows, list) and rows:
            lines.extend(["", _markdown_table(rows[:100]), ""])
        elif isinstance(step.result.get("overview"), dict):
            lines.extend(["", "```json", json.dumps(step.result["overview"], ensure_ascii=False, indent=2), "```", ""])
        elif isinstance(step.result.get("chart"), dict):
            chart = step.result["chart"]
            lines.extend(
                [
                    "",
                    f"- 图表类型：{chart.get('chart_type', '未知')}",
                    f"- 图表字段：{chart.get('x_field', '')} / {chart.get('y_field', '')}",
                    "",
                ]
            )
            chart_rows = chart.get("data")
            if isinstance(chart_rows, list) and chart_rows:
                lines.extend([_markdown_table(chart_rows[:100]), ""])
    lines.extend(["## 说明", "", "本文件由 DataPilot 根据本次上传数据的真实工具结果生成；合成样例数据不代表真实企业经营数据。", ""])
    return "\n".join(lines)


def _markdown_table(rows: Sequence[dict[str, Any]]) -> str:
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        return "暂无数据。"
    columns = [str(column) for column in frame.columns]
    header = "| " + " | ".join(_escape_cell(column) for column in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_escape_cell(row.get(column)) for column in columns) + " |"
        for row in frame.to_dict(orient="records")
    ]
    return "\n".join([header, separator, *body])


def _escape_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _join_steps(steps: Sequence[int]) -> str:
    return ", ".join(str(step) for step in steps) if steps else "无"
