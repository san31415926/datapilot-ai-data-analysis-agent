import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.practice_data import (
    PRACTICE_DATASETS,
    generate_practice_frame,
    practice_catalog_frame,
    write_practice_files,
)


class PracticeDataTestCase(unittest.TestCase):
    def test_catalog_contains_ten_topics(self) -> None:
        self.assertEqual(len(PRACTICE_DATASETS), 10)
        self.assertEqual(len({item.slug for item in PRACTICE_DATASETS}), 10)
        self.assertEqual(len(practice_catalog_frame()), 10)

    def test_each_topic_has_reproducible_tabular_data(self) -> None:
        for dataset in PRACTICE_DATASETS:
            first = generate_practice_frame(dataset.slug)
            second = generate_practice_frame(dataset.slug)
            pd.testing.assert_frame_equal(first, second)
            self.assertGreaterEqual(len(first), 70)
            self.assertGreaterEqual(len(first.columns), 6)

    def test_writes_csv_and_xlsx_for_each_topic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_practice_files(Path(temp_dir))
            self.assertEqual(len(paths), 10)
            self.assertEqual(len(list(Path(temp_dir).glob("*.csv"))), 10)
            self.assertEqual(len(list(Path(temp_dir).glob("*.xlsx"))), 10)
            for csv_path, xlsx_path in paths:
                csv_frame = pd.read_csv(csv_path)
                xlsx_frame = pd.read_excel(xlsx_path)
                self.assertEqual(csv_frame.shape, xlsx_frame.shape)
                self.assertListEqual(list(csv_frame.columns), list(xlsx_frame.columns))


if __name__ == "__main__":
    unittest.main()
