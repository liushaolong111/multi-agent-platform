"""演示数据生成脚本：扩充业务库数据到中等规模。

生成内容：
- 20 个工程项目（覆盖 6 大区域）
- ~500 条成本流水（20 项目 × 6 月 × 4 类别 + 部分 7 月）
- ~80 条质量安全问题

用法：
    .venv/Scripts/python.exe app/script/generate_demo_data.py

生成的 CSV 会覆盖 data/ 下对应的文件，之后再跑 seed_business_db.py 灌库。
"""
import csv
import os
import random
from datetime import date, timedelta

# 固定随机种子，保证可复现
random.seed(42)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")

# ---- 20 个项目：覆盖 6 大区域 ----
PROJECTS = [
    # 华东（5）
    ("BY-HD-001", "上海临港新城商务中心项目", "华东", "张伟", "在建", 268000000, "2025-03-01", "2027-06-30"),
    ("BY-HD-002", "杭州萧山科技城综合体项目", "华东", "李强", "在建", 186500000, "2025-08-15", "2027-12-31"),
    ("BY-HD-003", "南京江北新区产业园项目", "华东", "王磊", "竣工", 132000000, "2024-05-10", "2026-03-20"),
    ("BY-HD-004", "苏州工业园区生物医药基地项目", "华东", "陈刚", "在建", 215000000, "2025-05-01", "2027-10-31"),
    ("BY-HD-005", "宁波东部新城轨道交通枢纽项目", "华东", "赵峰", "在建", 358000000, "2024-11-01", "2027-08-31"),
    # 华北（4）
    ("BY-HB-001", "雄安新区安置房项目", "华北", "赵鹏", "在建", 315000000, "2025-01-20", "2027-09-30"),
    ("BY-HB-002", "天津滨海新区地下管廊项目", "华北", "孙明", "停工", 78000000, "2024-11-01", "2026-06-30"),
    ("BY-HB-003", "北京城市副中心行政办公区项目", "华北", "周建国", "在建", 420000000, "2024-06-01", "2027-05-31"),
    ("BY-HB-004", "济南新旧动能转换先行区项目", "华北", "吴海涛", "竣工", 156000000, "2024-03-15", "2026-04-30"),
    # 华南（4）
    ("BY-HN-001", "深圳前海金融大厦项目", "华南", "陈涛", "在建", 420000000, "2024-09-01", "2027-03-31"),
    ("BY-HN-002", "广州白云机场配套项目", "华南", "周杰", "在建", 156000000, "2025-06-10", "2027-02-28"),
    ("BY-HN-003", "厦门翔安新机场航站楼项目", "华南", "黄志强", "在建", 560000000, "2024-04-01", "2027-12-31"),
    ("BY-HN-004", "珠海横琴粤港澳深度合作区项目", "华南", "林伟", "在建", 198000000, "2025-02-01", "2027-11-30"),
    # 西南（3）
    ("BY-XN-001", "成都天府新区会展中心项目", "西南", "刘洋", "竣工", 205000000, "2024-03-15", "2026-01-15"),
    ("BY-XN-002", "重庆江北嘴国际金融中心项目", "西南", "郑凯", "在建", 385000000, "2024-08-01", "2027-09-30"),
    ("BY-XN-003", "昆明滇池国际会展中心二期项目", "西南", "马文斌", "在建", 172000000, "2025-04-01", "2027-07-31"),
    # 华中（2）
    ("BY-HZ-001", "武汉光谷科技创新大走廊项目", "华中", "徐建华", "在建", 295000000, "2025-01-10", "2027-12-31"),
    ("BY-HZ-002", "长沙岳麓山大学科技城项目", "华中", "胡敏", "竣工", 118000000, "2024-07-01", "2026-05-31"),
    # 西北（2）
    ("BY-XB-001", "西安国际港务区中欧班列集结中心项目", "西北", "郭永强", "在建", 236000000, "2024-10-01", "2027-06-30"),
    ("BY-XB-002", "兰州新区综合保税区项目", "西北", "高志远", "在建", 142000000, "2025-03-01", "2027-09-30"),
]

COST_CATEGORIES = ["材料", "人工", "机械", "分包"]
# 各成本类别占合同金额的大致比例
CATEGORY_RATIO = {"材料": 0.42, "人工": 0.25, "机械": 0.12, "分包": 0.21}
# 项目级目标预算执行率（实际成本 / 预算）：制造真实的黄/橙/红超支样本，覆盖多个区域。
# 红≥115% / 橙110-115% / 黄105-110%；未列出的项目执行率 <100%（预算有结余）。
PROJECT_COST_RATE = {
    "BY-HN-001": 1.18,  # 深圳前海金融大厦（华南）🔴
    "BY-HB-003": 1.17,  # 北京城市副中心（华北）🔴
    "BY-XN-002": 1.13,  # 重庆江北嘴（西南）🟠
    "BY-HD-002": 1.12,  # 杭州萧山科技城（华东）🟠
    "BY-HZ-001": 1.08,  # 武汉光谷（华中）🟡
    "BY-HD-004": 1.07,  # 苏州工业园生物医药（华东）🟡
    "BY-XB-001": 1.06,  # 西安国际港务区（西北）🟡
}
MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
          "2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12"]
