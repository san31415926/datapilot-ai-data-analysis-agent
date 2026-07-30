"""生成 DataPilot 的中文电商样例 CSV/XLSX 文件。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.sample_data import write_sample_files  # noqa: E402


if __name__ == "__main__":
    csv_path, xlsx_path = write_sample_files(PROJECT_ROOT / "data")
    print(f"已生成：{csv_path}")
    print(f"已生成：{xlsx_path}")
