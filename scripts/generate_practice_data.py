"""生成 DataPilot 页面使用的 10 组练习数据。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.practice_data import write_practice_files  # noqa: E402


if __name__ == "__main__":
    for csv_path, xlsx_path in write_practice_files():
        print(f"已生成：{csv_path.name}、{xlsx_path.name}")