# 月度进度系数（前低后高再收尾，模拟施工进度曲线，12 个月之和≈1）
MONTH_PROGRESS = [0.06, 0.07, 0.08, 0.08, 0.09, 0.10, 0.10, 0.11, 0.11, 0.10, 0.06, 0.04]

SAFETY_TYPES = ["安全", "质量"]
SEVERITIES = ["一般", "较大", "重大"]
STATUSES = ["待整改", "整改中", "已闭环"]

# 安全/质量问题描述模板
ISSUE_TEMPLATES = [
    ("安全", "一般", "施工现场消防通道被材料堆放占用，影响应急疏散"),
    ("安全", "一般", "高处作业人员未系挂安全带，安全帽佩戴不规范"),
    ("安全", "一般", "临时用电线路敷设不规范，存在私拉乱接现象"),
    ("安全", "一般", "安全网破损未及时更换，存在坠物风险"),
    ("安全", "较大", "楼层临边防护栏杆缺失，未设置警示标识"),
    ("安全", "较大", "配电箱未设置漏电保护装置，电缆拖地敷设"),
    ("安全", "较大", "模板支撑体系未按专项方案设置扫地杆和剪刀撑"),
    ("安全", "重大", "脚手架连墙件设置不足，架体稳定性不满足专项方案要求"),
    ("安全", "重大", "塔吊附着装置螺栓松动，未按周期复紧"),
    ("安全", "重大", "有限空间作业未进行气体检测即安排人员进入"),
    ("质量", "一般", "混凝土结构表面出现蜂窝麻面，局部漏振"),
    ("质量", "一般", "墙体砌筑灰缝饱满度不足，砂浆强度不稳定"),
    ("质量", "一般", "幕墙预埋件位置偏差超出允许范围"),
    ("质量", "较大", "防水卷材搭接宽度不足，存在渗漏隐患"),
    ("质量", "较大", "楼板混凝土养护时间不足即上荷载"),
    ("质量", "较大", "钢筋保护层厚度实测偏差超出规范允许范围"),
    ("质量", "重大", "主体结构混凝土强度检测未达到设计要求"),
    ("质量", "重大", "钢结构焊缝探伤检测存在不合格项，未按要求返修"),
]


def gen_projects():
    path = os.path.join(DATA_DIR, "projects.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["项目编号", "项目名称", "区域", "项目经理", "项目状态", "合同金额", "开工日期", "计划竣工日期"])
        for p in PROJECTS:
            writer.writerow(p)
    print(f"✅ projects.csv：{len(PROJECTS)} 个项目")


def gen_costs():
    path = os.path.join(DATA_DIR, "cost_records.csv")
    rows = []
    for p in PROJECTS:
        code, name, region, manager, status, contract, start, end = p
        # 在建项目生成全年 12 个月；竣工项目生成到计划竣工月份；停工项目生成到停工月份
        if status == "在建":
            months = MONTHS[:]
        elif status == "竣工":
            end_month = end[:7]  # "2026-03-20" -> "2026-03"
            months = [m for m in MONTHS if m <= end_month]
        else:  # 停工
            months = [m for m in MONTHS if m <= "2026-03"]
        # 项目级目标执行率：命中则按 budget = amount / rate 反推预算，制造真实超支样本
        target_rate = PROJECT_COST_RATE.get(code)
        for mi, month in enumerate(months):
            progress = MONTH_PROGRESS[mi] if mi < len(MONTH_PROGRESS) else 0.08
            monthly_total = contract * progress * random.uniform(0.85, 1.15)
            for cat in COST_CATEGORIES:
                amount = monthly_total * CATEGORY_RATIO[cat] * random.uniform(0.90, 1.10)
                if target_rate is not None:
                    # ±0.4pp 小扰动：单行不跨预警级，项目内 48 行聚合后执行率收敛到目标值
                    rate = target_rate * random.uniform(0.996, 1.004)
                    budget = amount / rate
                else:
                    budget = amount * random.uniform(1.02, 1.12)  # 预算略高于实际
                rows.append([code, name, region, month, cat, round(amount, 2), round(budget, 2)])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["项目编号", "项目名称", "区域", "成本月份", "成本类别", "金额", "预算金额"])
        writer.writerows(rows)
    print(f"✅ cost_records.csv：{len(rows)} 条成本流水")


