# 双麦克风系统错误修复记录

## 问题1：PyAudio 初始化失败 (Linux)

### 错误信息
```
python3: ../src/hostapi/alsa/pa_linux_alsa.c:3641: PaAlsaStreamComponent_Initialize: 
Assertion `hostApi->info.defaultInputDevice < hostApi->info.deviceCount' failed.
```

### 原因分析
- Linux 系统上的 ALSA 音频设备配置问题
- PyAudio 在枚举设备时遇到无效的默认输入设备索引

### 解决方案
在导入 `pyaudio` 之前设置环境变量：

```python
# 在 dual_mic_asr.py 顶部
import os
os.environ.setdefault('PA_ALSA_PLUGHW', '1')
os.environ.setdefault('JACK_NO_AUDIO_RESERVATION', '1')

import pyaudio  # 必须在环境变量设置之后导入
```

同时在 `run_dual_mic.sh` 脚本中设置：
```bash
export PA_ALSA_PLUGHW=1
export JACK_NO_AUDIO_RESERVATION=1
export PULSE_LATENCY_MSEC=30
```

---

## 问题2：信号处理器线程错误

### 错误信息
```
ValueError: signal only works in main thread of the main interpreter
```

### 原因分析
- `signal.signal()` 只能在主线程中调用
- `MicASRWorker` 初始化时尝试注册 SIGINT 处理器
- 但 worker 在子线程中创建，导致错误

### 解决方案
在 `dual_mic_asr.py` 的 `MicASRWorker.__init__()` 中添加 try-except：

```python
try:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
except ValueError:
    # 在子线程中调用会失败，忽略即可
    pass
```

---

## 问题3：asyncio 事件循环冲突

### 错误信息
```
Task <Task pending ...> got Future <Future pending> attached to a different loop 
than the current one
```

### 原因分析
**多线程 + 多事件循环架构：**
- **主线程**：运行 `DualMicDialogApp`，负责启动管理
- **麦克风线程1**：运行 `MicASRWorker` for 嘉宾麦克风，有独立事件循环
- **麦克风线程2**：运行 `MicASRWorker` for 辅助记者麦克风，有独立事件循环  
- **会话线程**：运行 `DialogSession`，有独立事件循环

**错误流程：**
```
[麦克风线程1] 识别到文本 
    → [回调] _on_guest_text(text) 
    → [主线程] asyncio.Queue.put(text)  ❌ 队列属于不同的事件循环！
    → [会话线程] asyncio.Queue.get()  ❌ 从不同的事件循环获取！
```

`asyncio.Queue` 是绑定到特定事件循环的，不能跨线程/跨事件循环使用。

### 解决方案

#### 修改1：使用线程安全的 `queue.Queue`

**在 `main_dual_mic.py` 中：**

```python
# 导入标准库的 queue 模块
import queue

class DualMicDialogApp:
    def __init__(self, ...):
        # 改用线程安全的标准库 Queue
        self._text_queue: queue.Queue = queue.Queue()
```

#### 修改2：回调函数直接使用 `put()`

```python
def _on_guest_text(self, text: str) -> None:
    """嘉宾语音识别回调（在麦克风线程中调用）"""
    if not text or not text.strip():
        return
    print(f"\n[嘉宾] 识别结果: {text}")
    # 直接 put，不需要 await（线程安全）
    self._text_queue.put((text, "guest"))

def _on_assistant_text(self, text: str) -> None:
    """辅助记者语音识别回调（在麦克风线程中调用）"""
    if not text or not text.strip():
        return
    print(f"\n[辅助记者] 识别结果: {text}")
    # 直接 put，不需要 await（线程安全）
    self._text_queue.put((text, "assistant"))
