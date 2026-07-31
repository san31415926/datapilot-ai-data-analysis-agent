"""DataPilot 的 DuckDB 只读查询引擎。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd

from src.sql_guard import SQLGuard, SQLValidation


@dataclass(frozen=True)
class QueryExecution:
    """一次查询的可审计执行记录，不包含完整数据结果。"""

    status: str
    sql: str
    row_count: int
    result_bytes: int
    elapsed_ms: int
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class QueryResponse:
    """查询结果和执行记录。"""

    dataframe: pd.DataFrame | None
    execution: QueryExecution

    @property
    def success(self) -> bool:
        return self.execution.status == "success"


class ReadOnlyQueryEngine:
    """将一个 DataFrame 注册到内存 DuckDB，并只执行受控 SELECT。"""

    def __init__(
        self,
        dataframe: pd.DataFrame,
        *,
        table_name: str = "uploaded_data",
        max_rows: int = 1_000,
        max_result_bytes: int = 1_000_000,
        timeout_seconds: float = 5.0,
    ) -> None:
        if max_rows <= 0 or max_result_bytes <= 0 or timeout_seconds <= 0:
            raise ValueError("查询限制必须大于 0")
        self.table_name = table_name
        self.max_rows = max_rows
        self.max_result_bytes = max_result_bytes
        self.timeout_seconds = timeout_seconds
        self._connection = duckdb.connect(database=":memory:")
        self._connection.execute("SET threads=1")
        self._connection.register(table_name, dataframe)
        self._column_names = tuple(str(column) for column in dataframe.columns)
        self._numeric_columns = frozenset(
            str(column)
            for column in dataframe.columns
            if pd.api.types.is_numeric_dtype(dataframe[column])
        )
        self._guard = SQLGuard(allowed_table=table_name)
        self._lock = threading.Lock()

    @property
    def column_names(self) -> tuple[str, ...]:
        """返回工具参数校验可以使用的字段名。"""

        return self._column_names

    def is_numeric_column(self, column: str) -> bool:
        """判断字段是否为可聚合的数值字段。"""

        return column in self._numeric_columns

    def validate_sql(self, sql: str) -> SQLValidation:
        """只做 SQL 安全校验，不执行查询。"""

        with self._lock:
            return self._guard.validate(sql, self._connection)

    def execute(self, sql: str) -> QueryResponse:
        """校验并执行查询，失败时只返回安全的中文错误。"""

        started_at = time.perf_counter()
        with self._lock:
            validation = self._guard.validate(sql, self._connection)
            if not validation.approved:
                return self._response(
                    status="rejected",
                    sql=sql if isinstance(sql, str) else "",
                    started_at=started_at,
                    error_code=validation.code,
                    error_message=validation.message,
                )

            interrupted = threading.Event()

            def interrupt_query() -> None:
                interrupted.set()
                self._connection.interrupt()

            timer = threading.Timer(self.timeout_seconds, interrupt_query)
            timer.daemon = True
            timer.start()
            try:
                limited_sql = (
                    f"SELECT * FROM ({validation.normalized_sql}) AS __datapilot_result "
                    f"LIMIT {self.max_rows + 1}"
                )
                result = self._connection.execute(limited_sql).fetch_df()
            except duckdb.Error as exc:
                if interrupted.is_set():
                    return self._response(
                        status="error",
                        sql=validation.normalized_sql,
                        started_at=started_at,
                        error_code="QUERY_TIMEOUT",
                        error_message=f"查询超过 {self.timeout_seconds:g} 秒限制，请缩小查询范围。",
                    )
                return self._response(
                    status="error",
                    sql=validation.normalized_sql,
                    started_at=started_at,
                    error_code="QUERY_ERROR",
                    error_message="查询执行失败，请检查字段、条件或聚合函数。",
                )
            finally:
                timer.cancel()

        result_bytes = int(result.memory_usage(index=True, deep=True).sum())
        if len(result) > self.max_rows or result_bytes > self.max_result_bytes:
            return self._response(
                status="rejected",
                sql=validation.normalized_sql,
                started_at=started_at,
                error_code="RESULT_TOO_LARGE",
                error_message=f"查询结果超过限制，最多返回 {self.max_rows} 行且不超过 {self.max_result_bytes} 字节。",
            )

        return self._response(
            status="success",
            sql=validation.normalized_sql,
            started_at=started_at,
            dataframe=result,
            row_count=len(result),
            result_bytes=result_bytes,
        )

    def close(self) -> None:
        self._connection.close()

    def _response(
        self,
        *,
        status: str,
        sql: str,
        started_at: float,
        dataframe: pd.DataFrame | None = None,
        row_count: int = 0,
        result_bytes: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> QueryResponse:
        execution = QueryExecution(
            status=status,
            sql=sql,
            row_count=row_count,
            result_bytes=result_bytes,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            error_code=error_code,
            error_message=error_message,
        )
        return QueryResponse(dataframe=dataframe, execution=execution)
