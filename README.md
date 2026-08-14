# 专家机器人语音采访系统（RealtimeDialog）

实时语音对话程序：专家机器人"小科"采访调研系统，支持语音输入、语音输出、
人脸识别引导、情绪推送与半双工/全双工对话。

## 一、前置条件

1. **API 密钥**：打开 `config.py`，修改以下两个字段（火山引擎端到端大模型）：
   ```python
   "X-Api-App-ID": "火山控制台上端到端大模型对应的App ID",
   "X-Api-Access-Key": "火山控制台上端到端大模型对应的Access Key",
   ```
   speaker 字段指定发音人（`zh_female_vv_jupiter_bigtts` 等）。

2. **ROS 环境**（机器人平台必备）：
   ```bash
   source /opt/ros/noetic/setup.bash
   roscore                    # 必须保持运行
   # 相机节点（发布 /camera/color/image_raw，人脸识别依赖）
   ```

3. **音频设备**：机器人下位机运行 `ros_audio_player.py`（ROS 扬声器模式），
   或本地声卡支持 24kHz 播放（本地播放模式）。

4. **Python 虚拟环境**（已配置好，无需重装）：
   ```bash
   cd /home/nvidia/Documents/Robot-Voice/yqy_audio_main
   source .venv/bin/activate
   ```

## 二、启动方式 A：桌面双击（推荐）

桌面上有**两对**可双击启动的程序（推荐用下面新的两个，旧的两个可自行删除）：

| 图标 | 作用 | 依赖 |
|---|---|---|
| **采访机器人_ROS扬声器**（新） | 声音从机器人下位机扬声器播出（话题 /audio） | 需要 roscore + ros_audio_player.py |
| **采访机器人_本地播放**（新） | 声音从本地声卡播出（PyAudio） | 需要本地音频设备 |
| main.py ROS扬声器（旧） | 同上（旧版，可删除） | — |
| main.py 本地播放（旧） | 同上（旧版，可删除） | — |

> 新版启动器在 `.desktop` 里**直接调用 `gnome-terminal`** 打开终端窗口，
> 程序的运行日志和报错会**直接显示在终端里**（同时写入 logs/ 文件），
> 不依赖桌面环境的 Terminal 处理，避免双击后窗口闪退。

> 首次双击若提示"未信任"，右键图标 → 属性 → 允许启动（Allow Launching）。

启动逻辑在项目内脚本中：
- `run_ros_speaker.sh`（带前置检查：虚拟环境 / ROS / roscore）
- `run_local_playback.sh`

每次运行自动写日志到 `logs/run_ros_speaker_时间戳.log` 或 `logs/run_local_playback_时间戳.log`。

## 三、启动方式 B：项目内命令行

```bash
cd /home/nvidia/Documents/Robot-Voice/yqy_audio_main

# 1) 终端 1：ROS 主节点（若未启动）
source /opt/ros/noetic/setup.bash
roscore

# 2) 终端 2：启动程序（以下两种任选其一）
#    ROS 下位机扬声器（默认）：
OUTPUT_AUDIO_MODE=ros1 .venv/bin/python main.py
#    本地扬声器：
OUTPUT_AUDIO_MODE=pyaudio .venv/bin/python main.py

# 等价写法（命令行参数）：
.venv/bin/python main.py --output-audio-mode ros1
.venv/bin/python main.py --output-audio-mode pyaudio
```

停止：终端按 `Ctrl + C`。

### 常用参数

| 参数 | 可选值 | 默认 | 说明 |
|---|---|---|---|
| `--duplex-mode` | half / full | half | 半双工 / 全双工（可打断） |
| `--input-audio-mode` | pyaudio / ros1 | pyaudio | 麦克风输入方式 |
| `--output-audio-mode` | pyaudio / ros1 | ros1 | 扬声器输出方式 |

环境变量（可选，均有默认值）：
- `FIRST_VOICE_RMS_THRESHOLD`（默认 800）：开场白后判定"用户开口"的能量阈值
- `FIRST_VOICE_END_SILENCE_MS`（默认 800）：用户说完一句话所需的连续静音时长
- `FIRST_VOICE_TIMEOUT_SEC`（默认 30）：用户一直不开口时强制注入 prompt 的兜底时间
- `HALF_DUPLEX_RESUME_DELAY_MS`（默认 50）：半双工下位机播完到恢复麦克风的延迟

### 采访问题选择（本地随机，先选好再组装 prompt）

每场采访的随机项都在**本地**（`config.build_interview_plan()`）一次性选定，
再组装成 start prompt 发给模型；模型只负责按清单提问，不再自己随机挑题：

| 随机项 | 方式 |
|---|---|
| 开场白 | 四选一（均为挥手打招呼动作 + 语音），say_hello 使用本地选定的那一条 |
| 身份问题 | 二选一；模型根据回答判断"用户侧/专家侧"，自主选对应侧关键问题 |
| 次要问题 | 每次固定选 2 个，持久化轮转（logs/interview_rotation.json）相邻场次不重复 |
| 结束语 | 二选一 |

