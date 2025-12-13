import os
import signal
import subprocess
import threading
import time
import tkinter as tk
from queue import Empty, Queue
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# ========= 你需要确认这里 =========
CONDA_BASE = "/home/zxr/Software/anaconda3"  # ← 改成你的 conda 根目录
ROOT_DIR = "/home/zxr/Documents/DA_robot/interview"
YQY_DIR = "/home/zxr/Documents/DA_robot/interview/yqy_audio"

CONDA_SH = os.path.join(CONDA_BASE, "etc/profile.d/conda.sh")


def bash_cmd(env_name: str, body: str) -> str:
    """拼出能在非交互 shell 正常 conda activate 的命令串"""
    return f"""
set -e
source "{CONDA_SH}"
conda activate "{env_name}"
{body}
"""


class ManagedTask:
    """
    管理一个子进程（开进程组，方便 SIGINT/TERM/KILL 一锅端）。
    日志通过 enqueue_log 推进队列，由主线程刷新到UI（线程安全）。
    """

    def __init__(
        self,
        name,
        cwd,
        cmd,
        enqueue_log,
        stop_mode="sigint",  # "sigint" / "termkill"
        auto_retry_pattern=None,
        max_retries=0,
    ):
        self.name = name
        self.cwd = cwd
        self.cmd = cmd
        self.enqueue_log = enqueue_log
        self.stop_mode = stop_mode
        self.auto_retry_pattern = auto_retry_pattern
        self.max_retries = max_retries

        self.proc = None
        self._stop_requested = False
        self._retries_left = max_retries
        self._reader_thread = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if not os.path.exists(CONDA_SH):
            messagebox.showerror(
                "错误", f"找不到 conda.sh：\n{CONDA_SH}\n请把 CONDA_BASE 改对。"
            )
            return

        if self.is_running():
            self.enqueue_log(self.name, "已在运行，忽略 start。")
            return

        self._stop_requested = False
        # 每次手动 start 时，把重试次数恢复
        self._retries_left = self.max_retries

        self.proc = subprocess.Popen(
            ["bash", "-lc", self.cmd],
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,  # 新进程组
        )

        self.enqueue_log(self.name, f"启动成功 PID={self.proc.pid}")
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def stop(self):
        if not self.is_running():
            self.enqueue_log(self.name, "未运行，忽略 stop。")
            return

        self._stop_requested = True
        pgid = os.getpgid(self.proc.pid)

        if self.stop_mode == "sigint":
            self.enqueue_log(self.name, "发送 SIGINT（模拟 Ctrl+C）...")
            try:
                os.killpg(pgid, signal.SIGINT)
            except ProcessLookupError:
                return
            self._wait_then_kill(pgid)

        elif self.stop_mode == "termkill":
            # 针对你说的“无法 Ctrl+C”的程序：TERM -> KILL
            self.enqueue_log(self.name, "发送 SIGTERM...")
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                return
            self._wait_then_kill(pgid, force=True)

    def _wait_then_kill(self, pgid, force=False):
        # 等一会儿看看是否退出
        for _ in range(25):
            if not self.is_running():
                self.enqueue_log(self.name, "已结束。")
                return
            time.sleep(0.1)

        # 仍活着就强杀
        self.enqueue_log(self.name, "仍未退出，发送 SIGKILL...")
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.enqueue_log(self.name, "已强制结束。")

    def _reader_loop(self):
        try:
            for line in self.proc.stdout:
                line = line.rstrip("\n")
                if line:
                    self.enqueue_log(self.name, line)

                # 自动重试：仅在用户没点 stop 的情况下
                if (
                    not self._stop_requested
                    and self.auto_retry_pattern
                    and self.auto_retry_pattern in line
                    and self._retries_left > 0
                ):
                    self.enqueue_log(
                        self.name,
                        f"检测到「{self.auto_retry_pattern}」，准备自动重试（剩余 {self._retries_left} 次）",
                    )
                    self._retries_left -= 1
                    self.stop()
                    time.sleep(0.8)
                    self.start()
                    return
        except Exception as e:
            self.enqueue_log(self.name, f"[reader异常] {e}")
        finally:
            if self.proc and self.proc.poll() is not None:
                self.enqueue_log(self.name, f"进程退出 code={self.proc.returncode}")
            self.proc = None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DA_robot 一键启动器（美化版）")
        self.geometry("1100x680")

        # ========= 字体/样式：统一放大 =========
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(size=12)
        text_font = ("DejaVu Sans Mono", 12)  # 日志区等宽字体更好看

        style = ttk.Style(self)
        try:
            style.theme_use("clam")  # 比默认更顺眼
        except:
            pass

        style.configure("TButton", padding=(12, 8), font=("DejaVu Sans", 12))
        style.configure("Title.TLabel", font=("DejaVu Sans", 14, "bold"))
        style.configure("Status.TLabel", font=("DejaVu Sans", 12, "bold"))
        style.configure("Card.TLabelframe.Label", font=("DejaVu Sans", 13, "bold"))

        # ========= 日志队列（线程安全） =========
        self.log_queue: Queue[tuple[str, str]] = Queue()

        # ========= 主布局：左右分栏（左控制，右日志） =========
        main = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=1)
        main.add(right, weight=3)

        # ========= 左侧：控制面板 =========
        ttk.Label(left, text="控制面板", style="Title.TLabel").pack(
            anchor="w", pady=(0, 8)
        )

        toolbar = ttk.Frame(left)
        toolbar.pack(fill="x", pady=(0, 10))

        ttk.Button(toolbar, text="全部启动", command=self.start_all).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar, text="全部结束", command=self.stop_all).pack(side="left")

        ttk.Separator(left).pack(fill="x", pady=10)

        self.status_vars = {}

        # ========= 右侧：日志输出（Tab：汇总 + 每任务一页） =========
        ttk.Label(right, text="命令输出 / 日志", style="Title.TLabel").pack(
            anchor="w", pady=(0, 8)
        )

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)

        self.text_all = self._make_log_tab("汇总日志", text_font)
        self.text_by_task = {}  # task_name -> Text

        # ========= 定义任务 =========
        self.tasks = {}
        self._define_tasks()

        # 给每个任务创建日志 Tab + 控制卡片
        for key, task in self.tasks.items():
            self.text_by_task[task.name] = self._make_log_tab(f"{task.name}", text_font)
            self._make_task_card(left, task)

        # 定时刷新UI日志
        self.after(60, self._drain_logs)

        # 定时刷新状态（运行/停止）
        self.after(400, self._refresh_status)

        # 提示
        tips = (
            "使用建议：先启动 roscore → direct_control/gaze → yqy_audio。\n"
            "direct_control：启动后 3 秒内右手自由舞动；若出现“连接失败”会自动重试最多 3 次。\n"
            "yqy_audio：你说 Ctrl+C 关不掉，所以结束按钮会 TERM→KILL 强制结束。\n"
            "yqy_audio 启动前：电脑连手机流量 WiFi，别连校园网。"
        )
        tip_label = ttk.Label(left, text=tips, wraplength=360, justify="left")
        tip_label.pack(anchor="w", pady=10)

    def enqueue_log(self, name: str, msg: str):
        self.log_queue.put((name, msg))

    def _make_log_tab(self, title: str, text_font):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text=title)

        text = ScrolledText(frame, wrap="word", font=text_font)
        text.pack(fill="both", expand=True)
        text.configure(state="disabled")
        return text

    def _append_text(self, text_widget: tk.Text, line: str):
        text_widget.configure(state="normal")
        text_widget.insert("end", line + "\n")
        text_widget.see("end")
        text_widget.configure(state="disabled")

    def _drain_logs(self):
        # 主线程把队列里的日志刷进 UI
        try:
            while True:
                name, msg = self.log_queue.get_nowait()
                line = f"[{name}] {msg}"
                self._append_text(self.text_all, line)

                tw = self.text_by_task.get(name)
                if tw is not None:
                    self._append_text(tw, msg)
        except Empty:
            pass

        self.after(60, self._drain_logs)

    def _refresh_status(self):
        # 更新状态显示
        for key, task in self.tasks.items():
            var = self.status_vars.get(task.name)
            if var is None:
                continue
            var.set("运行中" if task.is_running() else "已停止")
        self.after(400, self._refresh_status)

    def _make_task_card(self, parent: ttk.Frame, task: ManagedTask):
        card = ttk.Labelframe(parent, text=task.name, style="Card.TLabelframe")
        card.pack(fill="x", pady=8)

        row1 = ttk.Frame(card)
        row1.pack(fill="x", pady=(6, 4), padx=8)

        ttk.Label(row1, text="状态：", style="Status.TLabel").pack(side="left")
        status_var = tk.StringVar(value="已停止")
        self.status_vars[task.name] = status_var
        ttk.Label(row1, textvariable=status_var, style="Status.TLabel").pack(
            side="left"
        )

        row2 = ttk.Frame(card)
        row2.pack(fill="x", pady=(2, 8), padx=8)

        ttk.Button(row2, text="开始", command=task.start).pack(side="left", padx=(0, 8))
        ttk.Button(row2, text="结束", command=task.stop).pack(side="left", padx=(0, 8))
        ttk.Button(
            row2, text="清空该任务日志", command=lambda: self._clear_task_log(task.name)
        ).pack(side="left")

    def _clear_task_log(self, task_name: str):
        tw = self.text_by_task.get(task_name)
        if tw is None:
            return
        tw.configure(state="normal")
        tw.delete("1.0", "end")
        tw.configure(state="disabled")

    def _define_tasks(self):
        # 1) roscore
        self.tasks["roscore"] = ManagedTask(
            name="roscore",
            cwd=os.path.expanduser("~"),
            cmd="roscore",
            enqueue_log=self.enqueue_log,
            stop_mode="sigint",
        )

        # 2) direct_control（连接失败自动重试 3 次）
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
            enqueue_log=self.enqueue_log,
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
            name="gaze",
            cwd=ROOT_DIR,
            cmd=cmd3,
            enqueue_log=self.enqueue_log,
            stop_mode="sigint",
        )

        # 4) yqy_audio main.py（TERM->KILL）
        cmd4 = bash_cmd(
            "yqy1",
            f"""
cd "{YQY_DIR}"
python main.py
""",
        )
        self.tasks["yqy_audio"] = ManagedTask(
            name="yqy_audio",
            cwd=YQY_DIR,
            cmd=cmd4,
            enqueue_log=self.enqueue_log,
            stop_mode="termkill",
        )

    def start_all(self):
        # 建议按依赖顺序启动
        for k in ["roscore", "direct_control", "gaze", "yqy_audio"]:
            self.tasks[k].start()

    def stop_all(self):
        # 结束顺序反过来更安全
        for k in ["yqy_audio", "gaze", "direct_control", "roscore"]:
            self.tasks[k].stop()


if __name__ == "__main__":
    App().mainloop()
