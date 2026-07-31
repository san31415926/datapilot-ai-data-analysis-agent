import unittest

import duckdb
import pandas as pd

from src.sql_guard import SQLGuard


class SQLGuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect(database=":memory:")
        self.connection.register("uploaded_data", pd.DataFrame({"地区": ["华东"], "销售额": [10]}))
        self.guard = SQLGuard()

    def tearDown(self) -> None:
        self.connection.close()

    def test_allows_select_on_uploaded_table(self) -> None:
        result = self.guard.validate("SELECT 地区, 销售额 FROM uploaded_data", self.connection)

        self.assertTrue(result.approved)
        self.assertEqual(result.code, "OK")

    def test_allows_cte_that_still_reads_uploaded_table(self) -> None:
        result = self.guard.validate(
            "WITH summary AS (SELECT SUM(销售额) AS total FROM uploaded_data) SELECT * FROM summary",
            self.connection,
        )

        self.assertTrue(result.approved)

    def test_rejects_write_and_configuration_statements(self) -> None:
        queries = (
            "INSERT INTO uploaded_data VALUES ('华南', 20)",
            "UPDATE uploaded_data SET 销售额 = 0",
            "DELETE FROM uploaded_data",
            "DROP TABLE uploaded_data",
            "CREATE TABLE other_data(value INTEGER)",
            "COPY uploaded_data TO 'out.csv'",
            "INSTALL httpfs",
            "LOAD httpfs",
            "PRAGMA enable_profiling",
        )

        for query in queries:
            with self.subTest(query=query):
                result = self.guard.validate(query, self.connection)
                self.assertFalse(result.approved)
                self.assertEqual(result.code, "READ_ONLY_REQUIRED")

    def test_rejects_multiple_statements_and_comments(self) -> None:
        cases = (
            ("SELECT * FROM uploaded_data; SELECT 1", "MULTI_STATEMENT"),
            ("SELECT * FROM uploaded_data -- bypass", "COMMENTS_NOT_ALLOWED"),
            ("SELECT * FROM uploaded_data /* bypass */", "COMMENTS_NOT_ALLOWED"),
        )

        for query, code in cases:
            with self.subTest(code=code):
                result = self.guard.validate(query, self.connection)
                self.assertFalse(result.approved)
                self.assertEqual(result.code, code)

    def test_rejects_unknown_tables_and_external_sources(self) -> None:
        cases = (
            ("SELECT * FROM other_table", "UNKNOWN_TABLE"),
            ("SELECT * FROM read_csv_auto('secret.csv')", "EXTERNAL_SOURCE_BLOCKED"),
            ("SELECT * FROM information_schema.tables", "EXTERNAL_SOURCE_BLOCKED"),
            ("SELECT * FROM 'secret.csv'", "EXTERNAL_SOURCE_BLOCKED"),
            ("SELECT * FROM range(10)", "EXTERNAL_SOURCE_BLOCKED"),
            ("SELECT 1", "EXTERNAL_SOURCE_BLOCKED"),
        )

        for query, code in cases:
            with self.subTest(code=code):
                result = self.guard.validate(query, self.connection)
                self.assertFalse(result.approved)
                self.assertEqual(result.code, code)

    def test_rejects_empty_long_and_invalid_sql(self) -> None:
        cases = (
            ("", "EMPTY_SQL"),
            ("SELECT", "SQL_SYNTAX_ERROR"),
            ("SELECT * FROM uploaded_data", "SQL_TOO_LONG"),
        )

        for query, code in cases:
            with self.subTest(code=code):
                guard = SQLGuard(max_sql_length=5) if code == "SQL_TOO_LONG" else self.guard
                result = guard.validate(query, self.connection)
                self.assertFalse(result.approved)
                self.assertEqual(result.code, code)


if __name__ == "__main__":
    unittest.main()