调试时可手动指定随机项（环境变量）：
- `EXPERT_OPENING_INDEX`：开场白索引（0~3）
- `EXPERT_IDENTITY_INDEX`：身份问题索引（0~1）
- `EXPERT_SECONDARY_INDICES`：次要问题索引，逗号分隔（如 `0,3`）
- `EXPERT_CLOSING_INDEX`：结束语索引（0~1）

## 四、常见问题排查（闪退 / 启动失败）

程序本身有自动重启逻辑，正常不会退出；若窗口一闪而过或反复报错，请按顺序排查：

1. **看日志**（最关键）：双击启动后日志写入 `logs/` 目录，`tail -f logs/run_*.log` 查看报错；
   脚本自身每一步检查记录在 `logs/launcher_debug.log`（含 ROS 环境变量值）。

2. **窗口瞬间关闭**：脚本内已处理两类双击环境坑，若仍闪退：
   - 检查 `logs/launcher_debug.log` 最后一行卡在哪一步
   - 右键图标 → 属性 → 允许启动（Allow Launching）

3. **双击环境缺 ROS 变量**（历史坑，已修）：桌面双击由 gnome-shell 启动，不读 `.bashrc`，
   缺少 `ROS_DISTRO` / `ROS_IP` / `ROS_MASTER_URI`。脚本已在 source 前补全：
   `ROS_DISTRO=noetic`、`ROS_IP=192.168.10.100`、`ROS_MASTER_URI=http://192.168.10.66:11311`
   （如需更改，直接编辑两个 run_*.sh 顶部；终端手动运行不受影响，会保留已有值）。

4. **找不到 rospy**：忘记 `source /opt/ros/noetic/setup.bash`（桌面版脚本已内置，命令行需手动执行）。

5. **`rostopic list` 失败 / ROS 相关报错**：确认机器人主机 192.168.10.66 的 roscore 在运行、
   网络可达（`ping 192.168.10.66`）。

6. **`[Errno -9997] Invalid sample rate`**：当前本地音频设备不支持 24kHz 播放，
   检查本地声卡（本地播放模式）或 ros_audio_player.py（ROS 模式，机器人端通常无此问题）。

7. **人脸识别失败**：确认相机节点在发布 `/camera/color/image_raw`；
   首次运行 deepface 会联网下载模型到 `~/.deepface/weights`。

8. **一直报 `[main] 捕获异常...3s 后重启`**：说明在自动重试，日志会显示具体异常，
   通常是上述 3~7 之一。

## 五、目录说明

```
main.py                  主程序（采访机器人入口）
config.py                配置：API 密钥、发音人、采访问题池、prompt、参数
dialog_session.py        对话会话（语音收发、prompt 注入时机、半双工逻辑）
idle_attract_announcer.py 闲时招揽客户语音脚本（独立运行，见"六"）
run_ros_speaker.sh       桌面启动脚本（ROS 扬声器）
run_local_playback.sh    桌面启动脚本（本地播放）
logs/                    运行日志
```

启动：
source /opt/ros/noetic/setup.bash                              
cd /home/nvidia/Documents/Robot-Voice/yqy_audio_main           
.venv/bin/python main.py 

## 六、闲时招揽客户语音脚本（idle_attract_announcer.py）

机器人空闲时，每固定间隔（默认 15 秒）通过 ROS 话题 /audio 说一句固定招揽语
（默认："你好，有空来做个小小的机器人访谈嘛？"）。TTS 音色与 main.py 开场白一致，
发布走 main.py 相同的 ROS1 链路。

> 注意：本脚本独立运行，**不要与 main.py 同时运行**（两者都会往 /audio 发布语音）。

### 启动（ROS 扬声器模式）

```bash
source /opt/ros/noetic/setup.bash
cd /home/nvidia/Documents/Robot-Voice/yqy_audio_main
OUTPUT_AUDIO_MODE=ros1 .venv/bin/python idle_attract_announcer.py
```

停止：终端按 `Ctrl + C`。

### 可选参数

| 参数 | 可选值 | 默认 | 说明 |
|---|---|---|---|
| `--phrase` | 任意文本 | 你好，有空来做个小小的机器人访谈嘛？ | 要说的固定语句 |
| `--interval` | 秒（浮点） | 15 | 每隔多少秒说一次 |
| `--duplex-mode` | half / full | half | 半双工 / 全双工（可打断） |
| `--output-audio-mode` | pyaudio / ros1 | ros1 | 扬声器输出方式 |

### 修改固定语句 / 间隔

直接编辑 `idle_attract_announcer.py` 顶部的可配置项：

```python
ATTRACT_PHRASE = "你好，有空来做个小小的机器人访谈嘛？"  # 固定输出的语句内容
ANNOUNCE_INTERVAL_SEC = 15.0                            # 每隔多少秒说一次
```

合成的语音会缓存到 `logs/idle_attract_*.pcm`，同一句话只合成一次，网络异常时可直接复用缓存。