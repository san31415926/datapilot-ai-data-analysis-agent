"""DataPilot 阶段 2 启动页。

此页面只验证工作台、配置和上传入口可以启动，不执行数据读取或模型调用。
"""

from __future__ import annotations

import streamlit as st

from config import get_settings


settings = get_settings()

st.set_page_config(
    page_title=settings.app_name,
    page_icon="📊",
    layout="wide",
)

st.title("DataPilot")
st.caption("本地自然语言数据分析 Agent | 阶段 2：项目骨架与独立依赖")

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
    st.info("请先选择 CSV 或 XLSX 文件。阶段 2 暂不读取文件内容。")
else:
    file_size = int(getattr(uploaded_file, "size", 0) or 0)
    file_size_mb = file_size / 1024 / 1024
    st.success(f"已选择：{uploaded_file.name}（{file_size_mb:.2f} MB）")
    if file_size > settings.max_upload_mb * 1024 * 1024:
        st.error(f"文件超过当前限制：{settings.max_upload_mb} MB。")
    else:
        st.info("文件入口验证通过。实际读取、编码处理和数据质量检查将在后续阶段完成。")

st.divider()
st.subheader("当前阶段验收")
st.write("项目骨架、独立依赖、配置读取和最小工作台已经建立。")
st.write("下一阶段将生成固定随机种子的中文电商样例数据，用来验证读取和分析结果。")
