#!/usr/bin/env python3
"""
快速配置切换脚本
用于快速更换 main.py 中的企业家配置

使用方法：
    python switch_profile.py AI              # 切换到AI企业家配置
    python switch_profile.py MANUFACTURING   # 切换到制造业配置
    python switch_profile.py --list          # 列出所有可用配置
"""

import re
import sys
from pathlib import Path

# 预定义配置（与 guest_profiles_examples.py 保持一致）
PROFILES = {
    "AI": {
        "name": "李总",
        "company": "智联AI科技",
        "industry": "人工智能应用",
        "focus_areas": ["大语言模型商业化", "AI在金融行业的应用", "企业数字化转型"],
        "background": "连续创业者，在AI领域深耕10年，曾主导多个行业标杆项目",
    },
    "MANUFACTURING": {
        "name": "王总",
        "company": "智造云平台",
        "industry": "工业互联网与智能制造",
        "focus_areas": ["工业数字化转型", "智能工厂解决方案", "供应链优化"],
        "background": "传统制造业出身，15年产业经验，近5年专注工业互联网创新",
    },
    "HEALTHCARE": {
        "name": "张医生",
        "company": "健康AI医疗",
        "industry": "医疗健康科技",
        "focus_areas": ["AI辅助诊断", "远程医疗平台", "医疗数据安全"],
        "background": "三甲医院主任医师，5年前跨界创业，致力于用科技提升医疗效率",
    },
    "EDTECH": {
        "name": "陈总",
        "company": "智学教育科技",
        "industry": "教育科技",
        "focus_areas": ["个性化学习方案", "AI教学助手", "教育资源均衡化"],
        "background": "教育行业资深从业者，曾任知名培训机构高管，3年前创立AI教育公司",
    },
    "EV": {
        "name": "刘总",
        "company": "驰远新能源",
        "industry": "新能源汽车与智能出行",
        "focus_areas": ["电动汽车技术创新", "自动驾驶商业化", "充电基础设施"],
        "background": "汽车行业20年经验，早期参与传统车企研发，5年前转型新能源赛道",
    },
    "FINTECH": {
        "name": "赵总",
        "company": "普惠金科",
        "industry": "金融科技",
        "focus_areas": ["数字支付创新", "区块链金融应用", "普惠金融解决方案"],
        "background": "银行业背景出身，深耕支付和风控领域，3年前创办金融科技公司",
    },
    "RETAIL": {
        "name": "周总",
        "company": "云商科技",
        "industry": "新零售与电商技术",
        "focus_areas": ["智能供应链管理", "全渠道零售解决方案", "消费者数据洞察"],
        "background": "电商平台运营出身，擅长数据驱动增长，5年创业经历",
    },
}


def format_profile_dict(profile):
    """将配置字典格式化为Python代码字符串"""
    lines = ["GUEST_PROFILE = {"]
    lines.append(f'    "name": "{profile["name"]}",  # 企业家姓名/称呼')
    lines.append(f'    "company": "{profile["company"]}",  # 公司名称')
    lines.append(f'    "industry": "{profile["industry"]}",  # 所属行业')
    lines.append('    "focus_areas": [  # 核心关注领域（2-4个）')
    for area in profile["focus_areas"]:
        lines.append(f'        "{area}",')
    lines.append("    ],")
    lines.append(f'    "background": "{profile["background"]}",')
    lines.append("}")
    return "\n".join(lines)


def update_main_py(profile_key):
    """更新 main.py 中的 GUEST_PROFILE"""
    if profile_key not in PROFILES:
        print(f"❌ 错误: 配置 '{profile_key}' 不存在")
        print(f"💡 可用配置: {', '.join(PROFILES.keys())}")
        return False

    main_py_path = Path(__file__).parent / "main.py"
    if not main_py_path.exists():
        print(f"❌ 错误: 找不到 main.py 文件")
        return False

    # 读取文件内容
    content = main_py_path.read_text(encoding="utf-8")

    # 匹配 GUEST_PROFILE = { ... } 块
    pattern = r"GUEST_PROFILE = \{[^}]+\}"
    new_profile = format_profile_dict(PROFILES[profile_key])

    # 替换
    new_content = re.sub(pattern, new_profile, content, flags=re.DOTALL)

    if new_content == content:
        print("⚠️  警告: 未找到 GUEST_PROFILE 配置块，可能配置结构已改变")
        return False

    # 写回文件
    main_py_path.write_text(new_content, encoding="utf-8")

    profile = PROFILES[profile_key]
    print(f"✅ 成功切换到配置: {profile_key}")
    print(f"   企业家: {profile['name']}")
    print(f"   公司: {profile['company']}")
    print(f"   行业: {profile['industry']}")
    print(f"   关注领域: {', '.join(profile['focus_areas'][:2])}...")
    return True


def list_profiles():
    """列出所有可用配置"""
    print("\n📋 可用的企业家配置:\n")
    for key, profile in PROFILES.items():
        print(f"  {key:15} - {profile['name']:8} ({profile['company']})")
        print(f"  {'':15}   行业: {profile['industry']}")
        print()


def main():
    if len(sys.argv) < 2:
        print("用法: python switch_profile.py <配置名称>")
        print("      python switch_profile.py --list")
        list_profiles()
        return

    arg = sys.argv[1].upper()

    if arg in ["--LIST", "-L", "LIST"]:
        list_profiles()
    elif arg in PROFILES:
        update_main_py(arg)
    else:
        print(f"❌ 未知配置: {sys.argv[1]}")
        list_profiles()


if __name__ == "__main__":
    main()