def gen_safety():
    path = os.path.join(DATA_DIR, "safety_issues.csv")
    rows = []
    base_date = date(2026, 1, 1)
    for p in PROJECTS:
        code, name, region, manager, status, contract, start, end = p
        # 每个项目 3-6 条问题，大项目更多
        n_issues = random.randint(3, 6)
        for _ in range(n_issues):
            issue_type, severity, desc = random.choice(ISSUE_TEMPLATES)
            # 发现日期在 2026-01 到 2026-07 之间
            found = base_date + timedelta(days=random.randint(0, 180))
            # 已闭环的概率约 70%
            if random.random() < 0.7:
                issue_status = "已闭环"
                closed = found + timedelta(days=random.randint(3, 15))
            elif random.random() < 0.6:
                issue_status = "整改中"
                closed = ""
            else:
                issue_status = "待整改"
                closed = ""
            rows.append([code, name, region, issue_type, severity, issue_status,
                         found.isoformat(), closed, desc])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["项目编号", "项目名称", "区域", "问题类型", "严重程度", "状态", "发现日期", "整改完成日期", "问题描述"])
        writer.writerows(rows)
    print(f"✅ safety_issues.csv：{len(rows)} 条质量安全问题")


# 材料类别与模板
MATERIAL_CATEGORIES = ["钢结构", "预制构件", "水泥", "钢筋", "防水材料", "幕墙",
                       "木材", "砌块", "机电设备", "保温材料"]
MATERIAL_TEMPLATES = [
    ("钢结构", "H型钢柱", "HW400×400", "吨"),
    ("钢结构", "钢梁", "HN600×200", "吨"),
    ("预制构件", "预制叠合板", "60mm厚", "块"),
    ("预制构件", "预制楼梯", "标准型", "块"),
    ("水泥", "P.O 42.5水泥", "袋装", "吨"),
    ("钢筋", "HRB400钢筋", "Φ12-Φ25", "吨"),
    ("防水材料", "SBS改性沥青卷材", "4mm厚", "卷"),
    ("幕墙", "中空玻璃幕墙", "6+12A+6", "㎡"),
    ("木材", "胶合板", "18mm", "张"),
    ("木材", "方木", "40×80", "根"),
    ("砌块", "加气混凝土砌块", "600×200×200", "块"),
    ("机电设备", "电梯", "客梯 1000kg", "台"),
    ("机电设备", "消防水泵", "XBD-15kW", "台"),
    ("保温材料", "挤塑聚苯板", "50mm", "㎡"),
    ("保温材料", "岩棉板", "80mm", "㎡"),
]
SUPPLIERS = ["精工钢构", "宝钢集团", "海螺水泥", "沙钢集团", "东方雨虹", "远大住工",
             "江河幕墙", "中国建材", "北新建材", "上海三菱电梯", "中联重科", "江苏中南建设"]


def gen_materials():
    """生成材料库存数据，包含部分滞后预警场景。"""
    path = os.path.join(DATA_DIR, "material_records.csv")
    rows = []
    base_date = date(2026, 6, 1)
    for p in PROJECTS:
        code, name, region, manager, status, contract, start, end = p
        # 每个项目 3-5 种材料
        n_materials = random.randint(3, 5)
        chosen = random.sample(MATERIAL_TEMPLATES, n_materials)
        for cat, mat_name, spec, unit in chosen:
            stock = random.randint(10, 500)
            # 计划使用日期在 2026-06 到 2026-09 之间
            plan_date = base_date + timedelta(days=random.randint(0, 90))
            # 70% 正常到货（ETA ≤ 计划使用日期），30% 滞后（ETA > 计划使用日期）
            if random.random() < 0.7:
                eta = plan_date - timedelta(days=random.randint(0, 10))
            else:
                eta = plan_date + timedelta(days=random.randint(1, 15))
            supplier = random.choice(SUPPLIERS)
            rows.append([code, name, region, cat, mat_name, spec, unit, stock,
                         eta.isoformat(), plan_date.isoformat(), supplier])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["项目编号", "项目名称", "区域", "材料类别", "材料名称", "规格型号",
                         "单位", "库存数量", "预计到货日期", "计划使用日期", "供应商"])
        writer.writerows(rows)
    print(f"✅ material_records.csv：{len(rows)} 条材料库存记录")


if __name__ == "__main__":
    gen_projects()
    gen_costs()
    gen_safety()
    gen_materials()
    print(f"\n数据已生成到：{DATA_DIR}")
    print("下一步：运行 python app/script/seed_business_db.py 灌入数据库")
