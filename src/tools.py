"""DataPilot 的结构化数据分析工具。

工具只接收 Pydantic 参数，并将实际计算交给只读查询引擎或固定规则。
它们不执行任意 Python 表达式，也不让模型直接决定 SQL 的安全性。
"""

from __future__ import annotations

import hashlib
import time
from datetime import date, datetime
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.data_loader import LoadedDataset
from src.data_quality import DataQualityReport, analyze_quality
from src.query_engine import ReadOnlyQueryEngine

ToolStatus = Literal["success", "rejected", "error"]
Aggregation = Literal["count", "sum", "avg", "min", "max"]
SortDirection = Literal["asc", "desc"]


class ToolExecutionRecord(BaseModel):
    """一次工具调用的安全摘要，不保存完整数据结果。"""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    status: ToolStatus
    elapsed_ms: int = Field(ge=0)
    input_summary: dict[str, Any] = Field(default_factory=dict)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


class OverviewRequest(BaseModel):
    """数据概览工具参数。"""

    model_config = ConfigDict(extra="forbid")

    include_quality_issues: bool = True


class OverviewColumn(BaseModel):
    name: str
    logical_type: str
    role: str
    non_empty_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    min_value: Any = None
    max_value: Any = None
    sample_values: list[str] = Field(default_factory=list)


class OverviewIssue(BaseModel):
    code: str
    severity: str
    column: str | None = None
    message: str
    count: int = Field(ge=0)
    sample_rows: list[int] = Field(default_factory=list)


class DataOverview(BaseModel):
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    missing_cell_count: int = Field(ge=0)
    duplicate_row_count: int = Field(ge=0)
    duplicate_identifier_count: int = Field(ge=0)
    columns: list[OverviewColumn] = Field(default_factory=list)
    issues: list[OverviewIssue] = Field(default_factory=list)


class OverviewToolResponse(BaseModel):
    status: ToolStatus
    overview: DataOverview | None = None
    record: ToolExecutionRecord

    @property
    def success(self) -> bool:
        return self.status == "success"


class ReadonlySQLRequest(BaseModel):
    """只读 SQL 工具参数。"""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=10_000)


class SQLToolResponse(BaseModel):
    status: ToolStatus
    sql: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(ge=0)
    record: ToolExecutionRecord

    @property
    def success(self) -> bool:
        return self.status == "success"


class GroupBySummaryRequest(BaseModel):
    """分组统计参数，字段和聚合函数由工具白名单控制。"""

    model_config = ConfigDict(extra="forbid")

    group_by: str = Field(min_length=1, max_length=100)
    metric: str | None = Field(default=None, min_length=1, max_length=100)
    aggregation: Aggregation = "count"
    sort_direction: SortDirection = "desc"
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def require_metric_for_aggregation(self) -> "GroupBySummaryRequest":
        if self.aggregation != "count" and not self.metric:
            raise ValueError("sum、avg、min 和 max 必须指定 metric 字段")
        return self


class AnomalyRequest(BaseModel):
    """异常检测参数，当前只提供可解释的 IQR 规则。"""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=100)
    method: Literal["iqr"] = "iqr"
    iqr_multiplier: float = Field(default=1.5, ge=0.1, le=10.0)
    limit: int = Field(default=50, ge=1, le=100)


class ChartRequest(BaseModel):
    """图表配置参数。"""

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "pie"] = "bar"
    x_field: str = Field(min_length=1, max_length=100)
    y_field: str = Field(min_length=1, max_length=100)
    title: str = Field(default="数据分析图表", min_length=1, max_length=120)
    limit: int = Field(default=100, ge=1, le=200)


class ChartSpec(BaseModel):
    chart_type: Literal["bar", "line", "pie"]
    title: str
    x_field: str
    y_field: str
    data: list[dict[str, Any]] = Field(default_factory=list)


class ChartToolResponse(BaseModel):
    status: ToolStatus
    chart: ChartSpec | None = None
    record: ToolExecutionRecord

    @property
    def success(self) -> bool:
        return self.status == "success"


