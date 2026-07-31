import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class StreamlitAppTestCase(unittest.TestCase):
    def test_practice_data_mode_loads_selected_dataset(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        app = AppTest.from_file(str(project_dir / "app.py")).run()

        app.radio[0].set_value("练习数据").run()

        self.assertEqual(app.selectbox[0].value, "ecommerce-orders")
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertTrue(any("已加载" in item.value for item in app.success))

    def test_upload_and_default_query_flow(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        app = AppTest.from_file(str(project_dir / "app.py")).run()

        app.radio[0].set_value("上传文件").run()
        self.assertEqual(len(app.file_uploader), 1)
        app.file_uploader[0].upload(
            "sample_ecommerce.csv",
            (project_dir / "data" / "sample_ecommerce.csv").read_bytes(),
            "text/csv",
        ).run()

        self.assertEqual(app.selectbox[0].value, "按地区汇总销售额")
        query_button = next(button for button in app.button if button.label == "执行只读查询")
        query_button.click().run()

        self.assertEqual(len(app.error), 0)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("返回 6 行", app.success[-1].value)


if __name__ == "__main__":
    unittest.main()
