import os
import signal
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext

# ====== 你需要确认这里 ======
CONDA_BASE = (
    "/home/zxr/miniconda3"  # ← 改成你的 conda 根目录(里面应有 etc/profile.d/conda.sh)
)
CONDA_SH = os.path.join(CONDA_BASE, "etc/profile.d/conda.sh")

# ====== 项目路径 ======
ROOT_DIR = "/home/zxr/Documents/DA_robot/interview"
YQY_DIR = "/home/zxr/Documents/DA_robot/interview/yqy_audio"


def bash_cmd(env_name: str, body: str) -> str:
    """
    拼出能在非交互 shell 正常 conda activate 的命令串
    """
    return f"""
set -e
source "{CONDA_SH}"
conda activate "{env_name}"
{body}
"""


class ManagedTask:
    def __init__(
        self,
        name,
        cwd,
        cmd,
        log_fn,
        stop_mode="sigint",  # sigint / termkill
        auto_retry_pattern=None,
        max_retries=0,
    ):
        self.name = name
        self.cwd = cwd
        self.cmd = cmd
        self.log_fn = log_fn
        self.stop_mode = stop_mode
        self.auto_retry_pattern = auto_retry_pattern
        self.max_retries = max_retries

        self.proc = None
        self._reader_thread = None
        self._stop_requested = False
        self._retries_left = max_retries

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if not os.path.exists(CONDA_SH):
            messagebox.showerror(
                "错误", f"找不到 conda.sh：\n{CONDA_SH}\n请把 CONDA_BASE 改对。"
            )
            return
        if self.is_running():
            self.log_fn(self.name, "已在运行，忽略 start。")
            return

        self._stop_requested = False
        # 开新进程组，方便 stop 时一锅端（包括 roslaunch/rosrun 子进程）
        self.proc = subprocess.Popen(
            ["bash", "-lc", self.cmd],
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,
        )
        self.log_fn(self.name, f"启动成功 PID={self.proc.pid}")
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def stop(self):
        if not self.is_running():
            self.log_fn(self.name, "未运行，忽略 stop。")
            return

        self._stop_requested = True
        pgid = os.getpgid(self.proc.pid)

        if self.stop_mode == "sigint":
            # 更像 Ctrl+C
            self.log_fn(self.name, "发送 SIGINT (模拟 Ctrl+C)...")
            try:
                os.killpg(pgid, signal.SIGINT)
            except ProcessLookupError:
                return
            self._wait_then_kill(pgid)

        elif self.stop_mode == "termkill":
            # 对“Ctrl+C 关不掉”的程序：先 TERM 再 KILL
            self.log_fn(self.name, "发送 SIGTERM...")
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                return
            self._wait_then_kill(pgid, force=True)

    def _wait_then_kill(self, pgid, force=False):
        # 等一小会儿
        for _ in range(20):
            if not self.is_running():
                self.log_fn(self.name, "已结束。")
                return
            time.sleep(0.1)

        # 仍活着就强杀
        self.log_fn(self.name, "仍未退出，发送 SIGKILL...")
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.log_fn(self.name, "已强制结束。")

    def _reader_loop(self):
        try:
            for line in self.proc.stdout:
                line = line.rstrip("\n")
                if line:
                    self.log_fn(self.name, line)

                # 自动重试：仅在用户没点 stop 的情况下生效
                if (
                    not self._stop_requested
                    and self.auto_retry_pattern
                    and self.auto_retry_pattern in line
                    and self._retries_left > 0
                ):
                    self.log_fn(
                        self.name,
                        f"检测到「{self.auto_retry_pattern}」，准备重试（剩余 {self._retries_left} 次）",
                    )
                    self._retries_left -= 1
                    # 重启流程：stop -> 稍等 -> start
                    self.stop()
                    time.sleep(0.8)
                    self.start()
                    return  # 退出当前 reader（新 start 会再起一个）

        except Exception as e:
            self.log_fn(self.name, f"[reader异常] {e}")
        finally:
            # 进程自然退出
            if self.proc and self.proc.poll() is not None:
                self.log_fn(self.name, f"进程退出 code={self.proc.returncode}")
            self.proc = None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DA_robot 一键启动器")
        self.geometry("980x620")

        self.log = scrolledtext.ScrolledText(self, height=18)
        self.log.pack(fill="both", expand=True, padx=10, pady=10)

        def log_fn(name, msg):
            self.log.insert("end", f"[{name}] {msg}\n")
            self.log.see("end")

        # ====== 任务定义 ======
        self.tasks = {}

        # 1) roscore
        self.tasks["roscore"] = ManagedTask(
            name="roscore",
            cwd=os.path.expanduser("~"),
            cmd="roscore",
            log_fn=log_fn,
            stop_mode="sigint",
        )

        # 2) direct_control interview_emo_1204_a.py (连接失败自动重试 3 次)
        cmd2 = bash_cmd(
            "myx_realman",
            f"""
cd "{ROOT_DIR}"
source devel/setup.bash
rosrun direct_control interview_emo_1204_a.py
""",
        )
        self.tasks["direct_control"] = ManagedTask(
            name="direct_control",
            cwd=ROOT_DIR,
            cmd=cmd2,
            log_fn=log_fn,
            stop_mode="sigint",
            auto_retry_pattern="连接失败",
            max_retries=3,
        )

        # 3) gaze.launch
        cmd3 = bash_cmd(
            "robot_interview",
            f"""
cd "{ROOT_DIR}"
source ./devel/setup.bash
roslaunch ./launch/gaze.launch
""",
        )
        self.tasks["gaze"] = ManagedTask(
            name="gaze", cwd=ROOT_DIR, cmd=cmd3, log_fn=log_fn, stop_mode="sigint"
        )

        # 4) yqy_audio main.py（你说 Ctrl+C 关不掉 → 用 term/kill）
        cmd4 = bash_cmd(
            "yqy1",
            f"""
cd "{YQY_DIR}"
python main.py
""",
        )
        self.tasks["yqy_audio"] = ManagedTask(
            name="yqy_audio", cwd=YQY_DIR, cmd=cmd4, log_fn=log_fn, stop_mode="termkill"
        )

        # ====== 按钮区域 ======
        panel = tk.Frame(self)
        panel.pack(fill="x", padx=10, pady=6)

        def add_row(task_key, title):
            row = tk.Frame(panel)
            row.pack(fill="x", pady=4)

            tk.Label(row, text=title, width=28, anchor="w").pack(side="left")
            tk.Button(
                row, text="开始", width=10, command=self.tasks[task_key].start
            ).pack(side="left", padx=4)
            tk.Button(
                row, text="结束", width=10, command=self.tasks[task_key].stop
            ).pack(side="left", padx=4)

        add_row("roscore", "第1终端：roscore")
        add_row("direct_control", "第2终端：direct_control（失败自动重试）")
        add_row("gaze", "第3终端：头部跟随 gaze.launch")
        add_row("yqy_audio", "第4终端：yqy_audio main.py（强制结束）")

        tips = (
            "注意：\n"
            "1) 启动顺序建议：先 roscore，再 direct_control / gaze，再 yqy_audio。\n"
            "2) direct_control：启动后 3 秒内右手自由舞动；若日志出现“连接失败”会自动重试最多 3 次。\n"
            "3) yqy_audio：你说 Ctrl+C 关不掉，所以“结束”会 TERM→KILL。\n"
            "4) yqy_audio 启动前请确保电脑连手机流量 WiFi，不连校园网。\n"
        )
        tk.Label(self, text=tips, justify="left").pack(anchor="w", padx=12, pady=6)


if __name__ == "__main__":
    App().mainloop()