def get_data_overview(
    dataset: LoadedDataset,
    request: OverviewRequest | dict[str, Any] | None = None,
) -> OverviewToolResponse:
    """返回字段、行数、类型和质量摘要。"""

    started_at = time.perf_counter()
    parsed = OverviewRequest.model_validate(request or {})
    try:
        quality = analyze_quality(dataset)
        overview = _overview_from_report(quality, parsed.include_quality_issues)
        record = _record(
            "get_data_overview",
            started_at,
            "success",
            {"include_quality_issues": parsed.include_quality_issues},
            {"row_count": overview.row_count, "column_count": overview.column_count},
        )
        return OverviewToolResponse(status="success", overview=overview, record=record)
    except Exception:
        record = _record(
            "get_data_overview",
            started_at,
            "error",
            {"include_quality_issues": parsed.include_quality_issues},
            error_code="OVERVIEW_ERROR",
            error_message="数据概览工具执行失败。",
        )
        return OverviewToolResponse(status="error", record=record)


def run_readonly_sql(
    engine: ReadOnlyQueryEngine,
    request: ReadonlySQLRequest | dict[str, Any],
) -> SQLToolResponse:
    """执行一条经过安全校验的只读 SQL。"""

    started_at = time.perf_counter()
    parsed = ReadonlySQLRequest.model_validate(request)
    try:
        response = engine.execute(parsed.sql)
        execution = response.execution
        rows = _frame_records(response.dataframe)
        record = _record(
            "run_readonly_sql",
            started_at,
            execution.status,
            _sql_summary(parsed.sql),
            {
                "row_count": execution.row_count,
                "result_bytes": execution.result_bytes,
                "columns": list(response.dataframe.columns) if response.dataframe is not None else [],
            },
            error_code=execution.error_code,
            error_message=execution.error_message,
        )
        return SQLToolResponse(
            status=execution.status,
            sql=execution.sql,
            columns=list(response.dataframe.columns) if response.dataframe is not None else [],
            rows=rows,
            row_count=execution.row_count,
            record=record,
        )
    except Exception:
        record = _record(
            "run_readonly_sql",
            started_at,
            "error",
            _sql_summary(parsed.sql),
            error_code="TOOL_ERROR",
            error_message="只读 SQL 工具执行失败。",
        )
        return SQLToolResponse(
            status="error",
            sql=parsed.sql,
            row_count=0,
            record=record,
        )


def group_by_summary(
    engine: ReadOnlyQueryEngine,
    request: GroupBySummaryRequest | dict[str, Any],
) -> SQLToolResponse:
    """按白名单字段和聚合函数生成分组统计查询。"""

    started_at = time.perf_counter()
    parsed = GroupBySummaryRequest.model_validate(request)
    input_summary = parsed.model_dump()
    field_error = _validate_engine_fields(engine, parsed.group_by, parsed.metric)
    if field_error:
        return _sql_tool_error("group_by_summary", started_at, input_summary, field_error[0], field_error[1])
    if parsed.aggregation != "count" and not engine.is_numeric_column(parsed.metric or ""):
        return _sql_tool_error(
            "group_by_summary",
            started_at,
            input_summary,
            "METRIC_NOT_NUMERIC",
            "sum、avg、min 和 max 只能用于数值字段。",
        )

    group_field = _quote_identifier(parsed.group_by)
    alias = _aggregation_alias(parsed.metric, parsed.aggregation)
    if parsed.aggregation == "count":
        aggregate_sql = f'COUNT(*) AS "{alias}"'
    else:
        aggregate_sql = f'{parsed.aggregation.upper()}({_quote_identifier(parsed.metric or "")}) AS "{alias}"'
    sql = (
        f'SELECT {group_field} AS "分组值", {aggregate_sql} '
        f'FROM {engine.table_name} GROUP BY {group_field} '
        f'ORDER BY "{alias}" {parsed.sort_direction.upper()} LIMIT {parsed.limit}'
    )
    response = run_readonly_sql(engine, {"sql": sql})
    return _rename_tool_record(response, "group_by_summary", started_at, input_summary)


