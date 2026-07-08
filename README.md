# yqy_audio_main — 具身智能语音对话系统

基于火山引擎[豆包实时对话 API](https://www.volcengine.com/docs/6561/1328968) 的机器人语音交互主程序，支持 ROS1 分布式音频、人脸检测、情绪识别、知识注入等功能。

## 1. 系统架构

```
┌─────────────────────────────────────────────────────┐
│                     main.py                         │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ Camera   │  │ FacePrompt   │  │ DialogSession │ │
│  │(RealSense)│  │ Detector     │  │               │ │
│  │          │  │ (DeepFace)   │  │ WebSocket ⇄   │ │
│  └──────────┘  └──────┬───────┘  │  火山豆包 API  │ │
│                       │          │               │ │
│                   UDP 5555       │ ┌───────────┐ │ │
│                   (情绪/表情)     │ │ output    │ │ │
│                                  │ │ stream    │ │ │
│  ┌──────────┐                   │ └──┬───┬────┘ │ │
│  │ ROS Mic  │──►/audio/audio──→ │    │   │      │ │
│  └──────────┘                   │    │   │      │ │
│                                  └────┼───┼──────┘ │
│  ┌──────────┐                        │   │        │
│  │ PyAudio  │──►本地麦克风──────────→│   │        │
│  └──────────┘                        │   │        │
│                                      │   │        │
│                         ┌────────────┘   │        │
│                         │                │        │
│                     PyAudio          ROS /audio   │
│                     本地扬声器        话题发布     │
└─────────────────────────────────────────────────────┘
                                      │
                          ┌───────────┴───────────┐
                          │  robot/ros_audio_player│
                          │  (下位机播放节点)       │
                          │  订阅 /audio           │
                          │  订阅 /audio/control   │
                          │  发布 /audio_playing_   │
                          │       status           │
                          └───────────────────────┘
```

## 2. 环境要求

| 项目     | 版本/说明                          |
| -------- | ---------------------------------- |
| Python   | 3.7+（推荐 3.9）                   |
| Conda    | `conda activate yqy1`              |
| ROS1     | rospy + audio_common_msgs（可选）   |
| PyAudio  | 本地麦克风/扬声器                   |
| 摄像头   | RealSense D435 / ROS topic 均可     |

## 3. 安装

```bash
conda activate yqy1
pip install -r requirements.txt
```

核心依赖：`websockets`、`pyaudio`、`numpy`、`opencv-python`、`deepface`、`rospy`（ROS场景）

## 4. 配置

所有配置位于 **`config.py`**，关键项如下：

### 4.1 API 密钥

```python
ws_connect_config = {
    "base_url": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
    "headers": {
        "X-Api-App-ID": "YOUR_APP_ID",
        "X-Api-Access-Key": "YOUR_ACCESS_KEY",
        "X-Api-App-Key": "YOUR_APP_KEY",
    },
}
```

### 4.2 TTS / 对话参数

```python
start_session_req = {
    "tts": {"speaker": "zh_female_vv_jupiter_bigtts", "audio_config": {
        "channel": 1, "format": "pcm", "sample_rate": 24000}},
    "dialog": {
        "bot_name": "小科导医",
        "system_role": "...",
        "speaking_style": "...",
        "location": {"city": "武汉"},
        "extra": {"model": "1.2.1.1"},
    },
}
```

可选发音人：`zh_female_vv_jupiter_bigtts`、`zh_female_xiaohe_jupiter_bigtts`、`zh_male_yunzhou_jupiter_bigtts`、`zh_male_xiaotian_jupiter_bigtts`

### 4.3 Prompt 配置

三层 Prompt 结构（均在 `config.py`）：

| 变量           | 作用                                        |
| -------------- | ------------------------------------------- |
| `BOT_ROLE`     | 角色定位，补充 `system_role`                  |
| `OPENING_LINE` | 开场白（可选，留空不强制）                     |
| `EXTRA_PROMPT` | 场景化指令 / 对白剧本 / 节奏规则等             |

### 4.4 视觉迎宾配置

机器人通过相机持续检测人脸，当有人稳定站在面前约 1 秒后自动播报迎宾话术。

```python
VISUAL_GREETING_INTERVAL_SEC = 0.25       # 检测间隔
VISUAL_GREETING_REQUIRED_CONSECUTIVE = 4   # 连续帧数（4 × 0.25s ≈ 1s）
VISUAL_GREETING_IOU_THRESHOLD = 0.6        # 人脸框 IoU 阈值
VISUAL_GREETING_TEXT = "您好我是导医小助手，需要帮忙吗。"
```

**视觉图片来源**：`CameraAdapter(kind="ros1")` 订阅 ROS1 话题 `/camera/color/image_raw`（`sensor_msgs/Image`），回调中缓存最新帧，供 `FacePromptDetector` 按 `read_latest_frame()` 随时取用。不是从本地图片文件读取。

**检测逻辑**（`FacePromptDetector.wait_for_stable_face`）：
- 每 0.25s 用 `DeepFace.extract_faces` 取一帧做人脸检测
- 维护一个"候选池"，池中每张脸有独立的连续命中计数
- 每帧用 IoU 贪心匹配：匹配到 → 计数 +1；未匹配 → 新脸入池 / 旧候选衰减
- **只要池中任何一张脸**连续命中 4 帧即触发迎宾
- 旁边有人走动不影响，只要有一张脸持续存在就会触发

**触发后行为**：通过 `ChatRAGText(event 502)` 发送迎宾 payload → 等待 TTS 播完 → 放开麦克风进入正常对话。每轮 `run_once` 只触发一次。

**上下文承载**：导医角色、路线、分诊规则通过 `StartSession` 的 `dialog_context` 和 600 秒一次的 `ConversationCreate(event 510)` 静默刷新，不走 502 主动播报。

### 4.5 音频输入/输出模式

| 环境变量              | 默认值    | 说明                            |
| --------------------- | --------- | ------------------------------- |
| `INPUT_AUDIO_MODE`    | `pyaudio` | `pyaudio`=本地麦克风, `ros1`=ROS话题订阅   |
| `OUTPUT_AUDIO_MODE`   | `ros1`    | `pyaudio`=本地扬声器, `ros1`=ROS话题发布  |
| `DUPLEX_MODE`         | `half`    | `half`=半双工, `full`=全双工(可打断)      |

也可通过命令行参数覆盖（见 §5）。

### 4.6 全双工打断参数

| 参数                       | 默认值         | 说明                              |
| -------------------------- | -------------- | --------------------------------- |
| `DUPLEX_MODE`              | `half`         | 双工模式                          |
| `ENABLE_BARGE_IN`          | `True`         | 启用本地能量检测打断               |
| `BARGE_IN_THRESHOLD`       | `1000`         | RMS 能量阈值（16-bit PCM）         |
| `BARGE_IN_MIN_DURATION_MS` | `300`          | 连续超过阈值的最小毫秒数            |
| `HALF_DUPLEX_RESUME_DELAY_MS` | `250`       | 半双工播放结束后延迟恢复麦克风，避开尾音 |
| `FULL_DUPLEX_INTERRUPT_ON_EVENT450` | `True` | 是否允许服务端 `event=450` 辅助打断 |
| `ROS_AUDIO_CONTROL_TOPIC`  | `/audio/control` | 全双工时 stop 控制消息话题          |
| `ROS_AUDIO_FRAME_MS`       | `20`           | 全双工时 ROS 音频小帧毫秒数         |

### 4.7 ROS 扬声器发布参数（`output_audio_config`）

| 参数 | 默认值 | 说明 |
| ---- | ------ | ---- |
| `mode` | `ros1` | 输出模式：`ros1` 发布到 ROS；`pyaudio` 本地扬声器播放 |
| `ros1_topic` | `/audio` | 下行音频发布话题 |
| `ros1_node_name` | `speaker_publisher` | 上位机发布节点名 |
| `ros1_queue_size` | `10` | 发布队列大小，过小可能掉包，过大可能增延迟 |
| `ros1_latch` | `False` | 音频流建议保持 `False`（避免新订阅者收到旧包） |
| `sample_rate` | `24000` | 下行播放采样率（需与下位机一致） |
| `channels` | `1` | 下行声道数（需与下位机一致） |
| `bit_size` | `pyaudio.paInt16` | 本地扬声器模式位宽；ROS 模式下主要用于格式参考 |

### 4.8 下位机 `ros_audio_player.py` 参数（ROS private params）

| 参数 | 默认值 | 说明 |
| ---- | ------ | ---- |
| `~topic` | `/audio` | 接收上位机音频流的话题 |
| `~control_topic` | `/audio/control` | 接收 stop 控制消息话题 |
| `~sample_rate` | `24000` | 播放采样率 |
| `~channels` | `1` | 播放声道 |
| `~sample_format` | `s16le` | 初始播放格式（支持 `s16le`/`f32le`） |
| `~auto_detect_format` | `True` | 自动探测输入格式并切换（建议开启） |
| `~device_index` | `None` | 输出声卡索引，空为系统默认设备 |
| `~sub_type` | `auto` | 订阅类型：`audio`(AudioData) / `bytes`(ByteMultiArray) / `auto` |
| `~status_topic` | `/audio_playing_status` | 播放状态发布话题（`Bool`） |

## 5. 启动方式

### 5.1 命令行（推荐）

```bash
# 直接启动（默认半双工）
python main.py

# 半双工（默认，机器人说话时麦克风静音）
python main.py --duplex-mode half

# 全双工 本地麦克风（可打断机器人）
python main.py --duplex-mode full --input-audio-mode pyaudio

# 全双工 ROS 麦克风
python main.py --duplex-mode full --input-audio-mode ros1

# 本地扬声器输出
python main.py --output-audio-mode pyaudio

# 查看所有参数
python main.py --help
```

### 5.2 环境变量覆盖

```powershell
$env:DUPLEX_MODE='full'; python main.py
$env:INPUT_AUDIO_MODE='ros1'; python main.py
$env:OUTPUT_AUDIO_MODE='pyaudio'; python main.py
```

### 5.3 GUI 启动器

```bash
python gui/robot_launch.py    # 任务管理面板（tkinter）
python gui/gui_photo.py       # 拍照场景启动器
```

### 5.4 下位机播放节点

在下位机机器人上运行：

```bash
rosrun yqy_audio ros_audio_player.py _topic:=/audio _control_topic:=/audio/control _auto_detect_format:=true
```

### 5.5 配套程序

```bash
python emotion.py       # 接收表情索引（UDP 5555）
python mic.py           # 接收麦克风收放指令（UDP 5558）
python integrated_receiver.py  # 综合接收器（表情+麦克风）
python ros_action_index_receiver.py  # ROS 动作 index 接收器
python str_receiver.py  # 文本指令接收（UDP 8889）
python keyListener.py   # 'p' 键监听
```

## 6. ROS 话题一览

| 话题                     | 方向        | 类型                     | 说明                     |
| ------------------------ | ----------- | ------------------------ | ------------------------ |
| `/audio`                 | 发布        | `AudioData` / `ByteMultiArray` | 机器人扬声器音频       |
| `/audio/control`         | 发布        | `std_msgs/String`        | 停止播放控制（JSON格式） |
| `/audio/audio`           | 订阅        | `AudioData`              | 麦克风输入（来自 audio_capture） |
| `/audio_playing_status`  | 订阅        | `std_msgs/Bool`          | 下位机播放状态反馈       |
| `/action_index`          | 发布        | `std_msgs/Int32`         | 语音关键词动作 index     |
| `/camera/color/image_raw`| 订阅        | `sensor_msgs/Image`      | 相机彩色图像             |

## 7. UDP 控制通道

| 端口 | 方向 | 用途                         |
| ---- | ---- | ---------------------------- |
| 5555 | 发布 | 情绪/表情索引（emotion_receiver.py） |
| 5558 | 发布 | 麦克风收放指令（send_microphone/release_microphone） |
| 8889 | 订阅 | 文本指令写入 ctrl.txt       |

语音关键词动作不再通过 UDP 5557 发布，改为 ROS1 `/action_index` 话题发布 `std_msgs/Int32`。当前 index 语义为：`left=5`、`right=4`、`wave=7`、`nod=8`、`shake=10`、`start=11`、`end=12`、`woshou=13`、`good=14`、`photo1=15`、`photo2=16`、`left_front=5`、`right_front=4`、`right_back=19`。

## 8. 主要文件说明

| 文件                       | 说明                                          |
| -------------------------- | --------------------------------------------- |
| `main.py`                  | 主入口：相机启动 → 视觉迎宾 → 对话循环 → 自恢复 |
| `config.py`                | 全部配置（API、音频、Prompt、视觉迎宾、双工）   |
| `dialog_session.py`        | 核心会话管理：WS通信、音频输入输出、打断逻辑     |
| `realtime_dialog_client.py`| 火山引擎 WebSocket 客户端封装                   |
| `protocol.py`              | 二进制协议：header 生成 / response 解析         |
| `audio_constants.py`       | 音频常量、ASR/LLM 关键词、`AudioConfig` dataclass |
| `audio_device_manager.py`  | PyAudio/ROS 音频设备管理                       |
| `ros_audio.py`             | `Ros1SpeakerStream`：ROS音频发布 + 全双工帧封装 |
| `duplex_audio.py`          | 全双工协议：帧打包/解包、stop控制消息            |
| `audio_utils.py`           | PCM/WAV 文件保存工具                           |
| `audio_manager.py`         | `DialogSession` 线程封装，外部调用入口           |
| `CameraAdapter.py`         | 统一相机接口（RealSense/OpenCV/ROS1/ROS2）      |
| `FacePromptDetector.py`    | 人脸检测（稳定脸候选池）+ 情绪推流（基于 DeepFace）|
| `emotion_receiver.py`      | UDP 5555 情绪数据接收                          |
| `integrated_receiver.py`   | 综合 UDP 接收器（情绪 + 语音关键词）             |
| `ros_action_index_receiver.py` | ROS `/action_index` 动作 index 接收器       |
| `mic_receiver.py`          | UDP 5558 麦克风指令接收                        |
| `str_receiver.py`          | UDP 8889 文本指令接收                          |
| `gui/robot_launch.py`      | Tkinter 进程管理面板                           |
| `gui/gui_photo.py`         | 拍照场景启动器                                |
| `robot/ros_audio_player.py`| **下位机** ROS→扬声器播放节点（支持打断）        |
| `ros_audio_sink.py`        | 简易 ROS→PyAudio 接收（无打断）                 |

## 9. 音频格式说明

| 环节          | 采样率 | 声道 | 位深     |
| ------------- | ------ | ---- | -------- |
| PyAudio 麦克风| 48000  | 1    | paInt16  |
| ROS 麦克风输入| 48000  | 2    | paInt16  |
| 发送给豆包    | 16000  | 1    | 16-bit PCM |
| 豆包 TTS 输出 | 24000  | 1    | float32/s16le |
| ROS 播放输出  | 24000  | 1    | f32le/s16le |

> 麦克风 48k→16k 由 `audioop.ratecv` 实时重采样；声道合并由 `audioop.tomono` 处理。

## 10. 对话流程

1. **启动相机**：`CameraAdapter(kind="ros1")` 订阅 `/camera/color/image_raw`，缓存最新帧
2. **建立连接**：WS 连接火山引擎 → StartConnection → StartSession（携带 `dialog_context` 静默上下文）
3. **视觉迎宾**：
   - `FacePromptDetector.wait_for_stable_face()` 持续检测人脸候选池
   - 有人稳定站立约 1 秒后 → 通过 `ChatRAGText(event 502)` 发送迎宾 payload
   - 服务端生成 TTS → 播放迎宾话术 → 等待 TTS 播完
4. **麦克风循环**：放开麦克风 → 持续采集音频 → 20ms 帧 → 发送 `task_request`
5. **服务器响应**：
   - `SERVER_ACK`（bytes）：TTS 音频 → 入队播放
   - `event 553`（LLM开始）：重置关键词缓冲区
   - `event 451`（ASR结果）：关键词检测 + 发布 ROS 动作 index + 用户文本累积
   - `event 450`（用户插话）：触发打断
   - `event 459`（TTS结束）：写入对话日志
6. **打断**（全双工）：
   - 服务端 event 450 或 本地 RMS 超阈值 → `_interrupt_playback`
   - 清空播放队列 → 发布 `/audio/control` stop → 下位机立即静音

## 11. 对话日志

运行时对话文本实时追加到 `dialog.txt`，格式为：

```
用户: 你好，我肚子疼还伴随发烧。
机器人: 肚子痛是间断的疼还是持续的疼？...
```

## 12. 常见问题

| 问题                                  | 解决方法                                    |
| ------------------------------------- | ----------------------------------------- |
| `pyaudio` 找不到设备                  | 运行 `python detect_audio_devices.py` 查看设备列表，在 config.py 中设置 `device_index` 或 `device_name` |
| ROS 话题收不到数据                    | 确认 `INPUT_AUDIO_MODE=ros1` 且 `audio_capture` 节点在运行 |
| 全双工打断不生效                      | 确认 `--duplex-mode full`，检查 `BARGE_IN_THRESHOLD` 是否过高 |
| 程序退出后下位机仍在播放              | 已自动发送 `/audio/control` stop；如下位机未响应，检查 topic 名称一致 |
| `audio_common_msgs` 未安装            | 程序会自动降级为 `ByteMultiArray`，不影响使用   |
| `ImportError: duplex_audio`           | 确保 `duplex_audio.py` 在项目根目录，且 Python path 正确 |
