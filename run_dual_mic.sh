#!/bin/bash
# run_dual_mic.sh
# 双麦克风语音交互系统启动脚本
# 用于设置必要的环境变量，避免 PyAudio 初始化问题

# 设置环境变量以避免 ALSA/JACK 错误
export PA_ALSA_PLUGHW=1
export JACK_NO_AUDIO_RESERVATION=1
export PULSE_LATENCY_MSEC=60

# 抑制 ALSA 错误输出（可选，不影响功能）
# export ALSA_LOG_LEVEL=0

echo "=========================================="
echo "双麦克风语音交互系统启动"
echo "=========================================="
echo ""
echo "环境变量已设置："
echo "  PA_ALSA_PLUGHW=1"
echo "  JACK_NO_AUDIO_RESERVATION=1"
echo "  PULSE_LATENCY_MSEC=60"
echo ""

# 运行 Python 程序
python main_dual_mic.py
