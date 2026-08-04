"""DataPilot 的结构化分析计划解析、校验和一次修复流程。"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.ollama_client import ChatResult, OllamaClient, OllamaClientError
from src.query_engine import ReadOnlyQueryEngine
from src.tools import (
    AnomalyRequest,
    ChartRequest,
    GroupBySummaryRequest,
    OverviewRequest,
    ReadonlySQLRequest,
)

ToolName = Literal[
    "get_data_overview",
    "run_readonly_sql",
    "group_by_summary",
    "detect_anomalies",
    "build_chart",
]

PLANNER_SYSTEM_PROMPT = """你是 DataPilot 的中文数据分析计划器。
你只能输出一个 JSON 对象，不要输出 Markdown、解释、代码或最终分析结论。
计划只允许调用以下工具：get_data_overview、run_readonly_sql、group_by_summary、detect_anomalies、build_chart。
工具参数必须使用给定字段名；不能执行 Python、系统命令、写入型 SQL 或访问上传表之外的数据。
如果用户问题无法由当前字段支持，仍然生成最小计划，交给系统校验，不要编造数据。
规划前会提供一份问题意图理解结果，它只能作为字段选择提示，最终仍必须以当前字段白名单为准。
JSON 顶层字段必须是 user_goal、steps、report_focus。
每个 steps 元素必须包含 tool、parameters、expected_output。
工具参数契约必须严格遵守：
- 所有工具参数中的字段名必须使用用户消息提供的 ASCII field_key（例如 column_0、column_1），不要直接填写中文显示名；系统会在校验前映射回真实字段名。
- 单个字段参数只能填写一个 field_key，不能写成 column_2,column_3；需要多个字段时使用 run_readonly_sql。
- get_data_overview：parameters 只能使用 include_quality_issues（布尔值）。
- run_readonly_sql：parameters 必须使用 sql 字符串，SQL 只能读取 uploaded_data。
- group_by_summary：parameters 必须使用 group_by、metric、aggregation、sort_direction、limit；aggregation 只能是 count、sum、avg、min、max。
- 如果问题要求按地区、渠道或类别比较销售额、收入、金额等总量，aggregation 必须使用 sum；只有用户明确询问单条记录的最大值时才使用 max。
- detect_anomalies：parameters 必须使用单个 metric 字段、method（只能是 iqr）、iqr_multiplier、limit；不要使用 columns 数组。
- build_chart：parameters 必须使用 chart_type、x_field、y_field、title、limit；chart_type 只能是 bar、line、pie。
只选择能够回答用户问题的步骤，不要默认添加异常检测或概览步骤。
例如用户问题为“哪个地区的销售额最高？”，合法计划可以只有一个步骤：
{"user_goal":"比较各地区销售额并找出最高地区","steps":[{"tool":"group_by_summary","parameters":{"group_by":"column_0","metric":"column_1","aggregation":"sum","sort_direction":"desc","limit":10},"expected_output":"各地区销售额汇总并按降序排列"}],"report_focus":["最高地区","销售额"]}
"""


class AnalysisStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: ToolName
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_output: str = Field(min_length=1, max_length=200)


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_goal: str = Field(min_length=1, max_length=300)
    steps: list[AnalysisStep] = Field(min_length=1, max_length=5)
    report_focus: list[str] = Field(min_length=1, max_length=6)


class PlanProblem(BaseModel):
    code: str
    message: str
    step_index: int | None = None


class PlanParseResult(BaseModel):
    success: bool
    plan: AnalysisPlan | None = None
    problems: list[PlanProblem] = Field(default_factory=list)
    normalized_json: str | None = None


class PlanValidationResult(BaseModel):
    success: bool
    plan: AnalysisPlan | None = None
    problems: list[PlanProblem] = Field(default_factory=list)


class PlanningResult(BaseModel):
    status: Literal["success", "error"]
    plan: AnalysisPlan | None = None
    model: str | None = None
    attempts: int = Field(ge=0)
    problems: list[PlanProblem] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == "success"


def parse_plan_text(text: str) -> PlanParseResult:
    """从纯 JSON、代码块或带额外说明的文本中解析计划。"""

    if not isinstance(text, str) or not text.strip():
        return _parse_error("EMPTY_PLAN", "模型没有返回分析计划。")
    payload = _extract_json_object(text)
    if payload is None:
        return _parse_error("INVALID_JSON", "模型输出中没有找到有效的 JSON 对象。")
    payload = _coerce_common_model_shapes(payload)
    try:
        plan = AnalysisPlan.model_validate(payload)
    except ValidationError:
        return _parse_error("PLAN_SCHEMA_INVALID", "模型输出不符合分析计划结构。")
    return PlanParseResult(
        success=True,
        plan=plan,
        normalized_json=json.dumps(plan.model_dump(), ensure_ascii=False, separators=(",", ":")),
    )


def validate_analysis_plan(
    plan: AnalysisPlan | dict[str, Any],
    engine: ReadOnlyQueryEngine,
) -> PlanValidationResult:
    """校验工具参数、字段白名单和 SQL 安全性，不执行任何分析工具。"""

    try:
        parsed_plan = AnalysisPlan.model_validate(plan)
    except ValidationError:
        return PlanValidationResult(
            success=False,
            problems=[PlanProblem(code="PLAN_SCHEMA_INVALID", message="分析计划结构不合法。")],
        )

    problems: list[PlanProblem] = []
    for index, step in enumerate(parsed_plan.steps):
        problems.extend(_validate_step(step, index, engine))
    if problems:
        return PlanValidationResult(success=False, problems=problems)
    return PlanValidationResult(success=True, plan=parsed_plan)


def build_planner_prompt(
    question: str,
    schema: Sequence[dict[str, Any]],
    intent: Mapping[str, Any] | None = None,
) -> str:
    """构造只包含字段元数据的规划请求，不把原始业务数据送入模型。"""

    field_lines = []
    for index, field in enumerate(schema):
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        field_lines.append(
            f"- field_key=column_{index}; display_name={name}; "
            f"logical_type={field.get('logical_type', 'unknown')}; role={field.get('role', 'unknown')}"
        )
    fields_text = "\n".join(field_lines) or "- 没有可用字段"
    intent_text = ""
    if intent:
        intent_text = (
            "\n\n前置问题意图理解（仅作参考，不能绕过字段白名单）：\n"
            f"{json.dumps(dict(intent), ensure_ascii=False)}\n"
            "如果 calculation 不为空，或 dimensions/ measures 包含多个字段，优先使用只读 SQL 完成计算。"
        )
    return (
        f"当前用户问题：{question.strip()}\n\n"
        "当前上传数据的字段元数据（工具参数必须使用 field_key）：\n"
        f"{fields_text}{intent_text}\n\n"
        "请根据问题和前置意图生成 JSON 分析计划。优先使用专用工具；只有需要灵活筛选或计算多个字段时才使用 run_readonly_sql。"
        "每个步骤写清 expected_output，report_focus 只写最终报告需要关注的事实。"
    )


class StructuredPlanner:
    """调用本地模型生成计划，并最多自动修复一次格式或参数错误。"""

    def __init__(self, client: OllamaClient, model: str, *, max_repairs: int = 1) -> None:
        if max_repairs < 0 or max_repairs > 1:
            raise ValueError("阶段 9 最多允许修复一次")
        self.client = client
        self.model = model
        self.max_repairs = max_repairs

    def create_plan(
        self,
        question: str,
        engine: ReadOnlyQueryEngine,
        schema: Sequence[dict[str, Any]],
        intent: Mapping[str, Any] | BaseModel | None = None,
    ) -> PlanningResult:
        if not isinstance(question, str) or not question.strip():
            return PlanningResult(
                status="error",
                model=self.model,
                attempts=0,
                problems=[PlanProblem(code="EMPTY_QUESTION", message="分析问题不能为空。")],
            )

        intent_payload: Mapping[str, Any] | None = None
        if intent is not None:
            if isinstance(intent, BaseModel):
                intent_payload = intent.model_dump()
            elif isinstance(intent, Mapping):
                intent_payload = intent
        original_prompt = build_planner_prompt(question, schema, intent_payload)
        if intent_payload:
            deterministic_plan = _build_plan_from_intent(intent_payload)
            if deterministic_plan is not None:
                normalized_plan = normalize_plan_fields(deterministic_plan, schema)
                validated = validate_analysis_plan(normalized_plan, engine)
                if validated.success:
                    return PlanningResult(
                        status="success",
                        plan=validated.plan,
                        model=self.model,
                        attempts=1,
                    )
        repair_context = ""
        problems: list[PlanProblem] = []
        for attempt in range(self.max_repairs + 1):
            messages = [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": original_prompt + repair_context},
            ]
            try:
                chat_result = self.client.chat(
                    self.model,
                    messages,
                    # Ollama 的 JSON 模式兼容性比复杂 oneOf grammar 更稳定，字段安全性由 Pydantic 完成。
                    response_format="json",
                    think=False,
                )
            except OllamaClientError as exc:
                return PlanningResult(
                    status="error",
                    model=self.model,
                    attempts=attempt + 1,
                    problems=[PlanProblem(code=exc.code, message=exc.message)],
                )

            parsed = parse_plan_text(chat_result.content)
            if parsed.success and parsed.plan is not None:
                normalized_plan = normalize_plan_fields(parsed.plan, schema)
                validated = validate_analysis_plan(normalized_plan, engine)
                if validated.success:
                    return PlanningResult(
                        status="success",
                        plan=validated.plan,
                        model=chat_result.model,
                        attempts=attempt + 1,
                    )
                problems = validated.problems
            else:
                problems = parsed.problems

            if attempt >= self.max_repairs:
                break
            repair_context = _repair_prompt(problems, chat_result)

        return PlanningResult(
            status="error",
            model=self.model,
            attempts=self.max_repairs + 1,
            problems=problems,
        )


def _validate_step(step: AnalysisStep, index: int, engine: ReadOnlyQueryEngine) -> list[PlanProblem]:
    request_models = {
        "get_data_overview": OverviewRequest,
        "run_readonly_sql": ReadonlySQLRequest,
        "group_by_summary": GroupBySummaryRequest,
        "detect_anomalies": AnomalyRequest,
        "build_chart": ChartRequest,
    }
    request_model = request_models[step.tool]
    try:
        request = request_model.model_validate(step.parameters)
    except ValidationError:
        return [PlanProblem(code="TOOL_PARAMETERS_INVALID", message="工具参数不合法。", step_index=index)]

    if step.tool == "run_readonly_sql":
        validation = engine.validate_sql(request.sql)
        if not validation.approved:
            return [PlanProblem(code=validation.code, message=validation.message, step_index=index)]
    elif step.tool == "group_by_summary":
        problems = _validate_columns(engine, index, request.group_by, request.metric)
        if problems:
            return problems
        if request.aggregation != "count" and not engine.is_numeric_column(request.metric or ""):
            return [PlanProblem(code="METRIC_NOT_NUMERIC", message="分组统计的度量字段必须是数值字段。", step_index=index)]
    elif step.tool == "detect_anomalies":
        problems = _validate_columns(engine, index, request.metric)
        if problems:
            return problems
        if not engine.is_numeric_column(request.metric):
            return [PlanProblem(code="METRIC_NOT_NUMERIC", message="异常检测字段必须是数值字段。", step_index=index)]
    elif step.tool == "build_chart":
        problems = _validate_columns(engine, index, request.x_field, request.y_field)
        if problems:
            return problems
        if not engine.is_numeric_column(request.y_field):
            return [PlanProblem(code="Y_FIELD_NOT_NUMERIC", message="图表纵轴字段必须是数值字段。", step_index=index)]
    return []


def _validate_columns(engine: ReadOnlyQueryEngine, index: int, *columns: str | None) -> list[PlanProblem]:
    missing = [column for column in columns if column and column not in engine.column_names]
    if not missing:
        return []
    return [PlanProblem(code="INVALID_FIELD", message=f"字段不存在：{', '.join(missing)}。", step_index=index)]


def normalize_plan_fields(
    plan: AnalysisPlan | dict[str, Any],
    schema: Sequence[dict[str, Any]],
) -> AnalysisPlan:
    """把模型使用的 ASCII 字段别名映射回上传数据中的真实字段名。"""

    parsed_plan = AnalysisPlan.model_validate(plan)
    aliases = {
        f"column_{index}": str(field.get("name") or "").strip()
        for index, field in enumerate(schema)
        if str(field.get("name") or "").strip()
    }
    column_aliases = dict(aliases)
    aliases.update({_normalize_field_label(name): name for name in aliases.values()})
    field_meta = {
        str(field.get("name") or "").strip(): field
        for field in schema
        if str(field.get("name") or "").strip()
    }
    payload = parsed_plan.model_dump()
    for step in payload["steps"]:
        parameters = step["parameters"]
        if step["tool"] == "run_readonly_sql":
            sql = parameters.get("sql")
            if isinstance(sql, str):
                parameters["sql"] = _replace_field_aliases(sql, column_aliases)
        else:
            for key in ("group_by", "metric", "x_field", "y_field"):
                if key in parameters and isinstance(parameters[key], str):
                    preferred_role = "numeric" if key in {"metric", "y_field"} else "dimension"
                    parameters[key] = _resolve_plan_field(
                        parameters[key],
                        aliases,
                        field_meta,
                        preferred_role,
                    )
    return AnalysisPlan.model_validate(payload)


def _resolve_plan_field(
    value: str,
    aliases: Mapping[str, str],
    field_meta: Mapping[str, Mapping[str, Any]],
    preferred_role: str,
) -> str:
    """修复小模型把多个候选拼在一起或使用近似字段名的情况。"""

    raw_values = [part.strip() for part in re.split(r"[,，、/\\|]", value) if part.strip()]
    resolved: list[str] = []
    for raw_value in raw_values:
        direct = aliases.get(raw_value) or aliases.get(_normalize_field_label(raw_value))
        if direct:
            resolved.append(direct)
            continue

        normalized = _normalize_field_label(raw_value)
        best_name = None
        best_score = 0.0
        for name in field_meta:
            score = _similarity(normalized, _normalize_field_label(name))
            if score > best_score:
                best_name = name
                best_score = score
        if best_name is not None and best_score >= 0.72:
            resolved.append(best_name)

    if not resolved:
        return value
    if preferred_role == "numeric":
        for field_name in resolved:
            metadata = field_meta.get(field_name, {})
            if metadata.get("logical_type") == "numeric" or metadata.get("role") == "measure":
                return field_name
    else:
        for field_name in resolved:
            metadata = field_meta.get(field_name, {})
            if metadata.get("logical_type") != "numeric" and metadata.get("role") != "measure":
                return field_name
    return resolved[0]


def _normalize_field_label(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.lower())


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 0.9
    return SequenceMatcher(None, left, right).ratio()


def _replace_field_aliases(sql: str, aliases: Mapping[str, str]) -> str:
    """只替换完整 alias 标识，避免修改 SQL 中的普通文本。"""

    for alias, field_name in aliases.items():
        quoted_name = '"' + field_name.replace('"', '""') + '"'
        sql = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])", quoted_name, sql)
    return sql


def _extract_json_object(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for index, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return None


def _parse_error(code: str, message: str) -> PlanParseResult:
    return PlanParseResult(success=False, problems=[PlanProblem(code=code, message=message)])


def _coerce_common_model_shapes(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容小模型常见的无歧义变体，之后仍交给 Pydantic 和工具校验。"""

    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(normalized.get("report_focus"), str):
        normalized["report_focus"] = [normalized["report_focus"]]

    for step in normalized.get("steps", []):
        if not isinstance(step, dict) or not isinstance(step.get("parameters"), dict):
            continue
        if not isinstance(step.get("expected_output"), str) or not step["expected_output"].strip():
            step["expected_output"] = f"{step.get('tool', '工具')} 执行结果"
        parameters = step["parameters"]
        for key in ("group_by", "metric", "x_field", "y_field", "aggregation"):
            value = parameters.get(key)
            if isinstance(value, list) and len(value) == 1:
                parameters[key] = value[0]
            elif isinstance(value, list) and value:
                parameters[key] = ",".join(str(item) for item in value)

        if step.get("tool") == "group_by_summary":
            aggregation = parameters.get("aggregation")
            if isinstance(aggregation, str):
                match = re.fullmatch(r"(count|sum|avg|min|max)(?:\(([^()]+)\))?", aggregation.strip(), re.IGNORECASE)
                if match:
                    parameters["aggregation"] = match.group(1).lower()
                    if match.group(2) and not parameters.get("metric"):
                        parameters["metric"] = match.group(2).strip()
            columns = parameters.pop("columns", None)
            if not parameters.get("metric") and isinstance(columns, list) and len(columns) == 1:
                parameters["metric"] = columns[0]
    return normalized