```

#### 修改3：在异步循环中使用 `get_nowait()`

```python
async def _text_injection_loop(self) -> None:
    """文本注入循环（在会话线程的事件循环中运行）"""
    while self.running:
        try:
            # 使用 get_nowait() + try-except queue.Empty 模式
            try:
                text, label = self._text_queue.get_nowait()
                # 注入到对话系统
                if self.session and self.session.is_running:
                    await self.session.inject_tagged_text(text, label)
            except queue.Empty:
                # 队列为空，短暂等待后重试
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[TEXT-INJECT] 文本注入失败: {e}")
```

### 核心要点

1. **跨线程通信必须使用线程安全的原语：**
   - ✅ `queue.Queue` (线程安全，不绑定事件循环)
   - ❌ `asyncio.Queue` (绑定到特定事件循环)

2. **事件循环隔离：**
   - 每个线程有自己的 `asyncio.new_event_loop()`
   - `asyncio` 原语（Queue, Event, Future）不能跨循环共享

3. **混合同步/异步模式：**
   - 同步回调 → `queue.Queue.put()` → 异步消费者
   - 在异步循环中使用 `get_nowait()` + `asyncio.sleep()` 实现非阻塞轮询

---

## 验证方法

### 1. 检查音频设备
```bash
python dual_mic_asr.py
```
预期输出：
```
可用音频输入设备列表：
  设备索引 2: KTMICRO-Device-1
  设备索引 3: USB.MIC
  ...
```

### 2. 测试系统启动
```bash
bash run_dual_mic.sh
```
或
```bash
export PA_ALSA_PLUGHW=1
export JACK_NO_AUDIO_RESERVATION=1
python main_dual_mic.py
```

预期输出（无错误）：
```
双麦克风对话系统已启动！
  嘉宾麦克风: 设备索引 3
  辅助记者麦克风: 设备索引 2
请开始对话，按 Ctrl+C 停止...
```

### 3. 测试语音识别
- 对着嘉宾麦克风说话 → 应显示 `[嘉宾] 识别结果: xxx`
- 对着辅助记者麦克风说话 → 应显示 `[辅助记者] 识别结果: xxx`
- 机器人应根据标签做出不同的回复

---

## 技术架构图

```
┌────────────────────┐
│   主线程 (Main)     │
│  DualMicDialogApp  │
│                    │
│  queue.Queue       │◄─────────┐
│  (线程安全队列)      │          │
└────────────────────┘          │
         │                      │
         │ 创建                  │ put(text)
         ↓                      │
┌─────────────────────────────────────────┐
│  麦克风线程1              麦克风线程2       │
│  MicASRWorker            MicASRWorker   │
│  (嘉宾麦克风)              (辅助记者麦克风)  │
│                                         │
│  PyAudio Stream          PyAudio Stream│
│  → VAD                   → VAD         │
│  → WebSocket ASR         → WebSocket ASR│
│  → 回调: _on_guest_text  → 回调: _on_assistant_text│
└─────────────────────────────────────────┘
         │
         │ get_nowait()
         ↓
┌────────────────────┐
│   会话线程          │
│   DialogSession    │
│                    │
│  _text_injection_loop│
│  → inject_tagged_text│
│  → WebSocket LLM   │
│  → TTS播放         │
└────────────────────┘
```

**关键设计：**
- 3个独立的 asyncio 事件循环（麦克风1、麦克风2、会话）
- 使用标准库 `queue.Queue` 实现跨线程通信
- 回调函数（同步）→ 队列 → 异步消费者（会话线程）

---

## 修改文件清单

| 文件 | 修改内容 | 状态 |
|-----|---------|------|
| `dual_mic_asr.py` | 添加环境变量设置 + try-except signal handler | ✅ 完成 |
| `main_dual_mic.py` | asyncio.Queue → queue.Queue | ✅ 完成 |
| `main_dual_mic.py` | 修改回调函数使用 put() | ✅ 完成 |
| `main_dual_mic.py` | 修改 _text_injection_loop 使用 get_nowait() | ✅ 完成 |
| `run_dual_mic.sh` | 添加环境变量导出 | ✅ 完成 |

---

## 已知问题和限制

1. **ALSA 警告信息：** 启动时可能看到 "unknown PCM cards.pcm.hdmi" 等警告，这是 ALSA 配置问题，不影响功能。

2. **麦克风同时说话：** 两个麦克风如果同时检测到语音，会并发发送文本给对话系统。对话系统会按接收顺序处理（串行）。

3. **延迟控制：** 
   - VAD 检测延迟：取决于 `silence_timeout`（默认600ms）
   - ASR 延迟：取决于火山引擎响应速度（通常<500ms）
   - LLM 延迟：取决于模型响应速度

---

## 下一步优化方向

1. **动态 VAD 阈值：** 根据环境噪音自动调整 RMS 阈值
2. **说话人打断处理：** 检测到新的说话人时，中断当前机器人的回复
3. **对话轮次管理：** 限制单方说话时长，避免垄断对话
4. **情绪检测集成：** 结合语音情绪特征优化回复策略

---

生成时间：2024-01-XX
