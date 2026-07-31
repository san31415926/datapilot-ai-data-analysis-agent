"""DataPilot 中文数据分析工作台。"""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st

from config import get_settings
from src.analysis_runner import PlanExecutionResult, execute_analysis_plan
from src.data_loader import DataLoadError, load_file
from src.data_quality import analyze_quality
from src.exporters import build_markdown_report, rows_to_csv_bytes
from src.ollama_client import OllamaClient, OllamaClientError
from src.planner import StructuredPlanner
from src.practice_data import (
    PRACTICE_DATASETS,
    PRACTICE_DATASET_BY_SLUG,
    practice_catalog_frame,
    practice_file_path,
)
from src.query_engine import ReadOnlyQueryEngine
from src.query_intent import QueryIntentAnalyzer, QueryIntentResult
from src.report_generator import ReportGenerationResult, ReportGenerator
from src.tools import build_chart, run_readonly_sql
from src.visualization import chart_to_csv_bytes, export_chart_png, render_chart


settings = get_settings()
ROLE_LABELS = {
    "id": "标识",
    "date": "日期",
    "measure": "度量",
    "category": "分类",
    "text": "文本",
}
SEVERITY_LABELS = {"warning": "警告", "error": "错误"}
# 结构化 JSON 对步骤一致性更敏感，规划和报告使用低温度。
STRUCTURED_TEMPERATURE = 0.0
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


