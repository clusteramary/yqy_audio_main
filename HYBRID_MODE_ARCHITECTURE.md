# 双麦克风系统架构说明

## 混合模式 (main_hybrid_mic.py) 架构

### 嘉宾麦克风输入路径

**默认使用 ROS 路径（和之前逻辑完全一样）：**

```
嘉宾麦克风硬件
    ↓
audio_capture (ROS 节点)
    ↓
/audio/audio 话题 (audio_common_msgs/AudioData)
    ↓
DialogSession._ros_audio_callback()
    ↓
self.ros_audio_queue
    ↓
process_microphone_input()
    ↓
实时重采样到 16kHz 单声道
    ↓
WebSocket 发送给 LLM（流式，低延迟）
```

**关键代码位置：**
- `dialog_session.py` 第 240-275 行：ROS 订阅初始化
- `dialog_session.py` 第 300-325 行：`_ros_audio_callback()` 接收音频
- `dialog_session.py` 第 920+ 行：`process_microphone_input()` 处理音频流

### 辅助记者麦克风输入路径

**使用独立的 PyAudio + ASR 路径：**

```
辅助记者麦克风硬件
    ↓
PyAudio 直接打开设备（索引 ASSISTANT_MIC_INDEX）
    ↓
MicASRWorker 线程
    ↓
VAD 检测静音
    ↓
火山引擎 ASR WebSocket
    ↓
识别结果回调
    ↓
queue.Queue (线程安全队列)
    ↓
DialogSession.inject_tagged_text("【辅助记者】说：xxx")
    ↓
WebSocket 发送给 LLM（文本打断）
```

## 配置要求

### 1. ROS 配置（嘉宾麦克风）

启动 audio_capture 时指定嘉宾的麦克风：

```bash
# 查看可用的 ALSA 设备
arecord -l

# 启动 audio_capture，指向嘉宾麦克风
# 假设嘉宾麦克风是 card 3, device 0
roslaunch audio_capture capture.launch device:=hw:3,0
```

### 2. PyAudio 配置（辅助记者麦克风）

在 `config.py` 中设置：

```python
ASSISTANT_MIC_INDEX = 2  # 辅助记者的麦克风索引
```

运行 `python check_mic_config.py` 查看设备索引。

## 对比：两种双麦克风模式

| 特性 | 混合模式 (main_hybrid_mic.py) | 全 ASR 模式 (main_dual_mic.py) |
|-----|------------------------------|-------------------------------|
| 嘉宾麦克风 | ROS 流式输入 ✓ | PyAudio + ASR |
| 辅助记者麦克风 | PyAudio + ASR | PyAudio + ASR |
| 嘉宾延迟 | **极低**（约 100-200ms） | **较高**（600-1000ms） |
| 辅助记者延迟 | 较高（ASR） | 较高（ASR） |
| 嘉宾标签 | 无（默认说话人） | 【嘉宾】说： |
| 辅助记者标签 | 【辅助记者】说： | 【辅助记者】说： |
| ROS 依赖 | 需要 audio_capture | 不需要 |
| 推荐场景 | 嘉宾主要对话，辅助记者偶尔插话 | 需要明确区分所有说话人 |

## 为什么混合模式延迟低？

**嘉宾走 ROS 流式路径：**
```
说话 → 音频采集 → ROS 发布 → LLM 接收
        10ms      10ms         100ms
        
总延迟：约 120ms（几乎实时）
```

**如果走 ASR 路径：**
```
说话 → 音频采集 → VAD 检测 → ASR 识别 → 文本发送 → LLM 接收
        10ms       300ms       300ms      10ms       100ms
        
总延迟：约 720ms（明显延迟感）
```

## 运行示例

```bash
# 1. 启动 ROS audio_capture（嘉宾麦克风）
roslaunch audio_capture capture.launch device:=hw:3,0

# 2. 运行混合模式
python main_hybrid_mic.py
```

## 当前配置检查

运行此命令查看和测试你的麦克风配置：

```bash
python check_mic_config.py
```
