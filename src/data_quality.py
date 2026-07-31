"""DataPilot 的数据概览、字段角色识别和质量检查。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.data_loader import ColumnProfile, LoadedDataset

ID_HINTS = ("编号", "订单号", "id", "编码", "标识")
DATE_HINTS = ("日期", "时间", "date", "time")
MEASURE_HINTS = ("金额", "销售额", "收入", "成本", "价格", "单价", "数量", "折扣", "amount", "revenue", "cost", "price")
CATEGORY_HINTS = ("地区", "渠道", "类别", "类型", "状态", "名称", "分类")


@dataclass(frozen=True)
class ColumnQuality:
    """一个字段的统计摘要和业务角色提示。"""

    name: str
    logical_type: str
    role: str
    non_empty_count: int
    missing_count: int
    unique_count: int
    min_value: Any
    max_value: Any
    sample_values: tuple[str, ...]


@dataclass(frozen=True)
class QualityIssue:
    """一个可展示、可传给后续 Agent 的质量问题。"""

    code: str
    severity: str
    column: str | None
    message: str
    count: int
    sample_rows: tuple[int, ...]


@dataclass(frozen=True)
class DataQualityReport:
    """一份不修改原始 DataFrame 的数据质量报告。"""

    row_count: int
    column_count: int
    missing_cell_count: int
    duplicate_row_count: int
    duplicate_identifier_count: int
    columns: tuple[ColumnQuality, ...]
    issues: tuple[QualityIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        """返回后续 Agent 和日志可以使用的 JSON 兼容摘要。"""

        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "missing_cell_count": self.missing_cell_count,
            "duplicate_row_count": self.duplicate_row_count,
            "duplicate_identifier_count": self.duplicate_identifier_count,
            "columns": [
                {
                    "name": column.name,
                    "logical_type": column.logical_type,
                    "role": column.role,
                    "non_empty_count": column.non_empty_count,
                    "missing_count": column.missing_count,
                    "unique_count": column.unique_count,
                    "min_value": column.min_value,
                    "max_value": column.max_value,
                    "sample_values": list(column.sample_values),
                }
                for column in self.columns
            ],
            "issues": [
                {
                    "code": issue.code,
                    "severity": issue.severity,
                    "column": issue.column,
                    "message": issue.message,
                    "count": issue.count,
                    "sample_rows": list(issue.sample_rows),
                }
                for issue in self.issues
            ],
        }


def analyze_quality(dataset: LoadedDataset) -> DataQualityReport:
    """根据加载结果生成概览和质量问题，不改变输入数据。"""

    dataframe = dataset.dataframe
    profiles = {profile.name: profile for profile in dataset.columns}
    columns: list[ColumnQuality] = []
    issues: list[QualityIssue] = []

    for name in dataframe.columns:
        profile = profiles[name]
        series = dataframe[name]
        missing_mask = _missing_mask(series)
        missing_count = int(missing_mask.sum())
        non_empty_count = int(len(series) - missing_count)
        unique_count = int(series.loc[~missing_mask].nunique(dropna=True))
        role = _infer_role(profile, unique_count, len(dataframe))
        min_value, max_value = _range_values(series, profile.logical_type, missing_mask)
        sample_values = _sample_values(series, missing_mask)

        columns.append(
            ColumnQuality(
                name=name,
                logical_type=profile.logical_type,
                role=role,
                non_empty_count=non_empty_count,
                missing_count=missing_count,
                unique_count=unique_count,
                min_value=min_value,
                max_value=max_value,
                sample_values=sample_values,
            )
        )

        if missing_count:
            issues.append(
                QualityIssue(
                    code="MISSING_VALUES",
                    severity="warning",
                    column=name,
                    message=f"字段“{name}”存在 {missing_count} 个缺失值。",
                    count=missing_count,
                    sample_rows=_sample_rows(missing_mask),
                )
            )

        if profile.logical_type == "date" and missing_count:
            issues.append(
                QualityIssue(
                    code="MISSING_DATE",
                    severity="warning",
                    column=name,
                    message=f"日期字段“{name}”存在缺失值，趋势分析可能不完整。",
                    count=missing_count,
                    sample_rows=_sample_rows(missing_mask),
                )
            )

        if profile.logical_type == "numeric":
            negative_mask = series.lt(0).fillna(False)
            negative_count = int(negative_mask.sum())
            if negative_count:
                issues.append(
                    QualityIssue(
                        code="NEGATIVE_VALUES",
                        severity="warning",
                        column=name,
                        message=f"数值字段“{name}”存在 {negative_count} 个负数。",
                        count=negative_count,
                        sample_rows=_sample_rows(negative_mask),
                    )
                )

    duplicate_row_mask = dataframe.duplicated(keep="first")
    duplicate_row_count = int(duplicate_row_mask.sum())
    if duplicate_row_count:
        issues.append(
            QualityIssue(
                code="DUPLICATE_ROWS",
                severity="warning",
                column=None,
                message=f"发现 {duplicate_row_count} 行与前面记录完全重复。",
                count=duplicate_row_count,
                sample_rows=_sample_rows(duplicate_row_mask),
            )
        )

    duplicate_identifier_count, identifier_column = _duplicate_identifier_count(dataframe, columns)
    if duplicate_identifier_count:
        issues.append(
            QualityIssue(
                code="DUPLICATE_IDENTIFIER",
                severity="warning",
                column=identifier_column,
                message=f"标识字段“{identifier_column}”存在 {duplicate_identifier_count} 个重复值。",
                count=duplicate_identifier_count,
                sample_rows=_duplicate_identifier_rows(dataframe, identifier_column),
            )
        )

    return DataQualityReport(
        row_count=len(dataframe),
        column_count=len(dataframe.columns),
        missing_cell_count=sum(column.missing_count for column in columns),
        duplicate_row_count=duplicate_row_count,
        duplicate_identifier_count=duplicate_identifier_count,
        columns=tuple(columns),
        issues=tuple(issues),
    )


def _infer_role(profile: ColumnProfile, unique_count: int, row_count: int) -> str:
    name = profile.name.lower()
    if any(hint in name for hint in ID_HINTS):
        return "id"
    if profile.logical_type == "date" or any(hint in name for hint in DATE_HINTS):
        return "date"
    if profile.logical_type == "numeric" or any(hint in name for hint in MEASURE_HINTS):
        return "measure"
    if any(hint in name for hint in CATEGORY_HINTS) or unique_count <= max(20, row_count // 5):
        return "category"
    return "text"


def _range_values(series: pd.Series, logical_type: str, missing_mask: pd.Series) -> tuple[Any, Any]:
    values = series.loc[~missing_mask]
    if values.empty or logical_type == "text":
        return None, None
    if logical_type == "date":
        minimum = pd.to_datetime(values, errors="coerce").min()
        maximum = pd.to_datetime(values, errors="coerce").max()
        return _date_value(minimum), _date_value(maximum)
    numeric_values = pd.to_numeric(values, errors="coerce").dropna()
    if numeric_values.empty:
        return None, None
    return _number_value(numeric_values.min()), _number_value(numeric_values.max())


def _sample_values(series: pd.Series, missing_mask: pd.Series) -> tuple[str, ...]:
    values = series.loc[~missing_mask].head(3)
    return tuple(_display_value(value) for value in values.tolist())


def _missing_mask(series: pd.Series) -> pd.Series:
    mask = series.isna()
    if pd.api.types.is_string_dtype(series):
        mask = mask | series.fillna("").astype(str).str.strip().eq("")
    return mask


def _sample_rows(mask: pd.Series) -> tuple[int, ...]:
    return tuple(int(index) for index in mask.index[mask][:5])


def _duplicate_identifier_count(
    dataframe: pd.DataFrame,
    columns: list[ColumnQuality],
) -> tuple[int, str | None]:
    for column in columns:
        if column.role != "id":
            continue
        values = dataframe[column.name].loc[~_missing_mask(dataframe[column.name])]
        return int(values.duplicated(keep="first").sum()), column.name
    return 0, None


def _duplicate_identifier_rows(dataframe: pd.DataFrame, column: str | None) -> tuple[int, ...]:
    if column is None:
        return ()
    series = dataframe[column]
    mask = series.duplicated(keep="first") & ~_missing_mask(series)
    return _sample_rows(mask)


def _number_value(value: Any) -> int | float | None:
    if pd.isna(value):
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def _date_value(value: Any) -> str | None:
    if pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _display_value(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)
