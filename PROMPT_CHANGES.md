# 三人访谈Prompt系统 v2.0 - 更新说明

> **重要更新**: 2026-01-04 - Prompt系统全面重构

---

## 📢 主要变更

### ✨ 新特性

1. **动态企业家配置系统**
   - 支持针对不同企业家快速切换配置
   - 7个行业的预设配置模板
   - 一行命令快速切换（`switch_profile.py`）

2. **模块化Prompt结构**
   - 从150+行精简至60行
   - 5个清晰模块：角色/风格/提问/协作/语言
   - 易于维护和扩展

3. **强化主动引导**
   - "不等回答结束就思考下一步"
   - "听到关键点立刻追问"
   - "制造张力，激发深层思考"

4. **三人对话优化**
   - 明确的处理流程（评价→引导→追问）
   - 被打断时的应对策略
   - 定期总结机制

5. **开场话题随机化**
   - 5个开场方向避免单调
   - 每次访谈自动变化

---

## 📂 新增文件

| 文件 | 说明 |
|------|------|
| [QUICK_START.md](QUICK_START.md) | 3分钟快速上手指南 |
| [PROMPT_USAGE_GUIDE.md](PROMPT_USAGE_GUIDE.md) | 完整使用手册 |
| [OPTIMIZATION_SUMMARY.md](OPTIMIZATION_SUMMARY.md) | 优化总结与对比 |
| [guest_profiles_examples.py](guest_profiles_examples.py) | 7个行业配置示例 |
| [switch_profile.py](switch_profile.py) | 配置快速切换脚本 |
| [PROMPT_CHANGES.md](PROMPT_CHANGES.md) | 本更新说明 |

---

## 🚀 快速开始

### 方法1: 手动配置

打开 [main.py](main.py)，找到 `GUEST_PROFILE`（第38行左右）：

```python
GUEST_PROFILE = {
    "name": "张总",              # 企业家称呼
    "company": "某AI科技公司",    # 公司名称
    "industry": "人工智能应用",   # 所属行业
    "focus_areas": [             # 2-4个核心领域
        "大语言模型商业化",
        "AI在金融行业的应用"
    ],
    "background": "连续创业者，在AI领域深耕10年"
}
```

### 方法2: 使用切换脚本（推荐）

```bash
# 列出所有可用配置
python switch_profile.py --list

# 切换到AI企业家配置
python switch_profile.py AI

# 切换到医疗健康配置
python switch_profile.py HEALTHCARE

# 运行程序
python main.py
```

---

## 💡 核心改进对比

### 旧版（v1.0）
```python
# 150+行固定prompt
BASE_RULES = r"""
    你是一位资深的AI访谈主持人...
    ## 角色定位
    ## 访谈主题
    ## 访谈风格与原则（5条）
    ## 提问策略（5条）
    ## 互动规则（4条）
    你的说话风格...（8条）
"""
PROMPT_POOL = BASE_RULES
```

**问题**:
- ❌ 冗余严重，关键指令被稀释
- ❌ 无法针对不同嘉宾调整
- ❌ 主动性不足
- ❌ 三人对话规则模糊

### 新版（v2.0）
```python
# 模块化动态生成
GUEST_PROFILE = {
    "name": "李总",
    "company": "智联AI",
    "industry": "人工智能",
    "focus_areas": ["大模型", "金融AI"],
    "background": "连续创业者，10年经验"
}

def build_system_prompt(guest_info):
    core_role = "..."        # 三方角色（3行）
    hosting_style = "..."    # 主持风格（4条）
    questioning = "..."      # 提问技巧（5招）
    collaboration = "..."    # 三人协作（4点）
    language = "..."         # 语言风格（4条）
    opening = "..."          # 开场指令（2步）
    return "\n".join([...])
```

**优势**:
- ✅ 精简60%，突出核心指令
- ✅ 根据企业家信息动态调整
- ✅ 强化主动引导风格
- ✅ 三人协作流程清晰

---

## 📊 效果提升

| 维度 | 旧版 | 新版 | 提升 |
|------|------|------|------|
| **Prompt长度** | 150+行 | 60行 | **-60%** |
| **可配置性** | 固定 | 动态生成 | **✅** |
| **主动性** | 被动响应 | 主动引导 | **⬆️⬆️** |
| **三人协作** | 规则模糊 | 流程清晰 | **⬆️⬆️** |
| **提问深度** | 抽象指导 | 具体战术 | **⬆️** |
| **开场变化** | 无 | 5个方向 | **✅** |
| **维护性** | 难 | 易 | **⬆️⬆️** |

