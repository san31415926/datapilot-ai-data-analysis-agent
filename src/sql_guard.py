"""DataPilot 的只读 SQL 校验。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import duckdb

FORBIDDEN_SOURCE_PATTERNS = (
    re.compile(r"\b(?:read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|glob)\s*\(", re.IGNORECASE),
    re.compile(r"\b(?:information_schema|pg_catalog|sqlite_master|duckdb_[a-z_]+)\b", re.IGNORECASE),
    re.compile(r"\b(?:attach|detach|use)\b", re.IGNORECASE),
    re.compile(r"\b(?:from|join)\s*['\"]", re.IGNORECASE),
)
PLAN_FUNCTION_PATTERN = re.compile(r"Function:\s*([A-Z0-9_]+)")


@dataclass(frozen=True)
class SQLValidation:
    """SQL 校验结果。"""

    approved: bool
    code: str
    message: str
    normalized_sql: str = ""


class SQLGuard:
    """只允许单条 SELECT，并把表绑定到当前上传数据。"""

    def __init__(self, allowed_table: str = "uploaded_data", max_sql_length: int = 10_000) -> None:
        self.allowed_table = allowed_table
        self.max_sql_length = max_sql_length

    def validate(self, sql: str, connection: Any) -> SQLValidation:
        normalized_sql = sql.strip() if isinstance(sql, str) else ""
        if not normalized_sql:
            return SQLValidation(False, "EMPTY_SQL", "查询语句不能为空。")
        if len(normalized_sql) > self.max_sql_length:
            return SQLValidation(False, "SQL_TOO_LONG", f"查询语句不能超过 {self.max_sql_length} 个字符。")
        if "--" in normalized_sql or "/*" in normalized_sql or "*/" in normalized_sql:
            return SQLValidation(False, "COMMENTS_NOT_ALLOWED", "查询语句不允许包含注释。")

        for pattern in FORBIDDEN_SOURCE_PATTERNS:
            if pattern.search(normalized_sql):
                return SQLValidation(False, "EXTERNAL_SOURCE_BLOCKED", "查询只能访问当前上传的数据表。")

        try:
            statements = connection.extract_statements(normalized_sql)
        except duckdb.Error as exc:
            return SQLValidation(False, "SQL_SYNTAX_ERROR", "SQL 语法无法解析，请检查查询条件。")

        if len(statements) != 1:
            return SQLValidation(False, "MULTI_STATEMENT", "一次只允许执行一条查询语句。")

        statement = statements[0]
        if statement.type != duckdb.StatementType.SELECT:
            return SQLValidation(False, "READ_ONLY_REQUIRED", "只允许执行 SELECT 查询，禁止修改数据或系统配置。")

        try:
            plan_rows = connection.execute(f"EXPLAIN {normalized_sql}").fetchall()
        except duckdb.CatalogException as exc:
            return SQLValidation(False, "UNKNOWN_TABLE", "查询引用了当前数据集之外的表。")
        except duckdb.BinderException as exc:
            return SQLValidation(False, "INVALID_REFERENCE", "查询引用了不存在的字段或不合法的表达式。")
        except duckdb.Error as exc:
            return SQLValidation(False, "SQL_VALIDATION_ERROR", "查询未通过 DuckDB 安全校验。")

        plan_text = "\n".join(str(row) for row in plan_rows)
        plan_functions = set(PLAN_FUNCTION_PATTERN.findall(plan_text))
        if plan_functions != {"PANDAS_SCAN"}:
            return SQLValidation(
                False,
                "EXTERNAL_SOURCE_BLOCKED",
                "查询只能读取当前上传的数据表，不能使用其他表函数或数据源。",
            )

        return SQLValidation(True, "OK", "查询通过只读校验。", normalized_sql)
