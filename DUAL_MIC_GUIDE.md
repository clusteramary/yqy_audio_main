# 双麦克风语音交互系统使用指南

## 概述

本系统实现了**双麦克风独立识别说话人**的功能，用于三人深度采访场景：
- **嘉宾（Guest）**：被采访对象
- **辅助记者（Assistant）**：提供补充提问和话题过渡
- **机器人主持人**：控制全局，负责核心提问和深度挖掘

## 系统架构

```
┌─────────────────┐     ┌─────────────────┐
│  麦克风 1       │     │  麦克风 2       │
│  (嘉宾)         │     │  (辅助记者)     │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│  VAD + ASR      │     │  VAD + ASR      │
│  Worker 1       │     │  Worker 2       │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │  "【嘉宾】说：..."     │  "【辅助记者】说：..."
         │                       │
         └───────────┬───────────┘
                     │
                     ▼
           ┌─────────────────┐
           │  DialogSession  │
           │  (对话引擎)      │
           └────────┬────────┘
                    │
                    ▼
           ┌─────────────────┐
           │  云端大模型     │
           │  (火山引擎)     │
           └────────┬────────┘
                    │
                    ▼
           ┌─────────────────┐
           │  TTS 语音输出   │
           └─────────────────┘
```

## 快速开始

### 1. 查看可用麦克风设备

```bash
python test_device.py
```

输出示例：
```
====== 输入/输出音频设备列表 ======
[0] Microsoft Sound Mapper - Input | Host API: MME | 输入通道: 2 | 输出通道: 0
[1] USB Microphone (Guest) | Host API: MME | 输入通道: 1 | 输出通道: 0
[2] USB Microphone (Assistant) | Host API: MME | 输入通道: 1 | 输出通道: 0
...
```

### 2. 配置麦克风索引

编辑 `config.py`，找到以下配置：

```python
# ============ 双麦克风配置（用于三人深度采访场景） ============
GUEST_MIC_INDEX = 1        # 嘉宾麦克风设备索引
ASSISTANT_MIC_INDEX = 2    # 辅助记者麦克风设备索引
```

将索引修改为你的实际麦克风设备索引。

### 3. 启动双麦克风交互

```bash
python main_dual_mic.py
```

### 4. 修改嘉宾信息（可选）

编辑 `main_dual_mic.py` 中的 `GUEST_PROFILE`：

```python
GUEST_PROFILE = {
    "name": "张总",                    # 嘉宾称呼
    "company": "某AI科技公司",          # 公司名称
    "industry": "人工智能应用",         # 所属行业
    "focus_areas": [                   # 关注领域
        "大语言模型商业化",
        "AI在金融行业的应用",
    ],
    "background": "连续创业者，在AI领域深耕10年",
}
```

## 核心文件说明

| 文件 | 功能 |
|------|------|
| `config.py` | 配置文件，包含麦克风索引、ASR参数等 |
| `dual_mic_asr.py` | 双麦克风 ASR 模块，封装 VAD + ASR 逻辑 |
| `dialog_session.py` | 对话会话管理，新增 `inject_tagged_text` 方法 |
| `main_dual_mic.py` | 双麦克风模式主入口 |
| `test_device.py` | 麦克风设备列表查看工具 |

## 配置参数说明

### ASR 配置 (`config.py`)

```python
dual_mic_asr_config = {
    "url": "wss://...",      # ASR 服务地址
    "sample_rate": 16000,    # 采样率（Hz）
    "channels": 1,           # 通道数
    "chunk_ms": 100,         # 每次读取音频的毫秒数
    "vad_threshold": 500,    # VAD 静音检测阈值（RMS）
    "vad_silence_ms": 600,   # 静音多久后认为说话结束
    "max_record_ms": 30000,  # 单次录音最大时长
}
```

### 说话人标签 (`config.py`)

```python
SPEAKER_LABELS = {
    "guest": "【嘉宾】",
    "assistant": "【辅助记者】",
}
```

## 工作原理

### 1. 双路并行采集
- 两个麦克风各自运行独立的 PyAudio 流
- 每个麦克风有独立的线程进行音频采集

### 2. 本地 VAD 过滤
- 使用 RMS（均方根）算法检测语音活动
- 静音时不消耗 ASR 资源
- 检测到语音后立即开始录制
- 静音超时后结束录制

### 3. ASR 识别
- 使用火山引擎 SAUC 大模型 ASR
- WebSocket 流式识别
- 返回识别文本

### 4. 标签注入
- 识别结果自动添加说话人标签
- 格式：`【嘉宾】说：识别的文本`
- 发送给 DialogSession

### 5. 大模型处理
- System Prompt 中明确说明标签含义
- 大模型根据标签区分说话人
- 生成针对性的回复

## 常见问题

### Q1: Linux 系统报错 "Assertion failed" 怎么办？

这是 PyAudio 在 Linux 系统上的已知问题。代码已自动添加了修复措施，但如果仍然失败，请尝试：

**方法 1：使用 PulseAudio（推荐）**
```bash
# 确保 PulseAudio 正在运行
pulseaudio --check
pulseaudio --start

# 重新运行程序
python main_dual_mic.py
```

**方法 2：临时禁用有问题的音频后端**
```bash
# 设置环境变量后运行
export AUDIODEV=null
export SDL_AUDIODRIVER=dummy
python main_dual_mic.py
```

**方法 3：使用特定设备索引**
在 `config.py` 中使用测试时显示的**实际硬件设备索引**（hw:X,Y）而不是虚拟设备（pulse/default）。

示例配置：
```python
GUEST_MIC_INDEX = 2    # KTMICRO-Device-1: USB Audio (hw:1,0)
ASSISTANT_MIC_INDEX = 3  # USB.MIC: Audio (hw:2,0)
```

### Q2: 两个麦克风同时说话会怎样？

两条识别结果会按时间顺序依次发送给大模型。大模型会根据上下文理解对话场景，不需要人为处理冲突。

### Q2: 两个麦克风同时说话会怎样？

两条识别结果会按时间顺序依次发送给大模型。大模型会根据上下文理解对话场景，不需要人为处理冲突。

### Q3: 延迟高怎么办？

- 检查网络连接
- 适当调低 `vad_silence_ms`（但不要太低，否则会过早截断）
- 确保 ASR 服务响应正常

### Q4: 识别不准确怎么办？

- 调整 `vad_threshold`（声音大的环境调高，安静环境调低）
- 确保麦克风正对说话人
- 检查麦克风硬件质量

### Q5: 如何切换回单麦克风模式？

使用原来的 `main.py` 启动即可：
```bash
python main.py
```

## 依赖项

确保已安装以下依赖：
- `pyaudio`
- `aiohttp`
- `websockets`

**Ubuntu/Debian 系统安装：**
```bash
# 先安装系统依赖
sudo apt-get update
sudo apt-get install -y portaudio19-dev python3-pyaudio libasound2-dev

# 再安装 Python 包
pip install pyaudio aiohttp websockets
```

**Windows 系统安装：**
```bash
pip install pyaudio aiohttp websockets
```

Windows 用户如果 PyAudio 安装失败，可以尝试：
```bash
pip install pipwin
pipwin install pyaudio
```

**macOS 系统安装：**
```bash
brew install portaudio
pip install pyaudio aiohttp websockets
```

## 注意事项

1. **麦克风硬件**：建议使用两个不同的 USB 麦克风，避免使用同一声卡的多个输入通道
2. **环境噪音**：在安静的环境下使用效果更好
3. **说话距离**：保持适当的麦克风距离，避免互相串音
4. **网络连接**：需要稳定的网络连接用于 ASR 和对话服务