def _repair_prompt(problems: list[PlanProblem], chat_result: ChatResult) -> str:
    problem_text = "；".join(problem.message for problem in problems)
    previous = chat_result.content[:12_000]
    return (
        "\n\n上一次输出未通过系统校验。请只输出修复后的 JSON，不要解释。"
        f"失败原因：{problem_text}\n上一次输出：{previous}"
    )


def _build_plan_from_intent(intent: Mapping[str, Any]) -> AnalysisPlan | None:
    """把低歧义意图转换为受控工具计划，减少小模型二次编排时的语义漂移。"""

    dimensions = [str(value) for value in intent.get("dimensions") or [] if str(value).strip()]
    measures = [str(value) for value in intent.get("measures") or [] if str(value).strip()]
    filters = [str(value) for value in intent.get("filters") or [] if str(value).strip()]
    aggregation = str(intent.get("aggregation") or "unknown")
    calculation = str(intent.get("calculation") or "")
    if calculation == "筛选出勤状态" and filters:
        condition = _parse_controlled_filter(filters[0])
        if condition is not None:
            field, operator, value = condition
            selected_fields = list(dict.fromkeys([*dimensions, field]))
            select_sql = ", ".join(selected_fields) or "*"
            escaped_value = value.replace("'", "''")
            sql = (
                f"SELECT {select_sql} FROM uploaded_data "
                f"WHERE {field} {operator} '{escaped_value}' LIMIT 50"
            )
            return AnalysisPlan(
                user_goal=str(intent.get("user_goal") or "查询出勤状态"),
                steps=[
                    AnalysisStep(
                        tool="run_readonly_sql",
                        parameters={"sql": sql},
                        expected_output="返回符合出勤状态条件的人员记录",
                    )
                ],
                report_focus=["人员记录", "出勤状态"],
            )

    if filters or len(dimensions) > 2:
        return None

    if calculation == "销售额减成本" and len(measures) >= 2:
        select_fields = ""
        group_clause = ""
        if dimensions:
            select_fields = ", ".join(
                f'{field} AS "分组{i + 1}"' for i, field in enumerate(dimensions)
            ) + ", "
            group_clause = f" GROUP BY {', '.join(dimensions)}"
        sql = (
            f'SELECT {select_fields}SUM({measures[0]} - {measures[1]}) AS "利润" '
            f'FROM uploaded_data{group_clause} ORDER BY "利润" DESC LIMIT 20'
        )
        return AnalysisPlan(
            user_goal=str(intent.get("user_goal") or "计算利润"),
            steps=[
                AnalysisStep(
                    tool="run_readonly_sql",
                    parameters={"sql": sql},
                    expected_output="根据销售额减成本计算利润",
                )
            ],
            report_focus=["利润", "数据范围"],
        )

    if len(dimensions) == 1 and len(measures) <= 1 and aggregation in {"count", "sum", "avg", "min", "max"}:
        parameters: dict[str, Any] = {
            "group_by": dimensions[0],
            "aggregation": aggregation,
            "sort_direction": "desc",
            "limit": 20,
        }
        if aggregation != "count" and measures:
            parameters["metric"] = measures[0]
        elif aggregation != "count":
            return None
        return AnalysisPlan(
            user_goal=str(intent.get("user_goal") or "按维度汇总数据"),
            steps=[
                AnalysisStep(
                    tool="group_by_summary",
                    parameters=parameters,
                    expected_output="按指定维度汇总并排序",
                )
            ],
            report_focus=[str(intent.get("user_goal") or "汇总结果")],
        )

    if not dimensions and len(measures) == 1 and aggregation in {"sum", "avg", "min", "max"}:
        sql = f'SELECT {aggregation.upper()}({measures[0]}) AS "结果" FROM uploaded_data'
        return AnalysisPlan(
            user_goal=str(intent.get("user_goal") or "汇总数据"),
            steps=[
                AnalysisStep(
                    tool="run_readonly_sql",
                    parameters={"sql": sql},
                    expected_output="返回指定指标的汇总结果",
                )
            ],
            report_focus=[str(intent.get("user_goal") or "汇总结果")],
        )
    return None


def _parse_controlled_filter(value: str) -> tuple[str, str, str] | None:
    """只接受意图模块生成的单字段等值筛选，避免模型拼接任意 SQL。"""

    match = re.fullmatch(r"\s*(column_\d+)\s*(==|!=|=)\s*(.+?)\s*", str(value))
    if not match:
        return None
    operator = "=" if match.group(2) in {"==", "="} else "<>"
    literal = match.group(3).strip().strip("'\"")
    if not literal or len(literal) > 80:
        return None
    return match.group(1), operator, literal
