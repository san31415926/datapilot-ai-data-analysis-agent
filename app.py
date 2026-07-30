"""DataPilot 阶段 2 启动页。

此页面只验证工作台、配置和上传入口可以启动，不执行数据读取或模型调用。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config import get_settings
from src.data_loader import DataLoadError, load_file


settings = get_settings()

st.set_page_config(
    page_title=settings.app_name,
    page_icon="📊",
    layout="wide",
)

st.title("DataPilot")
st.caption("本地自然语言数据分析 Agent | 阶段 4：文件读取与类型识别")

with st.sidebar:
    st.subheader("运行配置")
    st.text_input("Ollama 地址", value=settings.ollama_base_url, disabled=True)
    st.text_input("默认模型", value=settings.default_model, disabled=True)
    st.caption("模型调用将在后续阶段接入。")

st.subheader("上传数据")
uploaded_file = st.file_uploader(
    "选择一份 CSV 或 XLSX 文件",
    type=["csv", "xlsx"],
    help=f"当前阶段仅检查文件入口，单文件建议不超过 {settings.max_upload_mb} MB。",
)

if uploaded_file is None:
    st.info("请先选择 CSV 或 XLSX 文件。")
else:
    try:
        loaded = load_file(
            uploaded_file.name,
            uploaded_file.getvalue(),
            max_upload_mb=settings.max_upload_mb,
            max_rows=settings.max_rows,
        )
    except DataLoadError as exc:
        st.error(f"文件无法加载：{exc.message}")
    else:
        encoding_label = loaded.encoding or "XLSX 内置格式"
        st.success(
            f"已加载：{loaded.source_name} | 格式：{loaded.file_format.upper()} | 编码：{encoding_label}"
        )

        metric_columns = st.columns(3)
        metric_columns[0].metric("数据行数", f"{len(loaded.dataframe):,}")
        metric_columns[1].metric("字段数量", f"{len(loaded.dataframe.columns):,}")
        metric_columns[2].metric("最大行限制", f"{settings.max_rows:,}")

        if loaded.warnings:
            for warning in loaded.warnings:
                st.warning(warning)

        st.subheader("字段识别")
        profile_frame = pd.DataFrame(
            [
                {
                    "字段": profile.name,
                    "原始表头": profile.original_name,
                    "识别类型": profile.logical_type,
                    "非空数量": profile.non_empty_count,
                    "转换失败行": ", ".join(map(str, profile.conversion_failure_rows)) or "无",
                }
                for profile in loaded.columns
            ]
        )
        st.dataframe(profile_frame, use_container_width=True, hide_index=True)

        st.subheader("数据预览")
        st.dataframe(loaded.dataframe.head(10), use_container_width=True, hide_index=True)

st.divider()
st.subheader("当前阶段验收")
st.write("文件读取、编码识别、字段清洗和基础类型识别已经接入工作台。")
st.write("下一阶段将增加数据质量检查，再进入 DuckDB 只读查询和 SQL 安全边界。")
