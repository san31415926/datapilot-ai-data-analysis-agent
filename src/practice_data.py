"""DataPilot 的练习数据目录和可复现数据生成器。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.sample_data import generate_orders


PRACTICE_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "practice"


@dataclass(frozen=True)
class PracticeDataset:
    """页面可展示的练习数据元信息。"""

    slug: str
    name: str
    topic: str
    description: str
    file_stem: str
    suggested_questions: tuple[str, ...]


PRACTICE_DATASETS = (
    PracticeDataset("ecommerce-orders", "电商订单分析", "销售经营", "订单、地区、渠道、商品和客户类型，用于练习销售额、订单量和异常订单分析。", "practice_01_ecommerce_orders", ("哪个地区销售额最高？", "哪些商品需要重点关注？")),
    PracticeDataset("inventory-stock", "商品库存管理", "库存管理", "商品库存、月销量和安全库存，用于练习库存预警、周转和补货分析。", "practice_02_inventory_stock", ("哪些商品低于安全库存？", "哪个仓库的库存金额最高？")),
    PracticeDataset("store-sales", "门店销售日报", "门店经营", "不同门店和城市的每日销售数据，用于练习门店对比和趋势分析。", "practice_03_store_sales", ("哪个门店销售额最高？", "销售额最近有什么变化？")),
    PracticeDataset("ad-campaigns", "广告投放效果", "营销投放", "广告曝光、点击、转化和费用，用于练习点击率、转化率和投产比分析。", "practice_04_ad_campaigns", ("哪个渠道投产比最高？", "哪些活动转化率偏低？")),
    PracticeDataset("customer-repurchase", "客户复购分析", "客户运营", "客户订单次数、累计消费和最近购买时间，用于练习客户分层和复购分析。", "practice_05_customer_repurchase", ("哪个会员等级累计消费最高？", "有多少客户需要召回？")),
    PracticeDataset("logistics-timeliness", "物流配送时效", "物流服务", "物流公司、配送方式、承诺天数和实际运输天数，用于练习超时配送分析。", "practice_06_logistics_timeliness", ("哪家物流公司的超时率最高？", "哪个地区平均运输时间最长？")),
    PracticeDataset("employee-attendance", "员工考勤记录", "人力运营", "部门出勤、迟到和加班数据，用于练习部门对比和异常考勤分析。", "practice_07_employee_attendance", ("哪个部门迟到次数最多？", "各部门平均加班时长是多少？")),
    PracticeDataset("training-results", "培训学习成绩", "培训学习", "课程学习时长、作业分数、考试分数和完成率，用于练习通过率和课程效果分析。", "practice_08_training_results", ("哪门课程通过率最高？", "学习时长和考试成绩有关吗？")),
    PracticeDataset("household-budget", "家庭收支记录", "生活财务", "收入、支出、分类和支付方式，用于练习月度收支和消费结构分析。", "practice_09_household_budget", ("哪个分类支出最多？", "每月结余有什么变化？")),
    PracticeDataset("website-analytics", "网站访问分析", "网站运营", "访问来源、设备、页面、访客和转化数据，用于练习流量质量和转化分析。", "practice_10_website_analytics", ("哪个渠道转化率最高？", "哪些页面跳出率偏高？")),
)

PRACTICE_DATASET_BY_SLUG = {item.slug: item for item in PRACTICE_DATASETS}


def generate_practice_frame(slug: str) -> pd.DataFrame:
    """按固定种子生成指定主题的练习数据。"""

    generators = {
        "ecommerce-orders": _generate_ecommerce_orders,
        "inventory-stock": _generate_inventory_stock,
        "store-sales": _generate_store_sales,
        "ad-campaigns": _generate_ad_campaigns,
        "customer-repurchase": _generate_customer_repurchase,
        "logistics-timeliness": _generate_logistics_timeliness,
        "employee-attendance": _generate_employee_attendance,
        "training-results": _generate_training_results,
        "household-budget": _generate_household_budget,
        "website-analytics": _generate_website_analytics,
    }
    try:
        frame = generators[slug]()
    except KeyError as exc:
        raise ValueError(f"未知练习数据：{slug}") from exc
    return frame.reset_index(drop=True)


def write_practice_files(output_dir: str | Path = PRACTICE_DATA_DIR) -> list[tuple[Path, Path]]:
    """生成 10 组练习数据，每组同时写入 CSV 和 XLSX。"""

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[tuple[Path, Path]] = []
    for dataset in PRACTICE_DATASETS:
        frame = generate_practice_frame(dataset.slug)
        csv_path = target_dir / f"{dataset.file_stem}.csv"
        xlsx_path = target_dir / f"{dataset.file_stem}.xlsx"
        frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
        frame.to_excel(xlsx_path, index=False, engine="openpyxl")
        paths.append((csv_path, xlsx_path))
    return paths


def practice_file_path(slug: str, file_format: str, data_dir: str | Path = PRACTICE_DATA_DIR) -> Path:
    """返回指定练习数据的 CSV 或 XLSX 路径。"""

    dataset = PRACTICE_DATASET_BY_SLUG.get(slug)
    normalized_format = file_format.lower().lstrip(".")
    if dataset is None or normalized_format not in {"csv", "xlsx"}:
        raise ValueError("练习数据或文件格式不合法。")
    return Path(data_dir) / f"{dataset.file_stem}.{normalized_format}"


def practice_catalog_frame() -> pd.DataFrame:
    """生成供页面展示的练习数据目录。"""

    rows = []
    for dataset in PRACTICE_DATASETS:
        frame = generate_practice_frame(dataset.slug)
        rows.append(
            {
                "数据集": dataset.name,
                "主题": dataset.topic,
                "数据量": f"{len(frame):,} 行 / {len(frame.columns)} 个字段",
                "可用格式": "CSV、XLSX",
                "适合练习": dataset.description,
            }
        )
    return pd.DataFrame(rows)


def _rng(seed_offset: int) -> random.Random:
    return random.Random(20250730 + seed_offset * 97)


def _generate_ecommerce_orders() -> pd.DataFrame:
    return generate_orders(seed=20250730, base_order_count=240)


def _generate_inventory_stock() -> pd.DataFrame:
    rng = _rng(2)
    products = (("厨房用品", "不粘锅", 259), ("厨房用品", "保温杯", 89), ("数码配件", "无线鼠标", 129), ("数码配件", "蓝牙耳机", 239), ("家居日用", "收纳箱", 69), ("家居日用", "护眼台灯", 149), ("食品饮料", "坚果礼盒", 118), ("食品饮料", "低糖燕麦", 59))
    warehouses = ("华东仓", "华南仓", "华北仓", "西南仓")
    rows = []
    for index in range(1, 73):
        category, product, unit_price = rng.choice(products)
        rows.append({
            "商品编号": f"SKU-{index:04d}", "商品类别": category, "商品名称": product,
            "仓库": rng.choice(warehouses), "库存数量": rng.randint(10, 480),
            "安全库存": rng.randint(30, 120), "月销量": rng.randint(15, 260),
            "采购价": round(unit_price * rng.uniform(0.45, 0.72), 2),
            "供应商": f"供应商{rng.randint(1, 12):02d}",
            "更新时间": (date(2025, 7, 1) + timedelta(days=rng.randint(0, 30))).isoformat(),
        })
    return pd.DataFrame(rows)


def _generate_store_sales() -> pd.DataFrame:
    rng = _rng(3)
    stores = (("上海静安店", "上海"), ("广州天河店", "广州"), ("北京朝阳店", "北京"), ("成都高新店", "成都"), ("武汉江汉店", "武汉"))
    categories = ("食品饮料", "家居用品", "数码配件", "厨房用品")
    rows = []
    for index in range(90):
        order_count = rng.randint(35, 220)
        average_order = round(rng.uniform(45, 260), 2)
        store, city = rng.choice(stores)
        rows.append({
            "日期": (date(2025, 4, 1) + timedelta(days=index)).isoformat(), "门店": store,
            "城市": city, "商品类别": rng.choice(categories), "订单数": order_count,
            "销售额": round(order_count * average_order, 2), "客单价": average_order,
            "退款金额": round(rng.uniform(0, 1800), 2),
        })
    return pd.DataFrame(rows)


def _generate_ad_campaigns() -> pd.DataFrame:
    rng = _rng(4)
    channels = ("搜索广告", "信息流", "短视频", "直播投放", "联盟推广")
    campaigns = ("春季上新", "会员日", "暑期促销", "新品首发", "年中大促")
    rows = []
    for index in range(80):
        impressions = rng.randint(15_000, 280_000)
        clicks = rng.randint(max(100, impressions // 80), max(200, impressions // 12))
        conversions = rng.randint(max(5, clicks // 60), max(8, clicks // 5))
        cost = round(rng.uniform(800, 18_000), 2)
        rows.append({
            "日期": (date(2025, 5, 1) + timedelta(days=index)).isoformat(), "投放渠道": rng.choice(channels),
            "活动名称": rng.choice(campaigns), "曝光量": impressions, "点击量": clicks,
            "转化量": conversions, "广告费用": cost,
            "成交金额": round(conversions * rng.uniform(120, 520), 2),
        })
    return pd.DataFrame(rows)


def _generate_customer_repurchase() -> pd.DataFrame:
    rng = _rng(5)
    levels = ("普通会员", "银卡会员", "金卡会员", "黑金会员")
    channels = ("自然搜索", "朋友推荐", "短视频", "线下门店")
    rows = []
    for index in range(1, 121):
        first_date = date(2024, 1, 1) + timedelta(days=rng.randint(0, 360))
        order_count = rng.randint(1, 18)
        total_spend = round(rng.uniform(80, 1800) * order_count / 3, 2)
        last_date = first_date + timedelta(days=rng.randint(8, 420))
        rows.append({
            "客户编号": f"CUS-{index:04d}", "注册渠道": rng.choice(channels), "会员等级": rng.choice(levels),
            "首次购买日期": first_date.isoformat(), "订单次数": order_count, "累计消费": total_spend,
            "最近购买日期": last_date.isoformat(), "平均客单价": round(total_spend / order_count, 2),
            "客户状态": rng.choice(("活跃", "沉睡", "待召回")),
        })
    return pd.DataFrame(rows)


def _generate_logistics_timeliness() -> pd.DataFrame:
    rng = _rng(6)
    regions = ("华东", "华南", "华北", "西南", "华中")
    carriers = ("顺达物流", "安速快运", "远航物流", "城市配送")
    methods = ("标准配送", "次日达", "经济配送")
    rows = []
    for index in range(1, 121):
        promised = rng.choice((1, 2, 3, 5))
        actual = max(1, promised + rng.choice((-1, 0, 0, 0, 1, 2)))
        rows.append({
            "运单编号": f"WAY-{index:05d}", "发货日期": (date(2025, 6, 1) + timedelta(days=rng.randint(0, 29))).isoformat(),
            "地区": rng.choice(regions), "物流公司": rng.choice(carriers), "配送方式": rng.choice(methods),
            "承诺天数": promised, "实际运输天数": actual, "运费": round(rng.uniform(6, 45), 2),
            "包裹状态": "准时" if actual <= promised else "超时",
        })
    return pd.DataFrame(rows)


def _generate_employee_attendance() -> pd.DataFrame:
    rng = _rng(7)
    departments = ("研发部", "产品部", "销售部", "运营部", "财务部")
    rows = []
    for index in range(1, 121):
        status = rng.choices(("正常", "迟到", "请假", "出差"), weights=(78, 10, 7, 5), k=1)[0]
        rows.append({
            "员工编号": f"EMP-{index:04d}", "部门": rng.choice(departments),
            "日期": (date(2025, 6, 1) + timedelta(days=rng.randint(0, 29))).isoformat(),
            "出勤状态": status, "迟到分钟": rng.randint(5, 55) if status == "迟到" else 0,
            "加班小时": round(rng.uniform(0, 4.5), 1) if status in {"正常", "出差"} else 0,
            "请假类型": rng.choice(("无", "事假", "病假")) if status == "请假" else "无",
        })
    return pd.DataFrame(rows)


def _generate_training_results() -> pd.DataFrame:
    rng = _rng(8)
    courses = ("Python 入门", "SQL 数据分析", "办公效率", "项目管理", "沟通表达")
    rows = []
    for index in range(1, 121):
        study_hours = round(rng.uniform(3, 36), 1)
        homework = round(rng.uniform(55, 100), 1)
        exam = round(min(100, homework * 0.35 + study_hours * 1.4 + rng.uniform(15, 45)), 1)
        completion = round(min(100, study_hours / 36 * 100 + rng.uniform(-8, 8)), 1)
        rows.append({
            "学员编号": f"STU-{index:04d}", "课程": rng.choice(courses), "班级": f"第{rng.randint(1, 6)}期",
            "学习时长": study_hours, "作业得分": homework, "考试得分": exam, "完成率": completion,
            "是否通过": "是" if exam >= 60 and completion >= 60 else "否",
        })
    return pd.DataFrame(rows)


def _generate_household_budget() -> pd.DataFrame:
    rng = _rng(9)
    categories = ("餐饮", "交通", "住房", "购物", "娱乐", "医疗", "工资", "奖金")
    payment_methods = ("微信", "支付宝", "银行卡", "现金")
    rows = []
    for index in range(100):
        category = rng.choice(categories)
        income = category in {"工资", "奖金"}
        amount = rng.uniform(3000, 12000) if income else rng.uniform(20, 2200)
        rows.append({
            "日期": (date(2025, 1, 1) + timedelta(days=index * 3)).isoformat(), "账户": rng.choice(("家庭账户", "个人账户", "储蓄账户")),
            "收支类型": "收入" if income else "支出", "一级分类": category, "支付方式": rng.choice(payment_methods),
            "金额": round(amount, 2), "是否固定": "是" if category in {"住房", "工资"} else "否",
            "备注": "固定项目" if category in {"住房", "工资"} else "日常记录",
        })
    return pd.DataFrame(rows)


def _generate_website_analytics() -> pd.DataFrame:
    rng = _rng(10)
    channels = ("自然搜索", "直接访问", "公众号", "短视频", "广告投放")
    devices = ("手机", "电脑", "平板")
    pages = ("首页", "商品列表", "商品详情", "购物车", "活动页")
    rows = []
    for index in range(180):
        visitors = rng.randint(120, 6800)
        visits = visitors + rng.randint(20, visitors // 2 + 20)
        rows.append({
            "日期": (date(2025, 7, 1) + timedelta(days=index % 30)).isoformat(), "来源渠道": rng.choice(channels),
            "设备": rng.choice(devices), "页面": rng.choice(pages), "访问次数": visits, "访客数": visitors,
            "跳出率": round(rng.uniform(0.18, 0.82), 3), "平均停留秒数": rng.randint(18, 420),
            "转化数": rng.randint(2, max(3, visitors // 10)),
        })
    return pd.DataFrame(rows)
