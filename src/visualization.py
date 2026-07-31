"""将结构化图表结果渲染为 Plotly 图表和可下载的 PNG。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import plotly.graph_objects as go
from PIL import Image, ImageDraw, ImageFont

from src.tools import ChartSpec


@dataclass(frozen=True)
class PNGExportResult:
    """PNG 导出结果，记录实际使用的渲染后端。"""

    data: bytes
    backend: Literal["plotly", "pillow"]
    warning: str | None = None


@dataclass(frozen=True)
class ChartRenderResult:
    """图表对象和需要提示用户的可解释警告。"""

    figure: go.Figure
    warning: str | None = None


def render_chart(
    chart: ChartSpec | dict[str, Any],
) -> ChartRenderResult:
    """只根据已校验的结构化 ChartSpec 创建 Plotly 图表。"""

    parsed = ChartSpec.model_validate(chart)
    labels = [_display_value(row.get(parsed.x_field)) for row in parsed.data]
    values = [_to_number(row.get(parsed.y_field)) for row in parsed.data]
    warning: str | None = None

    figure = go.Figure()
    if parsed.chart_type == "bar":
        figure.add_trace(go.Bar(x=labels, y=values, name=parsed.y_field))
    elif parsed.chart_type == "line":
        figure.add_trace(
            go.Scatter(
                x=labels,
                y=values,
                mode="lines+markers",
                name=parsed.y_field,
            )
        )
    else:
        positive_values = [value if value is not None and value > 0 else 0 for value in values]
        negative_count = sum(1 for value in values if value is not None and value < 0)
        if any(value > 0 for value in positive_values):
            figure.add_trace(go.Pie(labels=labels, values=positive_values, name=parsed.y_field))
        if negative_count:
            warning = "饼图不展示负值，建议切换为柱状图或折线图查看完整数据。"

    figure.update_layout(
        title={"text": parsed.title, "x": 0.02, "xanchor": "left"},
        template="plotly_white",
        font={"family": "Microsoft YaHei, SimHei, sans-serif", "size": 14},
        margin={"l": 80, "r": 40, "t": 80, "b": 120},
        legend={"title": {"text": parsed.y_field}},
    )
    if parsed.chart_type != "pie":
        figure.update_xaxes(
            title_text=parsed.x_field,
            tickangle=-45 if len(labels) > 8 else 0,
        )
        figure.update_yaxes(title_text=parsed.y_field, separatethousands=True)
    if not parsed.data:
        figure.add_annotation(
            text="当前结果没有可绘制数据",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 18},
        )
    elif parsed.chart_type == "pie" and not any(value > 0 for value in positive_values):
        figure.add_annotation(
            text="饼图需要至少一个正数值，请改用柱状图或折线图",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 16},
        )
    return ChartRenderResult(figure=figure, warning=warning)


def chart_to_csv_bytes(chart: ChartSpec | dict[str, Any]) -> bytes:
    """导出图表所使用的两列结构化数据，使用 UTF-8 BOM 兼容 Excel。"""

    parsed = ChartSpec.model_validate(chart)
    frame = pd.DataFrame(parsed.data).reindex(columns=[parsed.x_field, parsed.y_field])
    return frame.to_csv(index=False).encode("utf-8-sig")


def export_chart_png(
    chart: ChartSpec | dict[str, Any],
    *,
    width: int = 1280,
    height: int = 720,
) -> PNGExportResult:
    """优先调用 Plotly/Kaleido，失败时生成可验证的 Pillow 静态图。"""

    parsed = ChartSpec.model_validate(chart)
    rendered = render_chart(parsed)
    try:
        image = rendered.figure.to_image(
            format="png",
            width=width,
            height=height,
            engine="kaleido",
        )
        return PNGExportResult(
            data=image,
            backend="plotly",
            warning=rendered.warning,
        )
    except Exception:
        fallback = _render_with_pillow(parsed, width=width, height=height)
        warning = "当前环境的 Plotly PNG 导出不可用，已使用本地静态渲染生成 PNG。"
        if rendered.warning:
            warning = f"{warning}{rendered.warning}"
        return PNGExportResult(data=fallback, backend="pillow", warning=warning)


def _render_with_pillow(chart: ChartSpec, *, width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(28)
    label_font = _load_font(16)
    small_font = _load_font(14)
    draw.text((60, 28), chart.title, fill="#1f2937", font=title_font)

    labels = [_display_value(row.get(chart.x_field)) for row in chart.data]
    values = [_to_number(row.get(chart.y_field)) for row in chart.data]
    if not chart.data:
        draw.text((width // 2 - 100, height // 2), "当前结果没有可绘制数据", fill="#6b7280", font=label_font)
        return _png_bytes(image)

    if chart.chart_type == "pie":
        _draw_pie(draw, labels, values, width, height, label_font, small_font)
        return _png_bytes(image)

    left, top, right, bottom = 100, 100, width - 60, height - 130
    valid_values = [value for value in values if value is not None]
    if not valid_values:
        draw.text((width // 2 - 100, height // 2), "没有可绘制的数值", fill="#6b7280", font=label_font)
        return _png_bytes(image)

    minimum = min(0.0, min(valid_values))
    maximum = max(0.0, max(valid_values))
    if math.isclose(minimum, maximum):
        maximum = minimum + 1.0
    plot_height = bottom - top
    plot_width = right - left

    def y_position(value: float) -> float:
        return bottom - ((value - minimum) / (maximum - minimum)) * plot_height

    baseline = y_position(0.0)
    draw.line((left, top, left, bottom), fill="#9ca3af", width=2)
    draw.line((left, baseline, right, baseline), fill="#9ca3af", width=2)
    _draw_scale(draw, minimum, maximum, left, top, bottom, label_font)

    count = max(len(labels), 1)
    slot_width = plot_width / count
    if chart.chart_type == "bar":
        bar_width = max(10, int(slot_width * 0.62))
        for index, (label, value) in enumerate(zip(labels, values)):
            center = left + slot_width * (index + 0.5)
            if value is not None:
                y_value = y_position(value)
                draw.rectangle(
                    (center - bar_width / 2, min(y_value, baseline), center + bar_width / 2, max(y_value, baseline)),
                    fill="#2563eb",
                )
            _draw_centered_label(draw, label, center, bottom + 18, small_font, width=int(slot_width))
    else:
        points: list[tuple[float, float]] = []
        for index, (label, value) in enumerate(zip(labels, values)):
            center = left + slot_width * (index + 0.5)
            if value is not None:
                point = (center, y_position(value))
                if points:
                    draw.line((points[-1][0], points[-1][1], point[0], point[1]), fill="#16a34a", width=4)
                points.append(point)
                draw.ellipse((point[0] - 5, point[1] - 5, point[0] + 5, point[1] + 5), fill="#16a34a")
            _draw_centered_label(draw, label, center, bottom + 18, small_font, width=int(slot_width))

    draw.text((left, height - 60), chart.x_field, fill="#374151", font=label_font)
    draw.text((width - 220, top - 28), chart.y_field, fill="#374151", font=label_font)
    return _png_bytes(image)


def _draw_pie(
    draw: ImageDraw.ImageDraw,
    labels: list[str],
    values: list[float | None],
    width: int,
    height: int,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    small_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    positive = [(label, value) for label, value in zip(labels, values) if value is not None and value > 0]
    if not positive:
        draw.text((width // 2 - 150, height // 2), "饼图需要正数值，请改用其他图表", fill="#6b7280", font=label_font)
        return
    colors = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"]
    total = sum(value for _, value in positive)
    box = (120, 130, min(height - 80, 620), min(height - 80, 620))
    start = 0.0
    for index, (label, value) in enumerate(positive):
        extent = value / total * 360
        draw.pieslice(box, start=start, end=start + extent, fill=colors[index % len(colors)], outline="white")
        start += extent
    legend_x = 700
    legend_y = 180
    for index, (label, value) in enumerate(positive[:12]):
        y = legend_y + index * 30
        draw.rectangle((legend_x, y, legend_x + 18, y + 18), fill=colors[index % len(colors)])
        draw.text((legend_x + 28, y - 2), f"{label}: {value:g}", fill="#374151", font=small_font)
    if len(positive) > 12:
        draw.text((legend_x, legend_y + 12 * 30), "其余类别未在图例中展开", fill="#6b7280", font=small_font)
    draw.text((120, height - 60), "正值占比", fill="#374151", font=label_font)


def _draw_scale(
    draw: ImageDraw.ImageDraw,
    minimum: float,
    maximum: float,
    left: int,
    top: int,
    bottom: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    for index in range(5):
        ratio = index / 4
        value = maximum - (maximum - minimum) * ratio
        y = top + (bottom - top) * ratio
        draw.text((12, y - 10), f"{value:,.2f}", fill="#6b7280", font=font)
        draw.line((left - 5, y, left, y), fill="#9ca3af", width=1)


def _draw_centered_label(
    draw: ImageDraw.ImageDraw,
    label: str,
    center: float,
    y: float,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    width: int,
) -> None:
    text = label if len(label) <= 10 else f"{label[:9]}..."
    box = draw.textbbox((0, 0), text, font=font)
    text_width = box[2] - box[0]
    draw.text((center - min(text_width, width - 4) / 2, y), text, fill="#374151", font=font)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    )
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _display_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "空值"
    return str(value)


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
