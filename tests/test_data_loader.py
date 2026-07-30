import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import pandas as pd

from src.data_loader import DataLoadError, load_file


class DataLoaderTestCase(unittest.TestCase):
    def test_loads_utf8_csv_and_infers_types(self) -> None:
        content = (
            "商品,数量,订单日期\n"
            "咖啡豆,2,2025-01-02\n"
            "保温杯,3,2025-01-03\n"
        ).encode("utf-8")

        result = load_file("orders.csv", content)

        self.assertEqual(result.encoding, "utf-8-sig")
        self.assertEqual(result.file_format, "csv")
        self.assertEqual([profile.logical_type for profile in result.columns], ["text", "numeric", "date"])
        self.assertEqual(result.dataframe["数量"].iloc[0], 2)
        self.assertEqual(result.dataframe["订单日期"].iloc[0].year, 2025)

    def test_loads_gbk_csv_and_reports_encoding(self) -> None:
        content = "商品,数量\n咖啡豆,2\n".encode("gb18030")

        result = load_file("orders.csv", content)

        self.assertEqual(result.encoding, "gb18030")
        self.assertEqual(result.raw_dataframe["商品"].iloc[0], "咖啡豆")

    def test_cleans_blank_and_duplicate_headers(self) -> None:
        content = " 商品 ,商品,,日期\n咖啡,2,,2025-01-01\n".encode("utf-8")

        result = load_file("headers.csv", content)

        self.assertEqual(list(result.raw_dataframe.columns), ["商品", "商品_2", "未命名列3", "日期"])
        self.assertEqual(len(result.warnings), 2)

    def test_preserves_raw_values_and_reports_conversion_failures(self) -> None:
        content = (
            "商品,数量,订单日期\n"
            "咖啡豆,2,2025-01-02\n"
            "保温杯,3,2025-01-03\n"
            "台灯,4,2025-01-04\n"
            "鼠标,5,2025-01-05\n"
            "耳机,不是数字,不是日期\n"
        ).encode("utf-8")

        result = load_file("invalid-values.csv", content)
        quantity_profile = result.columns[1]
        date_profile = result.columns[2]

        self.assertEqual(quantity_profile.logical_type, "numeric")
        self.assertEqual(quantity_profile.conversion_failure_rows, (4,))
        self.assertEqual(date_profile.logical_type, "date")
        self.assertEqual(date_profile.conversion_failure_rows, (4,))
        self.assertEqual(result.raw_dataframe["数量"].iloc[4], "不是数字")
        self.assertTrue(pd.isna(result.dataframe["数量"].iloc[4]))

    def test_loads_xlsx_and_keeps_columns(self) -> None:
        frame = pd.DataFrame({"商品": ["咖啡豆"], "数量": [2]})
        buffer = BytesIO()
        frame.to_excel(buffer, index=False, engine="openpyxl")

        result = load_file("orders.xlsx", buffer.getvalue())

        self.assertIsNone(result.encoding)
        self.assertEqual(result.file_format, "xlsx")
        self.assertEqual(result.dataframe["数量"].iloc[0], 2)

    def test_rejects_unsupported_extension_empty_file_and_no_header(self) -> None:
        cases = (
            ("orders.txt", "商品,数量\n咖啡豆,2\n".encode("utf-8"), "UNSUPPORTED_EXTENSION"),
            ("orders.csv", b"", "EMPTY_FILE"),
            ("orders.csv", b"1,2\n3,4\n", "NO_HEADER"),
        )

        for file_name, content, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(DataLoadError) as context:
                    load_file(file_name, content)
                self.assertEqual(context.exception.code, code)

    def test_rejects_bad_file_and_limits(self) -> None:
        with self.assertRaises(DataLoadError) as bad_xlsx:
            load_file("orders.xlsx", b"not an xlsx")
        self.assertEqual(bad_xlsx.exception.code, "PARSE_ERROR")

        oversized_content = b"x" * (1024 * 1024 + 1)
        with self.assertRaises(DataLoadError) as size_error:
            load_file("orders.csv", oversized_content, max_upload_mb=1)
        self.assertEqual(size_error.exception.code, "FILE_TOO_LARGE")

    def test_rejects_too_many_rows(self) -> None:
        content = "商品,数量\n" + "\n".join(f"商品{i},1" for i in range(3))

        with self.assertRaises(DataLoadError) as context:
            load_file("orders.csv", content.encode("utf-8"), max_rows=2)

        self.assertEqual(context.exception.code, "TOO_MANY_ROWS")


if __name__ == "__main__":
    unittest.main()