def detect_anomalies(
    engine: ReadOnlyQueryEngine,
    request: AnomalyRequest | dict[str, Any],
) -> SQLToolResponse:
    """使用 IQR 规则返回异常数值及其偏离程度。"""

    started_at = time.perf_counter()
    parsed = AnomalyRequest.model_validate(request)
    input_summary = parsed.model_dump()
    field_error = _validate_engine_fields(engine, parsed.metric)
    if field_error:
        return _sql_tool_error("detect_anomalies", started_at, input_summary, field_error[0], field_error[1])
    if not engine.is_numeric_column(parsed.metric):
        return _sql_tool_error(
            "detect_anomalies",
            started_at,
            input_summary,
            "METRIC_NOT_NUMERIC",
            "异常检测只能用于数值字段。",
        )

    metric = _quote_identifier(parsed.metric)
    multiplier = f"{parsed.iqr_multiplier:.6f}"
    sql = (
        "WITH stats AS ("
        f"SELECT quantile_cont({metric}, 0.25) AS __q1, "
        f"quantile_cont({metric}, 0.75) AS __q3 FROM {engine.table_name} "
        f"WHERE {metric} IS NOT NULL), "
        "scored AS ("
        f"SELECT data.*, "
        f"stats.__q1 - {multiplier} * (stats.__q3 - stats.__q1) AS \"异常下界\", "
        f"stats.__q3 + {multiplier} * (stats.__q3 - stats.__q1) AS \"异常上界\", "
        f"CASE WHEN data.{metric} < stats.__q1 - {multiplier} * (stats.__q3 - stats.__q1) "
        f"OR data.{metric} > stats.__q3 + {multiplier} * (stats.__q3 - stats.__q1) "
        "THEN TRUE ELSE FALSE END AS \"是否异常\", "
        f"CASE WHEN data.{metric} < stats.__q1 - {multiplier} * (stats.__q3 - stats.__q1) "
        f"THEN abs(data.{metric} - (stats.__q1 - {multiplier} * (stats.__q3 - stats.__q1))) "
        f"WHEN data.{metric} > stats.__q3 + {multiplier} * (stats.__q3 - stats.__q1) "
        f"THEN abs(data.{metric} - (stats.__q3 + {multiplier} * (stats.__q3 - stats.__q1))) "
        "ELSE 0 END AS \"异常偏离\" "
        f"FROM {engine.table_name} AS data CROSS JOIN stats) "
        f'SELECT * FROM scored WHERE "是否异常" ORDER BY "异常偏离" DESC LIMIT {parsed.limit}'
    )
    response = run_readonly_sql(engine, {"sql": sql})
    return _rename_tool_record(response, "detect_anomalies", started_at, input_summary)


def build_chart(
    dataframe: pd.DataFrame,
    request: ChartRequest | dict[str, Any],
) -> ChartToolResponse:
    """根据结构化结果生成图表配置，不接受模型生成的 JavaScript。"""

    started_at = time.perf_counter()
    parsed = ChartRequest.model_validate(request)
    input_summary = parsed.model_dump()
    columns = {str(column) for column in dataframe.columns}
    missing = [field for field in (parsed.x_field, parsed.y_field) if field not in columns]
    if missing:
        return _chart_tool_error(
            started_at,
            input_summary,
            "INVALID_FIELD",
            f"图表字段不存在：{', '.join(missing)}。",
        )
    if not pd.api.types.is_numeric_dtype(dataframe[parsed.y_field]):
        return _chart_tool_error(
            started_at,
            input_summary,
            "Y_FIELD_NOT_NUMERIC",
            "图表纵轴字段必须是数值字段。",
        )

    chart = ChartSpec(
        chart_type=parsed.chart_type,
        title=parsed.title,
        x_field=parsed.x_field,
        y_field=parsed.y_field,
        data=_frame_records(dataframe.loc[:, [parsed.x_field, parsed.y_field]].head(parsed.limit)),
    )
    record = _record(
        "build_chart",
        started_at,
        "success",
        input_summary,
        {"row_count": len(chart.data), "chart_type": chart.chart_type},
    )
    return ChartToolResponse(status="success", chart=chart, record=record)


