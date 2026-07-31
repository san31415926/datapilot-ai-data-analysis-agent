"""DataPilot 阶段 5 数据概览页。

此页面展示数据概览和质量问题，不执行 SQL 或模型调用。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config import get_settings
from src.data_loader import DataLoadError, load_file
from src.data_quality import analyze_quality


settings = get_settings()
ROLE_LABELS = {
    "id": "标识",
    "date": "日期",
    "measure": "度量",
    "category": "分类",
    "text": "文本",
}
SEVERITY_LABELS = {"warning": "警告", "error": "错误"}

st.set_page_config(
    page_title=settings.app_name,
    page_icon="📊",
    layout="wide",
)

st.title("DataPilot")
st.caption("本地自然语言数据分析 Agent | 阶段 5：数据概览与质量检查")

with st.sidebar:
    st.subheader("运行配置")
    st.text_input("Ollama 地址", value=settings.ollama_base_url, disabled=True)
    st.text_input("默认模型", value=settings.default_model, disabled=True)
    st.caption("模型调用将在后续阶段接入。")

st.subheader("上传数据")
uploaded_file = st.file_uploader(
    "选择一份 CSV 或 XLSX 文件",
    type=["csv", "xlsx"],
    help=f"单文件不超过 {settings.max_upload_mb} MB，最多读取 {settings.max_rows:,} 行。",
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

        quality = analyze_quality(loaded)
        st.subheader("数据质量概览")
        quality_metrics = st.columns(5)
        quality_metrics[0].metric("缺失单元格", f"{quality.missing_cell_count:,}")
        quality_metrics[1].metric("重复行", f"{quality.duplicate_row_count:,}")
        quality_metrics[2].metric("重复标识", f"{quality.duplicate_identifier_count:,}")
        quality_metrics[3].metric("质量问题", f"{len(quality.issues):,}")
        quality_metrics[4].metric("可分析行", f"{quality.row_count:,}")

        if quality.issues:
            issue_frame = pd.DataFrame(
                [
                    {
                        "级别": SEVERITY_LABELS.get(issue.severity, issue.severity),
                        "问题": issue.message,
                        "字段": issue.column or "整表",
                        "数量": issue.count,
                        "样例行": ", ".join(map(str, issue.sample_rows)) or "无",
                    }
                    for issue in quality.issues
                ]
            )
            st.dataframe(issue_frame, use_container_width=True, hide_index=True)
        else:
            st.success("暂未发现缺失值、重复记录或负数等质量问题。")

        st.subheader("字段识别")
        quality_by_name = {column.name: column for column in quality.columns}
        profile_frame = pd.DataFrame(
            [
                {
                    "字段": profile.name,
                    "原始表头": profile.original_name,
                    "识别类型": profile.logical_type,
                    "业务角色": ROLE_LABELS.get(quality_by_name[profile.name].role, quality_by_name[profile.name].role),
                    "非空数量": profile.non_empty_count,
                    "缺失数量": quality_by_name[profile.name].missing_count,
                    "唯一值数量": quality_by_name[profile.name].unique_count,
                    "最小值": quality_by_name[profile.name].min_value,
                    "最大值": quality_by_name[profile.name].max_value,
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
st.write("数据概览、缺失值、重复记录、数值范围和字段角色已经接入工作台。")
st.write("下一阶段将进入 DuckDB 只读查询和 SQL 安全边界。")
