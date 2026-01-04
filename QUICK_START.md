# 快速开始 - 三人访谈Prompt系统

## 🚀 3分钟快速上手

### 步骤1: 选择或创建企业家配置

打开 [main.py](main.py)，找到第38行左右的 `GUEST_PROFILE`：

```python
GUEST_PROFILE = {
    "name": "张总",              # ← 改成实际企业家称呼
    "company": "某AI科技公司",    # ← 改成公司名
    "industry": "人工智能应用",   # ← 改成所属行业
    "focus_areas": [             # ← 改成2-4个核心领域
        "大语言模型商业化",
        "AI在金融行业的应用",
        "企业数字化转型"
    ],
    "background": "连续创业者，在AI领域深耕10年，曾主导多个行业标杆项目",
}
```

### 步骤2: 参考配置示例（可选）

查看 [guest_profiles_examples.py](guest_profiles_examples.py)，有7个行业的现成模板：
- AI领域 → `AI_ENTREPRENEUR`
- 制造业 → `MANUFACTURING_LEADER`
- 医疗健康 → `HEALTHCARE_FOUNDER`
- 教育科技 → `EDTECH_CEO`
- 新能源 → `EV_EXECUTIVE`
- 金融科技 → `FINTECH_INNOVATOR`
- 新零售 → `RETAIL_TECH_LEADER`

### 步骤3: 运行程序

```bash
python main.py
```

控制台会输出：
```
[PROMPT] 使用企业家配置: 张总 (某AI科技公司)
[PROMPT] 开场话题方向 #2: 失败案例复盘
```

---

## ⚡ 快速切换配置（推荐）

使用自动化脚本，一行命令切换配置：

```bash
# 切换到AI企业家配置
python switch_profile.py AI

# 切换到医疗健康配置
python switch_profile.py HEALTHCARE

# 查看所有可用配置
python switch_profile.py --list
```

---

## 📝 配置说明

### 必填字段

| 字段 | 说明 | 示例 |
|------|------|------|
| `name` | 企业家称呼（对话中使用） | "张总"、"李博士" |
| `company` | 公司名称 | "智联AI科技" |
| `industry` | 所属行业（影响问题方向） | "人工智能应用" |
| `focus_areas` | 2-4个核心领域（访谈重点） | ["大模型", "金融AI"] |
| `background` | 背景简介（1句话） | "连续创业者，10年经验" |

### 填写技巧

✅ **好的配置**:
```python
{
    "name": "李总",
    "company": "智联AI科技",
    "industry": "人工智能应用",
    "focus_areas": ["大语言模型商业化", "AI在金融行业的应用"],
    "background": "连续创业者，在AI领域深耕10年"
}
```

❌ **不好的配置**:
```python
{
    "name": "李明",  # ❌ 太正式，对话中不自然
    "company": "公司",  # ❌ 太泛化
    "industry": "科技",  # ❌ 不够具体
    "focus_areas": ["AI", "技术", "创新", "商业", "管理"],  # ❌ 太多太泛
    "background": "李明先生毕业于清华大学，曾在多家公司任职..."  # ❌ 太长
}
```

---

## 🎯 核心特性

### 1. 动态Prompt生成
系统会根据企业家信息自动生成针对性的访谈prompt，包括：
- 称呼使用（"张总"、"李博士"）
- 行业聚焦（"人工智能应用"）
- 提问方向（"大语言模型商业化"）

### 2. 主动引导风格
机器人会：
- 不等回答结束就思考下一步
- 听到关键点立刻追问数据/案例
- 适时提出争议话题激发思考
- 掌控节奏，保持主导权

### 3. 三人对话协作
明确的处理流程：
- 辅助记者提问后：评价→引导→追问
- 被打断时：好问题→肯定；坏时机→推后
- 每10分钟主动总结要点

### 4. 深度提问策略
- 对"成功/失败"追问3个why
- 引导对比时间/空间差异
- 礼貌质疑激发深思
- 抽象概念必须举具体案例

### 5. 开场话题变化
每次访谈从5个方向随机选择：
- 最近行业热点
- 公司新产品幕后
- 失败案例复盘
- 争议性观点
- 职业关键转折

---

## 📚 完整文档

需要深入了解？查看这些文档：

1. **[PROMPT_USAGE_GUIDE.md](PROMPT_USAGE_GUIDE.md)** - 完整使用指南
   - 系统架构说明
   - 配置字段详解
   - 提问策略说明
   - 常见问题解答

2. **[OPTIMIZATION_SUMMARY.md](OPTIMIZATION_SUMMARY.md)** - 优化总结
   - 新旧版本对比
   - 核心改进说明
   - 解决的问题分析
   - 下一步建议

3. **[guest_profiles_examples.py](guest_profiles_examples.py)** - 配置示例
   - 7个行业的配置模板
   - 开箱即用
   - 带注释说明

---

## 💡 常见场景

### 场景1: 访谈AI创业者
```python
GUEST_PROFILE = {
    "name": "李总",
    "company": "智联AI",
    "industry": "人工智能应用",
    "focus_areas": ["大语言模型", "AI商业化"],
    "background": "连续创业者，10年AI经验"
}
```
**效果**: 机器人会围绕大模型和商业化展开深度提问

### 场景2: 访谈医疗科技专家
```python
GUEST_PROFILE = {
    "name": "张医生",
    "company": "健康AI",
    "industry": "医疗健康科技",
    "focus_areas": ["AI辅助诊断", "远程医疗"],
    "background": "三甲医院主任医师，5年创业经历"
}
```
**效果**: 机器人会关注医疗效率、数据安全等话题

### 场景3: 访谈制造业转型专家
```python
GUEST_PROFILE = {
    "name": "王总",
    "company": "智造云",
    "industry": "工业互联网",
    "focus_areas": ["数字化转型", "智能工厂"],
    "background": "传统制造15年，近5年专注工业互联网"
}
```
**效果**: 机器人会探讨传统行业痛点和转型路径

---

## ⚠️ 注意事项

1. **配置修改后需重启程序**才能生效
2. **focus_areas不要超过4个**，否则访谈会分散
3. **background控制在50字以内**，突出核心经历即可
4. **使用switch_profile.py脚本**可避免手动编辑错误

---

## 🆘 遇到问题？

### 问题1: 机器人不够主动
→ 检查 `GUEST_PROFILE` 是否配置准确
→ 查看控制台是否正确打印了配置信息

### 问题2: 访谈深度不够
→ 确保 `focus_areas` 足够具体（如"大模型商业化"而非"AI"）
→ 检查是否启用了追问机制

### 问题3: 三人对话混乱
→ 查看 [PROMPT_USAGE_GUIDE.md](PROMPT_USAGE_GUIDE.md) 中的协作机制说明
→ 可能需要调整辅助记者的提问时机

### 问题4: 配置切换失败
→ 使用 `python switch_profile.py --list` 查看可用配置
→ 确保配置名称大写（如 `AI` 而非 `ai`）

---

## 📞 技术支持

查看详细文档:
- [PROMPT_USAGE_GUIDE.md](PROMPT_USAGE_GUIDE.md) - 使用指南
- [OPTIMIZATION_SUMMARY.md](OPTIMIZATION_SUMMARY.md) - 优化总结
- [guest_profiles_examples.py](guest_profiles_examples.py) - 配置示例

---

## ✨ 版本信息

**当前版本**: v2.0
**更新日期**: 2026-01-04
**主要特性**: 
- 动态企业家配置
- 模块化prompt结构
- 强化主动引导
- 三人对话协作优化
