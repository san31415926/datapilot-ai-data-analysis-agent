"""DataPilot 的 CSV/XLSX 文件加载、清洗和类型识别。"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")
NUMERIC_HEADER_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
DATE_COLUMN_HINTS = ("日期", "时间", "date", "time")
TYPE_INFERENCE_THRESHOLD = 0.8


class DataLoadError(ValueError):
    """可直接展示给用户的文件加载错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ColumnProfile:
    """一个字段的清洗名称、推断类型和转换失败摘要。"""

    original_name: str
    name: str
    logical_type: str
    non_empty_count: int
    conversion_failure_rows: tuple[int, ...]


@dataclass(frozen=True)
class LoadedDataset:
    """文件加载结果，同时保留原始值和类型转换后的数据。"""

    source_name: str
    file_format: str
    encoding: str | None
    raw_dataframe: pd.DataFrame
    dataframe: pd.DataFrame
    columns: tuple[ColumnProfile, ...]
    warnings: tuple[str, ...]


def load_file(
    file_name: str,
    content: bytes,
    *,
    max_upload_mb: int = 20,
    max_rows: int = 100_000,
) -> LoadedDataset:
    """加载一个受支持的 CSV/XLSX 文件。

    解析阶段不会调用模型，也不会执行任何 SQL。原始字段值保存在
    ``raw_dataframe``，可安全转换的日期和数值字段保存在 ``dataframe``。
    """

    suffix = Path(file_name or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DataLoadError("UNSUPPORTED_EXTENSION", "只支持 CSV 或 XLSX 文件。")
    if max_upload_mb <= 0 or max_rows <= 0:
        raise ValueError("max_upload_mb 和 max_rows 必须大于 0")
    if not content:
        raise DataLoadError("EMPTY_FILE", "文件内容为空，请选择有效文件。")
    if len(content) > max_upload_mb * 1024 * 1024:
        raise DataLoadError(
            "FILE_TOO_LARGE",
            f"文件大小超过 {max_upload_mb} MB 限制，请压缩文件或拆分数据。",
        )

    if suffix == ".csv":
        table, encoding = _read_csv(content)
        file_format = "csv"
    else:
        table = _read_xlsx(content)
        encoding = None
        file_format = "xlsx"

    raw_dataframe, original_names, warnings = _extract_data_rows(table)
    if len(raw_dataframe) > max_rows:
        raise DataLoadError(
            "TOO_MANY_ROWS",
            f"数据行数超过 {max_rows} 行限制，请先筛选或拆分文件。",
        )

    cleaned_names, name_warnings = _clean_column_names(original_names)
    raw_dataframe.columns = cleaned_names
    raw_dataframe = raw_dataframe.reset_index(drop=True)
    typed_dataframe, profiles = _infer_column_types(raw_dataframe, original_names)

    return LoadedDataset(
        source_name=Path(file_name).name,
        file_format=file_format,
        encoding=encoding,
        raw_dataframe=raw_dataframe,
        dataframe=typed_dataframe,
        columns=tuple(profiles),
        warnings=tuple(warnings + name_warnings),
    )


def _read_csv(content: bytes) -> tuple[pd.DataFrame, str]:
    decode_error = True
    for encoding in CSV_ENCODINGS:
        try:
            text = content.decode(encoding)
            decode_error = False
            break
        except UnicodeDecodeError:
            continue
    if decode_error:
        raise DataLoadError("ENCODING_ERROR", "无法识别 CSV 编码，请保存为 UTF-8 或 GBK 后重试。")

    try:
        table = pd.read_csv(StringIO(text), header=None, dtype=object)
    except pd.errors.EmptyDataError as exc:
        raise DataLoadError("EMPTY_FILE", "CSV 文件没有可读取内容。") from exc
    except (pd.errors.ParserError, ValueError) as exc:
        raise DataLoadError("PARSE_ERROR", "CSV 结构无法解析，请检查分隔符和引号。") from exc
    return table, encoding


def _read_xlsx(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_excel(BytesIO(content), header=None, dtype=object, engine="openpyxl")
    except (ValueError, ImportError, OSError, zipfile.BadZipFile) as exc:
        raise DataLoadError("PARSE_ERROR", "XLSX 文件无法解析，请确认文件没有损坏。") from exc


def _extract_data_rows(table: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    if table.empty or len(table.index) < 2:
        raise DataLoadError("NO_DATA", "文件只有表头或没有数据行。")

    header_values = table.iloc[0].tolist()
    original_names = [_stringify_header(value) for value in header_values]
    non_empty_names = [name for name in original_names if name]
    if not non_empty_names:
        raise DataLoadError("NO_HEADER", "未找到可用表头，请提供带字段名的文件。")
    if all(NUMERIC_HEADER_PATTERN.fullmatch(name) for name in non_empty_names):
        raise DataLoadError("NO_HEADER", "检测到表头疑似是数据行，请提供带字段名的文件。")

    data = table.iloc[1:].copy()
    non_empty_rows = ~data.apply(_row_is_empty, axis=1)
    data = data.loc[non_empty_rows]
    if data.empty:
        raise DataLoadError("NO_DATA", "文件只有表头或数据行为空。")

    warnings: list[str] = []
    if len(data.index) < len(table.index) - 1:
        warnings.append("已忽略文件末尾的空白行。")
    return data, original_names, warnings


def _clean_column_names(original_names: list[str]) -> tuple[list[str], list[str]]:
    cleaned: list[str] = []
    warnings: list[str] = []
    used: set[str] = set()
    duplicate_counts: dict[str, int] = {}

    for position, original_name in enumerate(original_names, start=1):
        base_name = re.sub(r"\s+", " ", original_name).strip()
        if not base_name or base_name.lower().startswith("unnamed"):
            base_name = f"未命名列{position}"
            warnings.append(f"第 {position} 列表头为空，已命名为“{base_name}”。")

        duplicate_counts[base_name] = duplicate_counts.get(base_name, 0) + 1
        candidate = base_name
        if candidate in used:
            candidate = f"{base_name}_{duplicate_counts[base_name]}"
            while candidate in used:
                duplicate_counts[base_name] += 1
                candidate = f"{base_name}_{duplicate_counts[base_name]}"
            warnings.append(f"字段“{base_name}”重复，已重命名为“{candidate}”。")

        used.add(candidate)
        cleaned.append(candidate)

    if not cleaned:
        raise DataLoadError("NO_HEADER", "未找到可用表头，请提供带字段名的文件。")
    return cleaned, warnings


def _infer_column_types(
    raw_dataframe: pd.DataFrame,
    original_names: list[str],
) -> tuple[pd.DataFrame, list[ColumnProfile]]:
    typed_dataframe = raw_dataframe.copy()
    profiles: list[ColumnProfile] = []

    for position, name in enumerate(raw_dataframe.columns):
        series = raw_dataframe[name]
        non_empty = series.map(_has_value)
        non_empty_count = int(non_empty.sum())
        text_series = series.where(non_empty).astype("string")
        numeric_values = pd.to_numeric(text_series, errors="coerce")
        date_values = pd.to_datetime(text_series, errors="coerce", format="mixed")
        numeric_ratio = _conversion_ratio(numeric_values, non_empty_count)
        date_ratio = _conversion_ratio(date_values, non_empty_count)

        if _looks_like_date(name, text_series) and date_ratio >= TYPE_INFERENCE_THRESHOLD:
            logical_type = "date"
            typed_dataframe[name] = date_values
            failures = _failure_rows(non_empty, date_values)
        elif numeric_ratio >= TYPE_INFERENCE_THRESHOLD:
            logical_type = "numeric"
            typed_dataframe[name] = numeric_values
            failures = _failure_rows(non_empty, numeric_values)
        else:
            logical_type = "text"
            typed_dataframe[name] = series.astype("string")
            failures = ()

        profiles.append(
            ColumnProfile(
                original_name=original_names[position],
                name=name,
                logical_type=logical_type,
                non_empty_count=non_empty_count,
                conversion_failure_rows=failures,
            )
        )

    return typed_dataframe, profiles


def _conversion_ratio(values: pd.Series, non_empty_count: int) -> float:
    if non_empty_count == 0:
        return 0.0
    return float(values.notna().sum()) / non_empty_count


def _failure_rows(non_empty: pd.Series, converted: pd.Series) -> tuple[int, ...]:
    return tuple(int(index) for index in converted.index[non_empty & converted.isna()])


def _looks_like_date(name: str, values: pd.Series) -> bool:
    lowered = name.lower()
    if any(hint in lowered for hint in DATE_COLUMN_HINTS):
        return True
    non_empty_values = [str(value) for value in values.dropna().tolist()]
    return any("-" in value or "/" in value for value in non_empty_values)


def _row_is_empty(row: pd.Series) -> bool:
    return all(not _has_value(value) for value in row.tolist())


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        return not bool(pd.isna(value)) and str(value).strip() != ""
    except (TypeError, ValueError):
        return str(value).strip() != ""


def _stringify_header(value: Any) -> str:
    return str(value).strip() if _has_value(value) else ""
