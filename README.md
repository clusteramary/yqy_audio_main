# RealtimeDialog

实时语音对话程序，支持语音输入和语音输出。

## 使用说明

此demo使用python3.7环境进行开发调试，其他python版本可能会有兼容性问题，需要自己尝试解决。

1. 配置API密钥
   - 打开 `config.py` 文件
   - 修改以下两个字段：
     ```python
     "X-Api-App-ID": "火山控制台上端到端大模型对应的App ID",
     "X-Api-Access-Key": "火山控制台上端到端大模型对应的Access Key",
     ```
   - 修改speaker字段指定发音人，本次支持四个发音人：
     - `zh_female_vv_jupiter_bigtts`：中文vv女声
     - `zh_female_xiaohe_jupiter_bigtts`：中文xiaohe女声
     - `zh_male_yunzhou_jupiter_bigtts`：中文云洲男声
     - `zh_male_xiaotian_jupiter_bigtts`：中文小天男声

2. 安装依赖
   ```bash
   pip install -r requirements.txt
   
3. 默认启动（本地 PyAudio 麦克风 + ROS 下位机扬声器 + 半双工）
   ```bash
   python main.py
   ```

4. 切换双工或音频输入/输出
   ```bash
   python main.py --duplex-mode half --input-audio-mode pyaudio --output-audio-mode ros1
   python main.py --duplex-mode full --input-audio-mode pyaudio --output-audio-mode ros1
   ```

   半双工恢复开麦延迟在 `config.py` 的 `HALF_DUPLEX_RESUME_DELAY_MS` 中调整，设为 `0` 表示下位机播放结束后立即开麦。