def render_chart_exports(chart_payload: dict[str, object], key_prefix: str) -> None:
    """渲染结构化图表，并提供 CSV/PNG 下载。"""

    try:
        rendered = render_chart(chart_payload)
    except (TypeError, ValueError):
        st.warning("图表结果结构不完整，当前无法渲染。")
        return

    st.plotly_chart(rendered.figure, use_container_width=True)
    if rendered.warning:
        st.warning(rendered.warning)

    chart_token = hashlib.sha256(
        json.dumps(chart_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    png_state_key = f"{key_prefix}_png_result_{chart_token}"
    if st.button("准备 PNG 下载", key=f"{key_prefix}_prepare_png_{chart_token}"):
        with st.spinner("正在生成 PNG 文件..."):
            st.session_state[png_state_key] = export_chart_png(chart_payload)
    png_result = st.session_state.get(png_state_key)
    if png_result is None:
        st.caption("需要图片文件时，点击“准备 PNG 下载”。")
    elif png_result.warning:
        st.caption(png_result.warning)
    download_columns = st.columns(2)
    with download_columns[0]:
        st.download_button(
            "下载图表 CSV",
            data=chart_to_csv_bytes(chart_payload),
            file_name="datapilot-chart-data.csv",
            mime="text/csv",
            key=f"{key_prefix}_csv",
        )
    if png_result is not None:
        with download_columns[1]:
            st.download_button(
                "下载图表 PNG",
                data=png_result.data,
                file_name="datapilot-chart.png",
                mime="image/png",
                key=f"{key_prefix}_png_{chart_token}",
            )


def numeric_columns(frame: pd.DataFrame) -> list[str]:
    """返回可以交给图表工具作为纵轴的数值字段。"""

    return [
        str(column)
        for column in frame.columns
        if pd.api.types.is_numeric_dtype(frame[column])
    ]

st.set_page_config(
    page_title=settings.app_name,
    page_icon="📊",
    layout="wide",
)

st.title("DataPilot")
st.caption("中文数据分析工作台")

with st.sidebar:
    st.subheader("分析模型")
    st.caption("使用本机 Ollama 模型生成分析结果。")
    if "ollama_model_result" not in st.session_state:
        st.session_state["ollama_model_result"] = None
    if st.button("检测本地模型", key="detect_ollama_models"):
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
        st.caption("请先检测可用模型。")
    elif not model_result.available:
        st.error(f"模型服务不可用：{model_result.error_message}")
    elif not model_result.models:
        st.warning(model_result.error_message or "没有可用的生成模型。")
    else:
        model_names = [model.name for model in model_result.models]
        default_index = model_names.index(settings.default_model) if settings.default_model in model_names else 0
        selected_model = st.selectbox("分析模型", model_names, index=default_index)
        st.session_state["selected_ollama_model"] = selected_model
        st.caption(f"可用模型：{len(model_names)} 个")
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
    with st.expander("查询限制"):
        st.caption(f"最多返回 {settings.max_query_rows:,} 行")
        st.caption(f"结果不超过 {settings.max_query_result_mb} MB")
        st.caption(f"单次查询最多运行 {settings.query_timeout_seconds:g} 秒")

st.subheader("选择数据")
source_mode = st.radio(
    "数据来源",
    ["练习数据", "上传文件"],
    horizontal=True,
    key="data_source_mode",
)
source_name: str | None = None
source_content: bytes | None = None

if source_mode == "练习数据":
    practice_options = [item.slug for item in PRACTICE_DATASETS]
    selected_slug = st.selectbox(
        "练习主题",
        practice_options,
        format_func=lambda slug: PRACTICE_DATASET_BY_SLUG[slug].name,
        key="practice_dataset_slug",
    )
    selected_dataset = PRACTICE_DATASET_BY_SLUG[selected_slug]
    selected_path = practice_file_path(selected_slug, "csv")
    selected_xlsx_path = practice_file_path(selected_slug, "xlsx")
    if selected_path.exists() and selected_xlsx_path.exists():
        preview_frame = pd.read_csv(selected_path)
        st.caption(selected_dataset.description)
        st.caption("建议问题：" + selected_dataset.suggested_questions[0])
        with st.expander("查看数据样例"):
            st.dataframe(preview_frame.head(5), use_container_width=True, hide_index=True)
        download_columns = st.columns(2)
        with download_columns[0]:
            st.download_button(
                "下载 CSV",
                data=selected_path.read_bytes(),
                file_name=selected_path.name,
                mime="text/csv",
                key="download_practice_csv",
            )
        with download_columns[1]:
            st.download_button(
                "下载 XLSX",
                data=selected_xlsx_path.read_bytes(),
                file_name=selected_xlsx_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_practice_xlsx",
            )
        source_name = selected_path.name
        source_content = selected_path.read_bytes()
    else:
        st.error("练习数据文件尚未生成，请重新启动项目或运行生成命令。")
else:
    uploaded_file = st.file_uploader(
        "选择一份 CSV 或 XLSX 文件",
        type=["csv", "xlsx"],
        help=f"单文件不超过 {settings.max_upload_mb} MB，最多读取 {settings.max_rows:,} 行。",
    )
    if uploaded_file is not None:
        source_name = uploaded_file.name
        source_content = uploaded_file.getvalue()

with st.expander("查看全部练习主题"):
    st.dataframe(practice_catalog_frame(), use_container_width=True, hide_index=True)

if source_content is None or source_name is None:
    st.info("请选择数据来源后开始分析。")
else:
    dataset_key = hashlib.sha256(source_content).hexdigest()
    if st.session_state.get("analysis_dataset_key") != dataset_key:
        st.session_state["analysis_dataset_key"] = dataset_key
        st.session_state.pop("planning_result", None)
        st.session_state.pop("planning_question", None)
        st.session_state.pop("execution_result", None)
        st.session_state.pop("report_result", None)
        st.session_state.pop("query_intent", None)
    try:
        loaded = load_file(
            source_name,
            source_content,
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

        metric_columns = st.columns(2)
        metric_columns[0].metric("数据行数", f"{len(loaded.dataframe):,}")
        metric_columns[1].metric("字段数量", f"{len(loaded.dataframe.columns):,}")

        if loaded.warnings:
            for warning in loaded.warnings:
                st.warning(warning)

        quality = analyze_quality(loaded)
        st.subheader("数据概览")
        quality_metrics = st.columns(4)
        quality_metrics[0].metric("缺失单元格", f"{quality.missing_cell_count:,}")
        quality_metrics[1].metric("重复行", f"{quality.duplicate_row_count:,}")
        quality_metrics[2].metric("质量问题", f"{len(quality.issues):,}")
        quality_metrics[3].metric("可分析行", f"{quality.row_count:,}")

        with st.expander("查看数据质量问题"):
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

        st.subheader("开始分析")
        default_question = (
            selected_dataset.suggested_questions[0]
            if source_mode == "练习数据"
            else "请概括这份数据，指出最值得关注的变化。"
        )
        planning_question = st.text_area(
            "想了解什么？",
            value=default_question,
            height=90,
            key=f"analysis_question_{dataset_key}",
            placeholder="例如：哪个地区的销售额最高？",
        )
        start_analysis = st.button(
            "开始分析",
            key="start_analysis",
            type="primary",
            use_container_width=True,
        )
        if start_analysis:
            selected_model = st.session_state.get("selected_ollama_model")
            st.session_state.pop("planning_result", None)
            st.session_state.pop("planning_question", None)
            st.session_state.pop("execution_result", None)
            st.session_state.pop("report_result", None)
            st.session_state.pop("query_intent", None)
            if not selected_model:
                st.warning("请先在侧边栏检测模型，并选择一个本地生成模型。")
            elif not planning_question.strip():
                st.warning("分析问题不能为空。")
            else:
                schema_context = [
                    {
                        "name": column.name,
                        "logical_type": column.logical_type,
                        "role": quality_by_name[column.name].role,
                        "sample_values": list(quality_by_name[column.name].sample_values),
                    }
                    for column in loaded.columns
                ]
                planning_engine = ReadOnlyQueryEngine(
                    loaded.dataframe,
                    max_rows=settings.max_query_rows,
                    max_result_bytes=settings.max_query_result_mb * 1024 * 1024,
                    timeout_seconds=settings.query_timeout_seconds,
                )
                try:
                    with OllamaClient(
                        settings.ollama_base_url,
                        timeout_seconds=settings.ollama_timeout_seconds,
                        temperature=STRUCTURED_TEMPERATURE,
                        max_output_tokens=settings.ollama_max_output_tokens,
                    ) as ollama:
                        with st.spinner(f"{selected_model} 正在理解问题并匹配字段..."):
                            intent_result = QueryIntentAnalyzer(
                                ollama,
                                selected_model,
                            ).analyze(planning_question, schema_context)
                        st.session_state["query_intent"] = intent_result
                        with st.spinner(f"{selected_model} 正在生成并校验分析计划..."):
                            planning_result = StructuredPlanner(
                                ollama,
                                selected_model,
                                max_repairs=1,
                            ).create_plan(
                                planning_question,
                                planning_engine,
                                schema_context,
                                intent=intent_result.intent if intent_result.success else None,
                            )
                finally:
                    planning_engine.close()

                st.session_state["planning_result"] = planning_result
                st.session_state["planning_question"] = planning_question
                if planning_result.success and planning_result.plan is not None:
                    execution_engine = ReadOnlyQueryEngine(
                        loaded.dataframe,
                        max_rows=settings.max_query_rows,
                        max_result_bytes=settings.max_query_result_mb * 1024 * 1024,
                        timeout_seconds=settings.query_timeout_seconds,
                    )
                    try:
                        with st.spinner("正在执行已校验的分析步骤..."):
                            execution_result = execute_analysis_plan(
                                planning_result.plan,
                                loaded,
                                execution_engine,
                            )
                    finally:
                        execution_engine.close()
                    st.session_state["execution_result"] = execution_result

                    if execution_result.success:
                        with st.spinner(f"{selected_model} 正在生成中文报告..."):
                            try:
                                with OllamaClient(
                                    settings.ollama_base_url,
                                    timeout_seconds=settings.ollama_timeout_seconds,
                                    temperature=STRUCTURED_TEMPERATURE,
                                    max_output_tokens=settings.ollama_max_output_tokens,
                                ) as ollama:
                                    report_result = ReportGenerator(
                                        ollama,
                                        selected_model,
                                        max_repairs=1,
                                    ).generate(
                                        planning_question,
                                        execution_result,
                                    )
                            except OllamaClientError as exc:
                                report_result = ReportGenerationResult(
                                    status="fallback",
                                    backend="fallback",
                                    model=selected_model,
                                    attempts=1,
                                    report=None,
                                    error_message=exc.message,
                                )
                        st.session_state["report_result"] = report_result

        planning_result = st.session_state.get("planning_result")
        if planning_result is not None:
            if not planning_result.success:
                st.error(
                    "暂时无法根据当前问题生成可靠的分析结果，请换一种说法再试。"
                )
            with st.expander("查看分析过程"):
                intent_result = st.session_state.get("query_intent")
                if isinstance(intent_result, QueryIntentResult) and intent_result.success and intent_result.intent:
                    intent = intent_result.intent
                    field_labels = {
                        f"column_{index}": column.name
                        for index, column in enumerate(loaded.columns)
                    }
                    matched_fields = [
                        field_labels.get(field, field)
                        for field in [*intent.dimensions, *intent.measures]
                    ]
                    st.write(f"已理解的问题：{intent.user_goal}")
                    if matched_fields:
                        st.caption("已匹配字段：" + "、".join(dict.fromkeys(matched_fields)))
                    if intent.calculation:
                        st.caption("计算方式：" + intent.calculation)
                if planning_result.success and planning_result.plan is not None:
                    st.success(
                        f"分析计划已通过校验：使用 {planning_result.model}，共请求 {planning_result.attempts} 次。"
                    )
                    st.json(planning_result.plan.model_dump())
                else:
                    st.write("分析计划未通过校验，详细原因如下：")
                    if planning_result.problems:
                        st.dataframe(
                            pd.DataFrame([problem.model_dump() for problem in planning_result.problems]),
                            use_container_width=True,
                            hide_index=True,
                        )

        with st.expander("查看字段详情"):
            st.dataframe(profile_frame, use_container_width=True, hide_index=True)

        with st.expander("查看数据样例"):
            st.dataframe(loaded.dataframe.head(5), use_container_width=True, hide_index=True)

        with st.expander("高级：只读 SQL 查询"):
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
                height=110,
                placeholder="例如：SELECT 地区, SUM(销售额) AS 总销售额 FROM uploaded_data GROUP BY 地区",
            )
            run_query = st.button("执行只读查询")

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

        execution_result = st.session_state.get("execution_result")
        if isinstance(execution_result, PlanExecutionResult):
            st.subheader("分析结果")
            if execution_result.success:
                st.success("分析完成，以下是根据数据计算出的结果。")
            else:
                st.error(execution_result.error_message or "分析未完成，暂时无法生成可信报告。")
            for step in execution_result.steps:
                status_label = {"success": "成功", "rejected": "拒绝", "error": "失败"}.get(
                    step.status,
                    step.status,
                )
                with st.expander(
                    f"查看第 {step.step_index + 1} 项结果（{status_label}）",
                    expanded=step.step_index == 0 and step.status == "success",
                ):
                    if step.status == "success":
                        if step.result.get("rows"):
                            rows = step.result["rows"]
                            st.dataframe(
                                pd.DataFrame(rows),
                                use_container_width=True,
                                hide_index=True,
                            )
                            st.download_button(
                                "下载此步骤 CSV",
                                data=rows_to_csv_bytes(rows),
                                file_name=f"datapilot-step-{step.step_index + 1}.csv",
                                mime="text/csv",
                                key=f"download_step_csv_{step.step_index}",
                            )
                        elif step.result.get("overview"):
                            st.json(step.result["overview"])
                        elif step.result.get("chart"):
                            render_chart_exports(
                                step.result["chart"],
                                f"plan_chart_{step.step_index}",
                            )
                    else:
                        st.error(step.record.error_message or "这一项没有返回结果。")

            table_steps = [
                step
                for step in execution_result.steps
                if step.success and isinstance(step.result.get("rows"), list) and step.result["rows"]
            ]
            if table_steps:
                with st.expander("查看图表", expanded=False):
                    step_labels = {
                        f"第 {step.step_index + 1} 项结果": step
                        for step in table_steps
                    }
                    selected_label = st.selectbox(
                        "选择结果",
                        list(step_labels),
                        key="chart_result_step",
                    )
                    selected_step = step_labels[selected_label]
                    selected_frame = pd.DataFrame(selected_step.result["rows"])
                    available_numeric = numeric_columns(selected_frame)
                    available_x = [
                        str(column)
                        for column in selected_frame.columns
                        if str(column) not in available_numeric
                    ] or [str(column) for column in selected_frame.columns]
                    if not available_numeric:
                        st.info("当前结果没有可用的数值字段，暂时无法绘制图表。")
                    else:
                        chart_columns = st.columns(3)
                        with chart_columns[0]:
                            selected_chart_type = st.selectbox(
                                "图表类型",
                                ["bar", "line", "pie"],
                                format_func={
                                    "bar": "柱状图",
                                    "line": "折线图",
                                    "pie": "饼图",
                                }.get,
                                key="manual_chart_type",
                            )
                        with chart_columns[1]:
                            selected_x_field = st.selectbox(
                                "横轴字段",
                                available_x,
                                key="manual_chart_x_field",
                            )
                        with chart_columns[2]:
                            selected_y_field = st.selectbox(
                                "纵轴字段",
                                available_numeric,
                                key="manual_chart_y_field",
                            )
                        selected_title = st.text_input(
                            "图表标题",
                            value=f"{selected_x_field}与{selected_y_field}分析",
                            key="manual_chart_title",
                        )
                        manual_chart = build_chart(
                            selected_frame,
                            {
                                "chart_type": selected_chart_type,
                                "x_field": selected_x_field,
                                "y_field": selected_y_field,
                                "title": selected_title,
                                "limit": 100,
                            },
                        )
                        if manual_chart.success and manual_chart.chart is not None:
                            render_chart_exports(
                                manual_chart.chart.model_dump(),
                                "manual_chart",
                            )
                        else:
                            st.warning(manual_chart.record.error_message or "当前字段无法生成图表。")

            st.download_button(
                "下载 Markdown 分析报告",
                data=build_markdown_report(
                    st.session_state.get("planning_question", planning_question),
                    execution_result,
                    st.session_state.get("report_result"),
                ).encode("utf-8"),
                file_name="datapilot-analysis-report.md",
                mime="text/markdown",
                key="download_markdown_report",
            )

        report_result = st.session_state.get("report_result")
        if isinstance(report_result, ReportGenerationResult):
            st.subheader("中文分析报告")
            if report_result.report is None:
                st.error(report_result.error_message or "报告生成失败。")
            else:
                if report_result.success:
                    st.success(
                        f"报告由 {report_result.model} 生成，共尝试 {report_result.attempts} 次。"
                    )
                elif report_result.status == "fallback":
                    st.warning(
                        "本地模型报告未生成，下面只展示安全降级说明和真实工具证据。"
                    )
                st.markdown(f"### {report_result.report.title}")
                st.write(report_result.report.summary)
                if report_result.report.findings:
                    st.markdown("**关键事实**")
                    for finding in report_result.report.findings:
                        st.write(f"- {finding}")
                if report_result.report.limitations:
                    st.markdown("**数据限制**")
                    for limitation in report_result.report.limitations:
                        st.write(f"- {limitation}")
                st.caption(f"数据依据：第 {report_result.report.evidence_steps or '无'} 项分析结果")
