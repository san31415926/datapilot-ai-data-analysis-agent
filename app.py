"""DataPilot 阶段 6 中文数据分析工作台。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config import get_settings
from src.data_loader import DataLoadError, load_file
from src.data_quality import analyze_quality
from src.ollama_client import OllamaClient, OllamaClientError
from src.query_engine import ReadOnlyQueryEngine
from src.tools import run_readonly_sql


settings = get_settings()
ROLE_LABELS = {
    "id": "标识",
    "date": "日期",
    "measure": "度量",
    "category": "分类",
    "text": "文本",
}
SEVERITY_LABELS = {"warning": "警告", "error": "错误"}
SQL_EXAMPLES = {
    "按地区汇总销售额": (
        "SELECT 地区, SUM(销售额) AS 总销售额 "
        "FROM uploaded_data GROUP BY 地区 ORDER BY 总销售额 DESC"
    ),
    "查看高额订单": (
        "SELECT 订单编号, 商品名称, 销售额 FROM uploaded_data "
        "WHERE 销售额 >= 1000 ORDER BY 销售额 DESC LIMIT 20"
    ),
    "统计各渠道订单数": (
        "SELECT 渠道, COUNT(*) AS 订单数, SUM(销售额) AS 总销售额 "
        "FROM uploaded_data GROUP BY 渠道 ORDER BY 总销售额 DESC"
    ),
}


def display_value(value: object) -> str:
    """将质量报告中的混合类型值转换为稳定的表格文本。"""

    if value is None:
        return "无"
    try:
        if bool(pd.isna(value)):
            return "无"
    except (TypeError, ValueError):
        pass
    return str(value)

st.set_page_config(
    page_title=settings.app_name,
    page_icon="📊",
    layout="wide",
)

st.title("DataPilot")
st.caption("本地自然语言数据分析 Agent | 阶段 7：受控工具工作台")

with st.sidebar:
    st.subheader("运行配置")
    st.text_input("Ollama 地址", value=settings.ollama_base_url, disabled=True)
    st.text_input("默认模型", value=settings.default_model, disabled=True)
    st.subheader("本地模型")
    st.caption("点击检测后读取本机 Ollama 模型，不会自动访问网络。")
    if "ollama_model_result" not in st.session_state:
        st.session_state["ollama_model_result"] = None
    if st.button("检测已安装模型", key="detect_ollama_models"):
        with st.spinner("正在读取本机模型列表..."):
            with OllamaClient(
                settings.ollama_base_url,
                timeout_seconds=settings.ollama_timeout_seconds,
                temperature=settings.ollama_temperature,
                max_output_tokens=settings.ollama_max_output_tokens,
            ) as ollama:
                st.session_state["ollama_model_result"] = ollama.list_models()

    model_result = st.session_state["ollama_model_result"]
    if model_result is None:
        st.caption("尚未检测本机模型。")
    elif not model_result.available:
        st.error(f"模型服务不可用：{model_result.error_message}")
    elif not model_result.models:
        st.warning(model_result.error_message or "没有可用的生成模型。")
    else:
        model_names = [model.name for model in model_result.models]
        default_index = model_names.index(settings.default_model) if settings.default_model in model_names else 0
        selected_model = st.selectbox("分析模型", model_names, index=default_index)
        st.session_state["selected_ollama_model"] = selected_model
        st.caption(f"已发现 {len(model_names)} 个生成模型，embedding 模型已过滤。")
        if st.button("测试本地模型", key="test_ollama_model"):
            try:
                with OllamaClient(
                    settings.ollama_base_url,
                    timeout_seconds=settings.ollama_timeout_seconds,
                    temperature=settings.ollama_temperature,
                    max_output_tokens=settings.ollama_max_output_tokens,
                ) as ollama:
                    chat_result = ollama.chat(
                        selected_model,
                        [{"role": "user", "content": "请用一句话说明数据分析助手的作用。"}],
                    )
            except OllamaClientError as exc:
                st.error(f"模型调用失败：{exc.message}（错误码：{exc.code}）")
            else:
                st.success(f"{chat_result.model} 已响应，耗时 {chat_result.elapsed_ms} 毫秒。")
                st.write(chat_result.content)
    st.subheader("查询限制")
    st.caption(f"最多返回 {settings.max_query_rows:,} 行")
    st.caption(f"结果不超过 {settings.max_query_result_mb} MB")
    st.caption(f"单次查询最多运行 {settings.query_timeout_seconds:g} 秒")

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
                    "最小值": display_value(quality_by_name[profile.name].min_value),
                    "最大值": display_value(quality_by_name[profile.name].max_value),
                    "转换失败行": ", ".join(map(str, profile.conversion_failure_rows)) or "无",
                }
                for profile in loaded.columns
            ]
        )
        st.dataframe(profile_frame, use_container_width=True, hide_index=True)

        st.subheader("数据预览")
        st.dataframe(loaded.dataframe.head(10), use_container_width=True, hide_index=True)

        st.subheader("只读 SQL 查询")
        st.caption(
            "查询只允许访问当前上传数据表 uploaded_data，系统会拦截写入语句、多语句、注释、"
            "外部数据源和超限结果。"
        )
        example_name = st.selectbox(
            "查询示例",
            ["自定义 SQL", *SQL_EXAMPLES.keys()],
            index=1,
            help="示例会填入查询框，你也可以直接修改后执行。",
        )
        sql_default = SQL_EXAMPLES.get(example_name, "")
        sql_text = st.text_area(
            "SQL 查询语句",
            value=sql_default,
            height=130,
            placeholder="例如：SELECT 地区, SUM(销售额) AS 总销售额 FROM uploaded_data GROUP BY 地区",
        )
        run_query = st.button("执行只读查询", type="primary")

        if run_query:
            engine = ReadOnlyQueryEngine(
                loaded.dataframe,
                max_rows=settings.max_query_rows,
                max_result_bytes=settings.max_query_result_mb * 1024 * 1024,
                timeout_seconds=settings.query_timeout_seconds,
            )
            try:
                response = run_readonly_sql(engine, {"sql": sql_text})
            finally:
                engine.close()

            execution = response.record
            if response.success:
                st.success(
                    f"查询成功：返回 {response.row_count:,} 行，耗时 {execution.elapsed_ms} 毫秒。"
                )
                st.dataframe(pd.DataFrame(response.rows), use_container_width=True, hide_index=True)
            else:
                st.error(
                    f"查询未执行或执行失败：{execution.error_message} "
                    f"（错误码：{execution.error_code}）"
                )
            with st.expander("查看本次执行记录"):
                st.write(
                    {
                        "状态": execution.status,
                        "耗时（毫秒）": execution.elapsed_ms,
                        "返回行数": response.row_count,
                        "结果大小（字节）": execution.result_summary.get("result_bytes", 0),
                        "执行 SQL": response.sql,
                        "工具记录": execution.model_dump(),
                    }
                )

st.divider()
st.subheader("当前阶段验收")
st.write("数据概览、质量检查、DuckDB 只读查询和受控工具已经接入工作台。")
st.write("下一阶段将接入 Ollama，并让模型先生成结构化分析计划。")
