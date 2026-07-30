import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.sample_data import DATA_COLUMNS, generate_orders, write_sample_files


class SampleDataTestCase(unittest.TestCase):
    def test_fixed_seed_produces_same_data(self) -> None:
        first = generate_orders()
        second = generate_orders()

        pd.testing.assert_frame_equal(first, second)

    def test_schema_and_quality_scenarios_are_present(self) -> None:
        frame = generate_orders()

        self.assertEqual(list(frame.columns), DATA_COLUMNS)
        self.assertEqual(len(frame), 243)
        self.assertTrue(frame["订单编号"].duplicated().any())
        self.assertTrue(frame["渠道"].isna().any())
        self.assertTrue(frame["客户类型"].isna().any())
        self.assertIn(99999.99, frame["销售额"].tolist())
        self.assertGreater(frame["销售额"].max(), 50000)

    def test_csv_and_xlsx_contain_the_same_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path, xlsx_path = write_sample_files(Path(temp_dir))
            csv_frame = pd.read_csv(csv_path)
            xlsx_frame = pd.read_excel(xlsx_path)

        self.assertEqual(csv_frame.shape, (243, 13))
        self.assertEqual(xlsx_frame.shape, (243, 13))
        self.assertListEqual(list(csv_frame.columns), DATA_COLUMNS)
        self.assertListEqual(list(xlsx_frame.columns), DATA_COLUMNS)
        self.assertEqual(csv_frame["订单编号"].tolist(), xlsx_frame["订单编号"].tolist())
        self.assertEqual(csv_frame["销售额"].tolist(), xlsx_frame["销售额"].tolist())


if __name__ == "__main__":
    unittest.main()