---

## 🎯 解决的问题

### 1. "Prompt没什么作用"
**原因**: 旧prompt太冗长，关键指令被稀释  
**解决**: 精简60%，突出核心指令，根据企业家信息动态调整

### 2. "机器人不够主动"
**原因**: 指令被动，缺乏明确行动指南  
**解决**: 强化主动性 - "不等回答结束"、"立刻追问"、"制造张力"

### 3. "三人对话关系混乱"
**原因**: 互动规则不明确  
**解决**: 明确处理流程（评价→引导→追问）、应对策略、称呼指导

### 4. "访谈深度不够"
**原因**: 提问策略抽象  
**解决**: 具体战术 - 追问3个why、必须举例、礼貌质疑

### 5. "Prompt结构冗余"
**原因**: 单块大段，重复多  
**解决**: 模块化函数设计，职责单一，易于调整

---

## 📚 使用指南

### 基础使用
1. **修改配置**: 编辑 [main.py](main.py) 中的 `GUEST_PROFILE`
2. **运行程序**: `python main.py`
3. **查看输出**: 控制台会显示当前配置信息

### 进阶使用
1. **快速切换**: 使用 `python switch_profile.py AI`
2. **自定义配置**: 参考 [guest_profiles_examples.py](guest_profiles_examples.py)
3. **调整Prompt**: 修改 `build_system_prompt()` 函数

### 完整文档
- [QUICK_START.md](QUICK_START.md) - 3分钟上手
- [PROMPT_USAGE_GUIDE.md](PROMPT_USAGE_GUIDE.md) - 详细手册
- [OPTIMIZATION_SUMMARY.md](OPTIMIZATION_SUMMARY.md) - 优化总结

---

## 🔧 配置示例

### AI企业家
```python
GUEST_PROFILE = {
    "name": "李总",
    "company": "智联AI科技",
    "industry": "人工智能应用",
    "focus_areas": ["大语言模型商业化", "AI在金融行业的应用"],
    "background": "连续创业者，在AI领域深耕10年"
}
```

### 医疗健康
```python
GUEST_PROFILE = {
    "name": "张医生",
    "company": "健康AI医疗",
    "industry": "医疗健康科技",
    "focus_areas": ["AI辅助诊断", "远程医疗平台"],
    "background": "三甲医院主任医师，5年前跨界创业"
}
```

更多示例见 [guest_profiles_examples.py](guest_profiles_examples.py)

---

## ⚙️ 技术细节

### Prompt生成流程
```
企业家配置 → build_system_prompt() → 动态Prompt
     ↓
GUEST_PROFILE → 5个模块拼接 → 最终Prompt
                  ↓
          发送给DialogSession
```

### 开场话题随机化
```python
OPENING_HOOKS = [
    "最近行业热点事件",
    "公司最新产品/战略幕后",
    "失败案例复盘",
    "争议性行业观点",
    "职业生涯关键转折"
]
# 每次访谈随机选择一个
```

---

## ⚠️ 注意事项

1. **配置修改后需重启程序**
2. **focus_areas不超过4个**（避免访谈分散）
3. **background控制在50字以内**（突出核心）
4. **使用switch_profile.py脚本**（避免格式错误）

---

## 🐛 已知问题

无

---

## 📅 版本历史

### v2.0 (2026-01-04)
- ✅ 引入动态企业家配置
- ✅ Prompt精简60%
- ✅ 强化三人对话协作
- ✅ 增加开场话题随机化
- ✅ 提供配置示例和切换脚本
- ✅ 完整文档体系

### v1.0
- ❌ 固定150+行prompt
- ❌ 无法针对不同嘉宾调整
- ❌ 三人对话规则不清晰

---

## 🤝 贡献

如有问题或建议，请查看:
- [PROMPT_USAGE_GUIDE.md](PROMPT_USAGE_GUIDE.md) - 常见问题
- [OPTIMIZATION_SUMMARY.md](OPTIMIZATION_SUMMARY.md) - 设计原理

---

## 📞 技术支持

- 快速上手: [QUICK_START.md](QUICK_START.md)
- 使用指南: [PROMPT_USAGE_GUIDE.md](PROMPT_USAGE_GUIDE.md)
- 配置示例: [guest_profiles_examples.py](guest_profiles_examples.py)

---

## 📜 许可证

与原项目保持一致
