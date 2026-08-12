#!/usr/bin/env bash
# ============================================================
# 启动入口：main.py（ROS1 扬声器播放，话题 /audio）
# 用法：
#   1) 桌面双击 main_ROS扬声器.desktop
#   2) 项目内终端运行: bash run_ros_speaker.sh
# 日志：logs/run_ros_speaker_*.log
# ============================================================
set -u
cd "$(dirname "$0")" || exit 1
mkdir -p logs
# ---- 调试追踪（排查桌面双击闪退）：所有输出和报错都进 launcher_debug.log ----
exec 2>>logs/launcher_debug.log
DBG(){ echo "[$(date +%F_%T)] $*" >> logs/launcher_debug.log; }
DBG "=== ${0##*/} 启动 pid=$$ cwd=$(pwd) ==="
DBG "uname=$(uname -a | cut -c1-60)  bash=$BASH_VERSION"
LOG="logs/run_ros_speaker_$(date +%Y%m%d_%H%M%S).log"

# ---------- 前置检查 ----------
DBG "检查 venv: $(test -x .venv/bin/python && echo OK || echo FAIL)"
if [ ! -x .venv/bin/python ]; then
    echo "[错误] 未找到 .venv/bin/python，请先创建虚拟环境（见 README.md）"
    echo "=== 10 秒后自动关闭 ==="
    sleep 10
    exit 1
fi

# ---- 机器人 ROS 环境（与 ~/.bashrc 一致）----
# 桌面双击由 gnome-shell 启动，不读 .bashrc，这里补齐；
# 若已在终端配置过（或手动 export）则保持原值。
# 注意：必须在 source setup.bash 之前设置，否则 10.roslaunch.sh 会
# 在变量未设置时把它重置为 http://localhost:11311。
export ROS_DISTRO="${ROS_DISTRO:-noetic}"
export ROS_IP="${ROS_IP:-192.168.10.100}"
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://192.168.10.66:11311}"

DBG "检查 ROS: $(test -f /opt/ros/noetic/setup.bash && echo OK || echo FAIL)"
if [ -f /opt/ros/noetic/setup.bash ]; then
    set +u  # setup.bash 内部引用 $ROS_DISTRO，在无 ROS 环境变量时 set -u 会直接退出
    source /opt/ros/noetic/setup.bash
    set -u
else
    echo "[警告] 未找到 /opt/ros/noetic/setup.bash，跳过 ROS 初始化"
fi
DBG "ROS_IP=$ROS_IP  ROS_MASTER_URI=$ROS_MASTER_URI"

DBG "检查 roscore: $(rostopic list >/dev/null 2>&1 && echo OK || echo FAIL)"
if ! rostopic list >/dev/null 2>&1; then
    echo "[错误] 未检测到 roscore（ROS 主节点）。"
    echo "       请先在终端启动: roscore"
    echo "       或改双击 main_本地播放.desktop（不依赖 ROS 扬声器）"
    echo "=== 10 秒后自动关闭 ==="
    sleep 10
    exit 1
fi

# ---------- 启动 ----------
DBG "前置检查全部通过，开始启动主程序"
echo "[启动] OUTPUT_AUDIO_MODE=ros1  main.py"
echo "[日志] $PWD/$LOG"
echo "[提示] 按 Ctrl+C 停止程序"
OUTPUT_AUDIO_MODE=ros1 .venv/bin/python -u main.py 2>&1 | tee -a "$LOG"
echo
echo "=== 程序已退出，日志见: $LOG ==="

# ---------- 保持窗口（防闪退：桌面双击时窗口至少停留 10 分钟） ----------
if [ -t 0 ]; then
    read -r -p "按回车关闭窗口..."
else
    echo "（窗口 10 分钟后自动关闭，也可直接点 × 关闭）"
    sleep 600
fi
