"""生成可复现的中文电商经营样例数据。"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SEED = 20250730
BASE_ORDER_COUNT = 240

DATA_COLUMNS = [
    "订单编号",
    "订单日期",
    "地区",
    "渠道",
    "商品类别",
    "商品名称",
    "数量",
    "单价",
    "折扣",
    "销售额",
    "成本",
    "客户类型",
    "订单状态",
]

REGIONS = ("华东", "华南", "华北", "西南", "华中", "西北")
CHANNELS = ("直营网店", "电商平台", "直播间", "企业团购")
CUSTOMER_TYPES = ("新客", "老客", "会员")
ORDER_STATUSES = ("已完成", "退款中", "已取消")
PRODUCTS = (
    ("厨房用品", "不粘锅", 259.0, 0.52),
    ("厨房用品", "保温杯", 89.0, 0.48),
    ("厨房用品", "刀具套装", 169.0, 0.55),
    ("数码配件", "无线鼠标", 129.0, 0.52),
    ("数码配件", "蓝牙耳机", 239.0, 0.58),
    ("数码配件", "手机支架", 49.0, 0.45),
    ("家居日用", "收纳箱", 69.0, 0.50),
    ("家居日用", "护眼台灯", 149.0, 0.53),
    ("家居日用", "床上四件套", 329.0, 0.62),
    ("食品饮料", "手冲咖啡豆", 79.0, 0.57),
    ("食品饮料", "坚果礼盒", 118.0, 0.64),
    ("食品饮料", "低糖燕麦", 59.0, 0.61),
)


def generate_orders(
    seed: int = SEED,
    base_order_count: int = BASE_ORDER_COUNT,
) -> pd.DataFrame:
    """生成含固定质量问题的电商订单表。

    数据全部为合成内容。固定种子保证测试和演示中的聚合结果可以复现。
    """

    if base_order_count <= 0:
        raise ValueError("base_order_count 必须大于 0")

    rng = random.Random(seed)
    start_date = date(2025, 1, 1)
    rows: list[dict[str, object]] = []

    for index in range(1, base_order_count + 1):
        category, product, unit_price, cost_rate = rng.choice(PRODUCTS)
        quantity = rng.randint(1, 5)
        discount = rng.choice((0.85, 0.90, 0.95, 1.0))
        status = rng.choices(ORDER_STATUSES, weights=(86, 8, 6), k=1)[0]
        order_date = start_date + timedelta(days=rng.randrange(181))
        sales_amount = round(quantity * unit_price * discount, 2)
        cost_amount = round(quantity * unit_price * cost_rate, 2)

        if status == "已取消":
            sales_amount = 0.0
            cost_amount = 0.0

        rows.append(
            {
                "订单编号": f"ORD-2025-{index:04d}",
                "订单日期": order_date.isoformat(),
                "地区": rng.choice(REGIONS),
                "渠道": rng.choice(CHANNELS),
                "商品类别": category,
                "商品名称": product,
                "数量": quantity,
                "单价": unit_price,
                "折扣": discount,
                "销售额": sales_amount,
                "成本": cost_amount,
                "客户类型": rng.choice(CUSTOMER_TYPES),
                "订单状态": status,
            }
        )

    rows.extend(
        [
            {
                "订单编号": "ORD-2025-HIGH",
                "订单日期": "2025-03-15",
                "地区": "华东",
                "渠道": "企业团购",
                "商品类别": "食品饮料",
                "商品名称": "企业礼盒组合",
                "数量": 30,
                "单价": 328.0,
                "折扣": 0.90,
                "销售额": 8856.0,
                "成本": 5904.0,
                "客户类型": "会员",
                "订单状态": "已完成",
            },
            {
                "订单编号": "ORD-2025-ANOMALY",
                "订单日期": "2025-05-20",
                "地区": "华南",
                "渠道": "直播间",
                "商品类别": "数码配件",
                "商品名称": "无线鼠标",
                "数量": 1,
                "单价": 129.0,
                "折扣": 0.95,
                "销售额": 99999.99,
                "成本": 67.08,
                "客户类型": "新客",
                "订单状态": "已完成",
            },
        ]
    )

    frame = pd.DataFrame(rows, columns=DATA_COLUMNS)

    # 固定插入两个缺失值，供数据质量检查阶段使用。
    frame.loc[5, "渠道"] = None
    frame.loc[12, "客户类型"] = None

    # 复制一整行，形成可被重复行检查发现的重复订单。
    duplicate_row = frame.iloc[[4]].copy()
    frame = pd.concat([frame, duplicate_row], ignore_index=True)
    return frame[DATA_COLUMNS]


def write_sample_files(output_dir: str | Path) -> tuple[Path, Path]:
    """将相同数据写入 UTF-8 CSV 和 XLSX 文件，并返回文件路径。"""

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    frame = generate_orders()
    csv_path = target_dir / "sample_ecommerce.csv"
    xlsx_path = target_dir / "sample_ecommerce.xlsx"

    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    frame.to_excel(xlsx_path, index=False, engine="openpyxl")
    return csv_path, xlsx_path