def _overview_from_report(report: DataQualityReport, include_issues: bool) -> DataOverview:
    return DataOverview(
        row_count=report.row_count,
        column_count=report.column_count,
        missing_cell_count=report.missing_cell_count,
        duplicate_row_count=report.duplicate_row_count,
        duplicate_identifier_count=report.duplicate_identifier_count,
        columns=[
            OverviewColumn(
                name=column.name,
                logical_type=column.logical_type,
                role=column.role,
                non_empty_count=column.non_empty_count,
                missing_count=column.missing_count,
                unique_count=column.unique_count,
                min_value=_json_safe(column.min_value),
                max_value=_json_safe(column.max_value),
                sample_values=list(column.sample_values),
            )
            for column in report.columns
        ],
        issues=[
            OverviewIssue(
                code=issue.code,
                severity=issue.severity,
                column=issue.column,
                message=issue.message,
                count=issue.count,
                sample_rows=list(issue.sample_rows),
            )
            for issue in report.issues
        ]
        if include_issues
        else [],
    )


def _validate_engine_fields(
    engine: ReadOnlyQueryEngine,
    *fields: str | None,
) -> tuple[str, str] | None:
    missing = [field for field in fields if field and field not in engine.column_names]
    if missing:
        return "INVALID_FIELD", f"字段不存在：{', '.join(missing)}。"
    return None


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _aggregation_alias(metric: str | None, aggregation: Aggregation) -> str:
    labels = {
        "count": "记录数",
        "sum": "合计",
        "avg": "平均值",
        "min": "最小值",
        "max": "最大值",
    }
    return labels[aggregation] if aggregation == "count" else f"{metric}{labels[aggregation]}"


def _sql_summary(sql: str) -> dict[str, Any]:
    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12]
    return {"sql_sha256_12": digest, "sql_length": len(sql)}


def _frame_records(dataframe: pd.DataFrame | None) -> list[dict[str, Any]]:
    if dataframe is None:
        return []
    return [
        {str(key): _json_safe(value) for key, value in row.items()}
        for row in dataframe.to_dict(orient="records")
    ]


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _record(
    tool_name: str,
    started_at: float,
    status: ToolStatus,
    input_summary: dict[str, Any],
    result_summary: dict[str, Any] | None = None,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        tool_name=tool_name,
        status=status,
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        input_summary=input_summary,
        result_summary=result_summary or {},
        error_code=error_code,
        error_message=error_message,
    )


def _sql_tool_error(
    tool_name: str,
    started_at: float,
    input_summary: dict[str, Any],
    error_code: str,
    error_message: str,
) -> SQLToolResponse:
    record = _record(
        tool_name,
        started_at,
        "rejected",
        input_summary,
        error_code=error_code,
        error_message=error_message,
    )
    return SQLToolResponse(status="rejected", sql="", row_count=0, record=record)


def _rename_tool_record(
    response: SQLToolResponse,
    tool_name: str,
    started_at: float,
    input_summary: dict[str, Any],
) -> SQLToolResponse:
    response.record = _record(
        tool_name,
        started_at,
        response.status,
        input_summary,
        {"row_count": response.row_count, "columns": response.columns},
        error_code=response.record.error_code,
        error_message=response.record.error_message,
    )
    return response


def _chart_tool_error(
    started_at: float,
    input_summary: dict[str, Any],
    error_code: str,
    error_message: str,
) -> ChartToolResponse:
    record = _record(
        "build_chart",
        started_at,
        "rejected",
        input_summary,
        error_code=error_code,
        error_message=error_message,
    )
    return ChartToolResponse(status="rejected", record=record)
