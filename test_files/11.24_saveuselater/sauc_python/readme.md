**项目概述**

- **说明**: 本仓库包含一个示例 WebSocket ASR 客户端 `sauc_websocket_demo.py`，支持两种模式：
  - **文件模式**: 将本地音频文件按段发送到服务（`--file`）。
  - **麦克风实时模式**: 实时从麦克风采集音频并流式发送（`--mic`）。

**先决条件**

- **Python**: 建议使用 `Python 3.8+`。
- **系统依赖**: `ffmpeg`（用于将非 WAV 音频转为 PCM WAV，若只用麦克风模式可不需要）。
- **PortAudio**: `sounddevice` 依赖 PortAudio；在 Windows 上通常随 wheel 安装，但如遇问题可使用 conda 或 pipwin。

**安装（Windows PowerShell 示例）**

- 创建并激活虚拟环境（可选但推荐）：

```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

- 使用 pip 安装 Python 依赖：

```
pip install -r e:\download\sauc_yuyingshibie\sauc_python\requirements.txt
```

- 如果 `sounddevice` 安装出错（PortAudio/编译问题），可尝试：
  - 使用 conda：`conda install -c conda-forge sounddevice`
  - 或使用 pipwin 安装预编译 Windows wheel：

```
pip install pipwin
pipwin install sounddevice
```

- 安装 ffmpeg（如果需要处理文件）：下载并将 ffmpeg 的 `bin` 目录加入到 `PATH`。

**配置凭证**

- 打开 `e:\download\sauc_yuyingshibie\sauc_python\sauc_websocket_demo.py`，在 `Config` 类中填写你的 `app_key` 与 `access_key`：
  - `self.auth = {"app_key": "<your_app_key>", "access_key": "<your_access_key>"}`

**运行示例（PowerShell）**

- 使用麦克风实时识别：

```
python e:\download\sauc_yuyingshibie\sauc_python\sauc_websocket_demo.py --mic --url wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream
```

- 使用本地文件：

```
python e:\download\sauc_yuyingshibie\sauc_python\sauc_websocket_demo.py --file C:\path\to\audio.wav --url wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream
```

- 可选参数：
  - `--seg-duration` 每包时长（ms），默认为 200

**日志与调试**

- 脚本会同时输出日志到终端和 `run.log` 文件（工作目录下）。
- 常见问题：
  - `sounddevice` 导入/安装失败：参考上文的 conda 或 pipwin 方案。
  - 无识别返回或服务错误：检查 `app_key`/`access_key`、`--url` 是否正确，并查看 `run.log` 中的错误信息。
  - 麦克风无法采集：检查系统麦克风权限与设备是否被占用。

**实现与注意事项**

- 音频格式：脚本使用 16kHz、pcm_s16le、单声道（与服务请求字段匹配）。
- 结束包：停止后脚本会尝试发送一个空的最后包（is_last=True）通知服务流结束。

**下一步建议**

- 如果你需要，我可以：
  - 把凭证提取到 `config.json` 或 `.env` 并修改脚本读取；
  - 把识别结果同时写入文件或做更友好的实时提示。

# README

**asr tob 相关 client demo**

# Notice

python version: python 3.x

替换代码中的 key 为真实数据:
"app_key": "xxxxxxx",
"access_key": "xxxxxxxxxxxxxxxx"
使用示例：
python3 sauc_websocket_demo.py --file /Users/bytedance/code/python/eng_ddc_itn.wav
