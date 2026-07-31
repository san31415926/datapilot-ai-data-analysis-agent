import unittest

import pandas as pd

from src.data_loader import load_file
from src.data_quality import analyze_quality


class DataQualityTestCase(unittest.TestCase):
    def test_sample_data_overview_and_roles(self) -> None:
        frame = pd.DataFrame(
            {
                "订单编号": ["A-1", "A-2", "A-2"],
                "订单日期": ["2025-01-01", "2025-01-02", "2025-01-02"],
                "地区": ["华东", "华南", "华南"],
                "数量": [2, 3, 3],
                "销售额": [100.0, 200.0, 200.0],
            }
        )
        csv_content = frame.to_csv(index=False).encode("utf-8")

        report = analyze_quality(load_file("quality.csv", csv_content))

        self.assertEqual(report.row_count, 3)
        self.assertEqual(report.column_count, 5)
        roles = {column.name: column.role for column in report.columns}
        self.assertEqual(roles["订单编号"], "id")
        self.assertEqual(roles["订单日期"], "date")
        self.assertEqual(roles["地区"], "category")
        self.assertEqual(roles["销售额"], "measure")
        sales = next(column for column in report.columns if column.name == "销售额")
        self.assertEqual((sales.min_value, sales.max_value), (100, 200))

    def test_sample_data_reports_missing_and_duplicates(self) -> None:
        frame = pd.DataFrame(
            {
                "订单编号": ["A-1", "A-1", "A-3"],
                "订单日期": ["2025-01-01", None, "2025-01-03"],
                "数量": [1, -2, 3],
                "销售额": [10.0, 20.0, None],
            }
        )
        csv_content = frame.to_csv(index=False).encode("utf-8")

        report = analyze_quality(load_file("quality.csv", csv_content))
        codes = {issue.code for issue in report.issues}

        self.assertEqual(report.missing_cell_count, 2)
        self.assertEqual(report.duplicate_row_count, 0)
        self.assertEqual(report.duplicate_identifier_count, 1)
        self.assertIn("MISSING_DATE", codes)
        self.assertIn("MISSING_VALUES", codes)
        self.assertIn("NEGATIVE_VALUES", codes)
        self.assertIn("DUPLICATE_IDENTIFIER", codes)

    def test_exact_duplicate_rows_and_json_summary(self) -> None:
        content = (
            "订单编号,数量,销售额\n"
            "A-1,1,10\n"
            "A-1,1,10\n"
        ).encode("utf-8")

        report = analyze_quality(load_file("quality.csv", content))
        summary = report.to_dict()

        self.assertEqual(report.duplicate_row_count, 1)
        self.assertEqual(report.duplicate_identifier_count, 1)
        self.assertEqual(summary["row_count"], 2)
        self.assertIsInstance(summary["columns"], list)
        self.assertIsInstance(summary["issues"], list)


if __name__ == "__main__":
    unittest.main()
