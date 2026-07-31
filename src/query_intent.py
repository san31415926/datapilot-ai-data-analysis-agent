"""DataPilot 的自然语言问题理解与字段语义匹配。"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.ollama_client import OllamaClient, OllamaClientError


class QueryIntent(BaseModel):
    """模型对用户问题的结构化理解。字段引用统一使用 column_N。"""

    model_config = ConfigDict(extra="forbid")

    user_goal: str = Field(min_length=1, max_length=300)
    intent_type: Literal[
        "overview",
        "group_summary",
        "trend",
        "detail",
        "anomaly",
        "unknown",
    ] = "unknown"
    dimensions: list[str] = Field(default_factory=list, max_length=3)
    measures: list[str] = Field(default_factory=list, max_length=4)
    aggregation: Literal["count", "sum", "avg", "min", "max", "unknown"] = "unknown"
    filters: list[str] = Field(default_factory=list, max_length=5)
    calculation: str = Field(default="", max_length=200)


class IntentProblem(BaseModel):
    code: str
    message: str


class QueryIntentResult(BaseModel):
    status: Literal["success", "error"]
    intent: QueryIntent | None = None
    model: str | None = None
    attempts: int = Field(ge=0)
    problems: list[IntentProblem] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == "success"


INTENT_SYSTEM_PROMPT = """你是 DataPilot 的中文数据分析问题理解器。
你只负责理解用户想查什么，不执行查询，也不回答最终问题。
只能输出一个 JSON 对象，不要输出 Markdown、解释或代码。
JSON 顶层字段必须是 user_goal、intent_type、dimensions、measures、aggregation、filters、calculation。
dimensions 和 measures 只能填写给定字段候选中的 field_key，例如 column_2；不要发明字段名。
如果一个参数需要多个字段，必须输出 JSON 数组，不能把多个 field_key 拼成一个字符串。
intent_type 只能是 overview、group_summary、trend、detail、anomaly、unknown。
aggregation 只能是 count、sum、avg、min、max、unknown。
用户说“赚多少钱、利润、盈利、净赚”时，如果字段中同时存在销售额或收入与成本，measures 应同时选择这两个字段，calculation 写成“销售额减成本”；后续系统会用只读查询计算。
用户说收入、营收、流水、销售金额时，优先匹配销售额或收入字段；用户说销量、件数时，优先匹配数量字段。
没有足够字段支持的问题，返回空数组，不要猜测。"""


def build_intent_prompt(question: str, schema: Sequence[dict[str, Any]]) -> str:
    """构造问题理解提示，只提供字段目录和少量样例值。"""

    field_lines: list[str] = []
    for index, field in enumerate(schema):
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        samples = field.get("sample_values") or []
        sample_text = "、".join(str(value) for value in list(samples)[:3]) or "无"
        field_lines.append(
            f"- field_key=column_{index}; display_name={name}; "
            f"logical_type={field.get('logical_type', 'unknown')}; "
            f"role={field.get('role', 'unknown')}; samples={sample_text}"
        )
    fields_text = "\n".join(field_lines) or "- 没有可用字段"
    return (
        f"用户问题：{question.strip()}\n\n"
        "当前数据字段候选：\n"
        f"{fields_text}\n\n"
        "请先识别用户目标、维度、度量和聚合方式。字段只从候选中选择。"
    )


class QueryIntentAnalyzer:
    """用一次本地模型调用提取问题意图，失败时交给规划器兜底。"""

    def __init__(self, client: OllamaClient, model: str) -> None:
        self.client = client
        self.model = model

    def analyze(
        self,
        question: str,
        schema: Sequence[dict[str, Any]],
    ) -> QueryIntentResult:
        if not isinstance(question, str) or not question.strip():
            return QueryIntentResult(
                status="error",
                model=self.model,
                attempts=0,
                problems=[IntentProblem(code="EMPTY_QUESTION", message="分析问题不能为空。")],
            )

        try:
            chat_result = self.client.chat(
                self.model,
                [
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": build_intent_prompt(question, schema)},
                ],
                response_format="json",
                think=False,
            )
        except OllamaClientError as exc:
            return QueryIntentResult(
                status="error",
                model=self.model,
                attempts=1,
                problems=[IntentProblem(code=exc.code, message=exc.message)],
            )

        parsed = parse_intent_text(chat_result.content)
        if not parsed.success or parsed.intent is None:
            fallback = infer_rule_based_intent(question, schema)
            if fallback is not None:
                return QueryIntentResult(
                    status="success",
                    intent=fallback,
                    model=chat_result.model,
                    attempts=1,
                )
            return QueryIntentResult(
                status="error",
                model=chat_result.model,
                attempts=1,
                problems=parsed.problems,
            )

        normalized = normalize_intent_fields(parsed.intent, schema, question=question)
        if _needs_rule_fallback(question, normalized):
            fallback = infer_rule_based_intent(question, schema)
            if fallback is not None:
                normalized = fallback
        return QueryIntentResult(
            status="success",
            intent=normalized,
            model=chat_result.model,
            attempts=1,
        )


class IntentParseResult(BaseModel):
    success: bool
    intent: QueryIntent | None = None
    problems: list[IntentProblem] = Field(default_factory=list)


def parse_intent_text(text: str) -> IntentParseResult:
    """兼容纯 JSON、代码块和小模型常见字段别名。"""

    if not isinstance(text, str) or not text.strip():
        return _intent_error("EMPTY_INTENT", "模型没有返回问题理解结果。")
    payload = _extract_json_object(text)
    if payload is None:
        return _intent_error("INVALID_INTENT_JSON", "模型输出中没有找到有效的问题理解 JSON。")
    payload = _coerce_intent_shapes(payload)
    try:
        intent = QueryIntent.model_validate(payload)
    except ValidationError:
        return _intent_error("INTENT_SCHEMA_INVALID", "问题理解结果结构不合法。")
    return IntentParseResult(success=True, intent=intent)


def normalize_intent_fields(
    intent: QueryIntent | dict[str, Any],
    schema: Sequence[dict[str, Any]],
    *,
    question: str | None = None,
) -> QueryIntent:
    """将字段显示名、近似名称和 column_N 统一为安全的字段 key。"""

    parsed = QueryIntent.model_validate(intent)
    aliases = _build_aliases(schema)

    def resolve_many(values: Sequence[str]) -> list[str]:
        resolved: list[str] = []
        for value in values:
            for token in _split_field_references(value):
                field_key = _resolve_field(token, aliases, schema)
                if field_key and field_key not in resolved:
                    resolved.append(field_key)
        return resolved

    payload = parsed.model_dump()
    payload["dimensions"] = resolve_many(parsed.dimensions)
    payload["measures"] = resolve_many(parsed.measures)

    # “能赚多少钱”通常没有名为“利润”的原始列，自动转换为收入/销售额减成本。
    intent_text = question or parsed.user_goal
    if _contains_profit_words(intent_text):
        revenue = _find_field_by_terms(schema, ("销售额", "收入", "营收", "营业额", "流水"))
        cost = _find_field_by_terms(schema, ("成本", "费用", "支出", "花费"))
        if revenue and cost:
            payload["measures"] = [revenue, cost]
            payload["calculation"] = "销售额减成本"
            payload["aggregation"] = "sum"
            if not _contains_dimension_words(intent_text):
                payload["dimensions"] = []

    return QueryIntent.model_validate(payload)


def infer_rule_based_intent(
    question: str,
    schema: Sequence[dict[str, Any]],
) -> QueryIntent | None:
    """为小模型输出异常时提供可解释的常见中文查询兜底。"""

    text = question.strip()
    if _contains_profit_words(text):
        revenue = _find_field_by_terms(schema, ("销售额", "收入", "营收", "营业额", "流水"))
        cost = _find_field_by_terms(schema, ("成本", "费用", "支出", "花费"))
        if revenue and cost:
            return QueryIntent(
                user_goal=text,
                intent_type="group_summary",
                dimensions=_find_dimension_fields(text, schema),
                measures=[revenue, cost],
                aggregation="sum",
                calculation="销售额减成本",
            )

    dimension_fields = _find_dimension_fields(text, schema)
    measure = _find_measure_field(text, schema)
    if measure and (dimension_fields or any(word in text for word in ("多少", "总计", "合计", "总和"))):
        return QueryIntent(
            user_goal=text,
            intent_type="group_summary" if dimension_fields else "overview",
            dimensions=dimension_fields,
            measures=[measure],
            aggregation="sum" if any(word in text for word in ("多少", "总计", "合计", "总和")) else "unknown",
        )
    return None


def _needs_rule_fallback(question: str, intent: QueryIntent) -> bool:
    if _contains_profit_words(question):
        return intent.calculation != "销售额减成本" or len(intent.measures) < 2
    return len(intent.dimensions) > 2 or len(intent.measures) > 3


def _find_dimension_fields(question: str, schema: Sequence[dict[str, Any]]) -> list[str]:
    groups = (
        (("地区", "区域", "省份", "城市"), ("地区", "区域", "省份", "城市")),
        (("渠道", "平台", "来源"), ("渠道", "平台", "来源")),
        (("类别", "分类"), ("类别", "分类")),
        (("客户", "用户"), ("客户", "用户")),
    )
    fields: list[str] = []
    for question_terms, field_terms in groups:
        if any(term in question for term in question_terms):
            field = _find_field_by_terms(schema, field_terms)
            if field:
                fields.append(field)
    return fields


def _find_measure_field(question: str, schema: Sequence[dict[str, Any]]) -> str | None:
    term_groups = (
        ("销售额", "收入", "营收", "营业额", "流水", "金额"),
        ("数量", "销量", "件数"),
        ("成本", "费用", "支出"),
    )
    for terms in term_groups:
        if any(term in question for term in terms):
            field = _find_field_by_terms(schema, terms)
            if field:
                return field
    return None


def _contains_dimension_words(value: str) -> bool:
    return any(word in value for word in ("按地区", "按区域", "按渠道", "按平台", "按类别", "各地区", "各渠道"))


def _build_aliases(schema: Sequence[dict[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for index, field in enumerate(schema):
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        key = f"column_{index}"
        aliases[key] = key
        aliases[_normalize_label(name)] = key
    return aliases


def _resolve_field(token: str, aliases: dict[str, str], schema: Sequence[dict[str, Any]]) -> str | None:
    raw = token.strip()
    if not raw:
        return None
    if raw in aliases:
        return aliases[raw]
    normalized = _normalize_label(raw)
    if normalized in aliases:
        return aliases[normalized]

    semantic_groups = (
        ("收入", "营收", "流水", "营业额", "销售额", "金额"),
        ("成本", "费用", "支出", "花费"),
        ("数量", "件数", "销量"),
        ("地区", "区域", "省份", "城市"),
        ("渠道", "来源", "平台"),
    )
    for group in semantic_groups:
        if any(word in raw for word in group):
            for index, field in enumerate(schema):
                name = str(field.get("name") or "")
                if any(word in name for word in group):
                    return f"column_{index}"

    best_key: str | None = None
    best_score = 0.0
    for index, field in enumerate(schema):
        name = _normalize_label(str(field.get("name") or ""))
        if not name:
            continue
        score = SequenceMatcher(None, normalized, name).ratio()
        if score > best_score:
            best_score = score
            best_key = f"column_{index}"
    return best_key if best_score >= 0.72 else None


def _find_field_by_terms(schema: Sequence[dict[str, Any]], terms: Sequence[str]) -> str | None:
    for index, field in enumerate(schema):
        name = str(field.get("name") or "")
        if any(term in name for term in terms):
            return f"column_{index}"
    return None


def _split_field_references(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，、/\\|]", str(value)) if part.strip()]


def _normalize_label(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.lower())


def _contains_profit_words(value: str) -> bool:
    return any(
        word in value
        for word in ("利润", "盈利", "赚钱", "赚了", "净赚", "毛利", "赚多少", "赚多少钱", "能赚", "挣多少", "获利")
    )


def _coerce_intent_shapes(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    aliases = {
        "analysis_type": "intent_type",
        "type": "intent_type",
        "group_by": "dimensions",
        "dimension": "dimensions",
        "metric": "measures",
        "metrics": "measures",
    }
    for source, target in aliases.items():
        if target not in normalized and source in normalized:
            normalized[target] = normalized[source]
        normalized.pop(source, None)

    for key in ("dimensions", "measures", "filters"):
        value = normalized.get(key, [])
        if value is None:
            normalized[key] = []
        elif isinstance(value, str):
            normalized[key] = _split_field_references(value) if key != "filters" else [value]
        elif not isinstance(value, list):
            normalized[key] = [str(value)]
        else:
            normalized[key] = [str(item) for item in value if str(item).strip()]

    intent_type = str(normalized.get("intent_type") or "unknown").strip().lower()
    intent_type = {
        "汇总": "group_summary",
        "聚合": "group_summary",
        "比较": "group_summary",
        "趋势": "trend",
        "明细": "detail",
        "异常": "anomaly",
    }.get(intent_type, intent_type)
    normalized["intent_type"] = intent_type if intent_type in {
        "overview", "group_summary", "trend", "detail", "anomaly", "unknown"
    } else "unknown"

    aggregation_value = normalized.get("aggregation")
    if isinstance(aggregation_value, list) and len(aggregation_value) == 1:
        aggregation_value = aggregation_value[0]
    aggregation = str(aggregation_value or "unknown").strip().lower()
    match = re.fullmatch(r"(count|sum|avg|min|max)(?:\([^()]+\))?", aggregation)
    normalized["aggregation"] = match.group(1) if match else aggregation if aggregation in {
        "count", "sum", "avg", "min", "max", "unknown"
    } else "unknown"
    if not str(normalized.get("user_goal") or "").strip():
        normalized["user_goal"] = "未识别用户目标"
    normalized.setdefault("calculation", "")
    return normalized


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


def _intent_error(code: str, message: str) -> IntentParseResult:
    return IntentParseResult(
        success=False,
        problems=[IntentProblem(code=code, message=message)],
    )
