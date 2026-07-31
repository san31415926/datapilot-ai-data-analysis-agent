"""运行固定评估集的结构化计划检查。

用法：
    python scripts/run_evaluation.py
    python scripts/run_evaluation.py --plans path/to/plan_outputs.json

plan_outputs.json 格式：
{
  "E01": {"success": true, "plan": {"user_goal": "...", "steps": [...], "report_focus": [...]}},
  "E17": {"success": false, "plan": null}
}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation import evaluate_plan, load_evaluation_cases, summarize_evaluations


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 DataPilot 固定中文评估集")
    parser.add_argument("--plans", type=Path, help="模型计划输出 JSON 文件")
    args = parser.parse_args()

    cases = load_evaluation_cases()
    print(f"已加载固定评估集：{len(cases)} 条")
    if args.plans is None:
        print("未提供 --plans，只验证评估集结构；需要模型输出文件后才会评分。")
        return 0

    outputs = json.loads(args.plans.read_text(encoding="utf-8"))
    results = []
    for case in cases:
        output: dict[str, Any] = outputs.get(case.case_id, {})
        results.append(
            evaluate_plan(
                case,
                output.get("plan"),
                planning_success=bool(output.get("success")),
            )
        )
    summary = summarize_evaluations(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
