from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image

from src.visualization import chart_to_csv_bytes, export_chart_png, render_chart


CHART_DATA = {
    "chart_type": "bar",
    "title": "地区销售额",
    "x_field": "地区",
    "y_field": "销售额",
    "data": [
        {"地区": "华东", "销售额": 1200.0},
        {"地区": "华南", "销售额": 900.0},
    ],
}


class VisualizationTestCase(unittest.TestCase):
    def test_renders_bar_line_and_pie_from_same_contract(self) -> None:
        for chart_type in ("bar", "line", "pie"):
            payload = {**CHART_DATA, "chart_type": chart_type}
            result = render_chart(payload)

            self.assertEqual(result.figure.layout.title.text, "地区销售额")
            self.assertEqual(len(result.figure.data), 1)

    def test_empty_and_negative_values_are_explained_without_crashing(self) -> None:
        empty = render_chart({**CHART_DATA, "data": []})
        self.assertEqual(len(empty.figure.data), 1)
        self.assertTrue(empty.figure.layout.annotations)

        negative = render_chart(
            {
                **CHART_DATA,
                "chart_type": "pie",
                "data": [
                    {"地区": "华东", "销售额": -20},
                    {"地区": "华南", "销售额": 30},
                ],
            }
        )
        self.assertIn("负值", negative.warning)
        self.assertEqual(len(negative.figure.data), 1)

    def test_csv_uses_utf8_bom_and_selected_columns(self) -> None:
        csv_bytes = chart_to_csv_bytes(CHART_DATA)

        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        csv_text = csv_bytes.decode("utf-8-sig")
        self.assertIn("地区,销售额", csv_text)
        self.assertIn("华东,1200.0", csv_text)

    def test_png_export_returns_valid_non_blank_image(self) -> None:
        result = export_chart_png(CHART_DATA, width=320, height=240)
        image = Image.open(BytesIO(result.data))

        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.size, (320, 240))
        self.assertIsNotNone(image.convert("RGB").getbbox())
        self.assertIn(result.backend, {"plotly", "pillow"})


if __name__ == "__main__":
    unittest.main()
