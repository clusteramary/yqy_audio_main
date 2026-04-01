#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import locale
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from queue import Empty, Queue
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# =========================
# Config (editable)
# =========================
DEFAULT_CONFIG = {
    # Change to your conda root dir
    "CONDA_BASE": "/home/zxr/Software/anaconda3",
    "ROOT_DIR": "/home/zxr/Documents/DA_robot/interview",
    "YQY_DIR": "/home/zxr/Documents/DA_robot/interview/yqy_audio",
    # Task env names
    "ENV_DIRECT_CONTROL": "myx_realman",
    "ENV_GAZE": "robot_interview",
    "ENV_YQY": "yqy1",
    # Auto retry
    "DIRECT_CONTROL_RETRY_PATTERN": "连接失败",
    "DIRECT_CONTROL_MAX_RETRIES": 3,
}


def _app_dir() -> str:
    """Folder of script (dev) or executable (pyinstaller)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    """
    Load config from:
      1) ./launcher_config.json (next to script/exe)
      2) ~/.config/da_robot_launcher/config.json
    If none exists, use DEFAULT_CONFIG.
    """
    candidates = [
        os.path.join(_app_dir(), "launcher_config.json"),
        os.path.join(os.path.expanduser("~/.config/da_robot_launcher"), "config.json"),
    ]
    cfg = dict(DEFAULT_CONFIG)
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                if isinstance(user_cfg, dict):
                    cfg.update(user_cfg)
            except Exception:
                pass
            break
    return cfg


CFG = load_config()

CONDA_BASE = CFG["CONDA_BASE"]
ROOT_DIR = CFG["ROOT_DIR"]
YQY_DIR = CFG["YQY_DIR"]
CONDA_SH = os.path.join(CONDA_BASE, "etc/profile.d/conda.sh")


def bash_cmd(env_name: str, body: str) -> str:
    """Command string that can conda activate in non-interactive bash."""
    return f"""
set -e
source "{CONDA_SH}"
conda activate "{env_name}"
{body}
"""


class ManagedTask:
    """
    Manage one subprocess (new process group). Logs go to a queue via enqueue_log.
    """

    def __init__(
        self,
        name: str,
        cwd: str,
        cmd: str,
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

    @staticmethod
    def _decode_line(raw: bytes) -> str:
        raw = raw.replace(b"\r\n", b"\n")
        preferred = locale.getpreferredencoding(False) or "utf-8"
        for enc in ("utf-8", preferred, "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.cmd.strip().startswith("source") or "conda activate" in self.cmd:
            if not os.path.exists(CONDA_SH):
                messagebox.showerror(
                    "Conda not found",
                    f"Cannot find conda.sh:\n{CONDA_SH}\n\n"
                    f"Fix CONDA_BASE in launcher_config.json or in the script.",
                )
                return

        if self.is_running():
            self.enqueue_log(self.name, "Already running. Ignored start().")
            return

        self._stop_requested = False
        self._retries_left = self.max_retries

        popen_kwargs = dict(
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            text=False,
        )

        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["preexec_fn"] = os.setsid  # new process group

        # Use bash -lc so "source" and conda activation works reliably.
        self.proc = subprocess.Popen(["bash", "-lc", self.cmd], **popen_kwargs)

        self.enqueue_log(self.name, f"Started. PID={self.proc.pid}")
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def stop(self):
        if not self.is_running():
            self.enqueue_log(self.name, "Not running. Ignored stop().")
            return

        self._stop_requested = True

        pgid = None
        if os.name != "nt":
            try:
                pgid = os.getpgid(self.proc.pid)
            except Exception:
                pgid = None

        if self.stop_mode == "sigint":
            self.enqueue_log(self.name, "Sending SIGINT (Ctrl+C)...")
            try:
                if os.name == "nt":
                    self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                    self._wait_then_kill(None)
                else:
                    if pgid is not None:
                        os.killpg(pgid, signal.SIGINT)
                    else:
                        self.proc.send_signal(signal.SIGINT)
                    self._wait_then_kill(pgid)
            except ProcessLookupError:
                return
            except Exception as e:
                self.enqueue_log(self.name, f"SIGINT failed: {e}")
                self._wait_then_kill(pgid, force=True)

        elif self.stop_mode == "termkill":
            self.enqueue_log(self.name, "Sending SIGTERM...")
            try:
                if os.name == "nt":
                    self.proc.terminate()
                else:
                    if pgid is not None:
                        os.killpg(pgid, signal.SIGTERM)
                    else:
                        self.proc.terminate()
            except ProcessLookupError:
                return
            self._wait_then_kill(pgid, force=True)

    def _wait_then_kill(self, pgid, force=False):
        # wait a bit
        for _ in range(25):
            if not self.is_running():
                self.enqueue_log(self.name, "Stopped.")
                return
            time.sleep(0.1)

        # still alive => kill
        self.enqueue_log(self.name, "Still alive. Sending SIGKILL...")
        try:
            if os.name == "nt":
                self.proc.kill()
            else:
                if pgid is not None:
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    self.proc.kill()
        except ProcessLookupError:
            pass
        self.enqueue_log(self.name, "Killed.")

    def _reader_loop(self):
        try:
            while True:
                raw = self.proc.stdout.readline()
                if not raw:
                    break

                line = self._decode_line(raw).rstrip("\n")
                if line:
                    self.enqueue_log(self.name, line)

                # Auto retry (only if user didn't stop)
                if (
                    (not self._stop_requested)
                    and self.auto_retry_pattern
                    and (self.auto_retry_pattern in line)
                    and (self._retries_left > 0)
                ):
                    self.enqueue_log(
                        self.name,
                        f"Auto-retry triggered: '{self.auto_retry_pattern}' "
                        f"(remaining {self._retries_left})",
                    )
                    self._retries_left -= 1
                    self.stop()
                    time.sleep(0.8)
                    self.start()
                    return
        except Exception as e:
            self.enqueue_log(self.name, f"[reader error] {e}")
        finally:
            if self.proc and self.proc.poll() is not None:
                self.enqueue_log(self.name, f"Exited. code={self.proc.returncode}")
            self.proc = None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DA_robot Launcher")
        self.geometry("1180x720")
        self.minsize(1080, 650)

        ui_font, mono_font = self._pick_fonts()

        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family=ui_font, size=12)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", font=(ui_font, 12))
        style.configure("TButton", padding=(12, 9), font=(ui_font, 12))
        style.configure("Header.TLabel", font=(ui_font, 16, "bold"))
        style.configure("SubHeader.TLabel", font=(ui_font, 11))
        style.configure("Card.TLabelframe.Label", font=(ui_font, 12, "bold"))
        style.configure("Status.TLabel", font=(ui_font, 12, "bold"))
        style.configure("TNotebook.Tab", font=(ui_font, 12))

        # log queue
        self.log_queue: Queue[tuple[str, str]] = Queue()

        # main split
        main = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        main.pack(fill="both", expand=True, padx=12, pady=12)

        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=1)
        main.add(right, weight=3)

        # left header
        ttk.Label(left, text="Control Panel", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="Start/Stop tasks and watch logs in real-time.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        topbar = ttk.Frame(left)
        topbar.pack(fill="x", pady=(0, 10))
        ttk.Button(topbar, text="Start All", command=self.start_all).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(topbar, text="Stop All", command=self.stop_all).pack(side="left")

        ttk.Separator(left).pack(fill="x", pady=10)

        # right header + logs
        ttk.Label(right, text="Logs", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            right,
            text="Summary + per-task tabs",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)

        self.text_all = self._make_log_tab("Summary", mono_font)
        self.text_by_task = {}

        # tasks
        self.status_vars = {}
        self.status_dots = {}  # task.name -> Canvas oval item id

        self.tasks = {}
        self._define_tasks()

        # task cards + task tabs
        for key, task in self.tasks.items():
            self.text_by_task[task.name] = self._make_log_tab(task.name, mono_font)
            self._make_task_card(left, task)

        # periodic UI update
        self.after(60, self._drain_logs)
        self.after(400, self._refresh_status)

        # helpful hint (English UI)
        hint = (
            "Notes:\n"
            "• Recommended order: roscore → direct_control → gaze → yqy_audio\n"
            "• direct_control: auto-retry on connection failure (max retries configurable)\n"
            "• yqy_audio: Stop uses TERM → KILL (for stubborn processes)\n"
            "• Config: put launcher_config.json next to the binary/script to avoid editing code."
        )
        ttk.Label(left, text=hint, justify="left", wraplength=360).pack(
            anchor="w", pady=12
        )

    def _pick_fonts(self) -> tuple[str, str]:
        families = set(tkfont.families(self))
        system = platform.system().lower()

        if system.startswith("win"):
            ui_candidates = ["Segoe UI", "Arial", "Tahoma"]
            mono_candidates = ["Cascadia Mono", "Consolas", "Courier New"]
        elif system.startswith("darwin"):
            ui_candidates = ["SF Pro Text", "Helvetica", "Arial"]
            mono_candidates = ["Menlo", "Monaco", "Courier"]
        else:
            # Ubuntu typically has DejaVu; Noto may also exist.
            ui_candidates = ["DejaVu Sans", "Noto Sans", "Ubuntu", "Liberation Sans"]
            mono_candidates = [
                "DejaVu Sans Mono",
                "Noto Sans Mono",
                "Ubuntu Mono",
                "Monospace",
            ]

        ui_font = next((f for f in ui_candidates if f in families), "TkDefaultFont")
        mono_font = next((f for f in mono_candidates if f in families), "TkFixedFont")
        return ui_font, mono_font

    def enqueue_log(self, name: str, msg: str):
        self.log_queue.put((name, msg))

    def _make_log_tab(self, title: str, mono_font_family: str):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text=title)

        text = ScrolledText(frame, wrap="word", font=(mono_font_family, 12))
        text.pack(fill="both", expand=True)
        text.configure(state="disabled")
        return text

    def _append_text(self, text_widget: tk.Text, line: str):
        text_widget.configure(state="normal")
        text_widget.insert("end", line + "\n")
        text_widget.see("end")
        text_widget.configure(state="disabled")

    def _drain_logs(self):
        try:
            while True:
                name, msg = self.log_queue.get_nowait()
                self._append_text(self.text_all, f"[{name}] {msg}")
                tw = self.text_by_task.get(name)
                if tw is not None:
                    self._append_text(tw, msg)
        except Empty:
            pass
        self.after(60, self._drain_logs)

    def _refresh_status(self):
        for _, task in self.tasks.items():
            var = self.status_vars.get(task.name)
            if var is None:
                continue
            running = task.is_running()
            var.set("RUNNING" if running else "STOPPED")

            # update dot
            canvas, dot_id = self.status_dots.get(task.name, (None, None))
            if canvas and dot_id:
                canvas.itemconfig(dot_id, fill=("#22c55e" if running else "#ef4444"))

        self.after(400, self._refresh_status)

    def _make_task_card(self, parent: ttk.Frame, task: ManagedTask):
        card = ttk.Labelframe(parent, text=task.name, style="Card.TLabelframe")
        card.pack(fill="x", pady=8)

        row1 = ttk.Frame(card)
        row1.pack(fill="x", pady=(8, 4), padx=10)

        # status dot
        dot_canvas = tk.Canvas(row1, width=16, height=16, highlightthickness=0)
        dot_canvas.pack(side="left", padx=(0, 6))
        dot_id = dot_canvas.create_oval(3, 3, 13, 13, fill="#ef4444", outline="")

        ttk.Label(row1, text="Status:", style="Status.TLabel").pack(side="left")
        status_var = tk.StringVar(value="STOPPED")
        self.status_vars[task.name] = status_var
        ttk.Label(row1, textvariable=status_var, style="Status.TLabel").pack(
            side="left", padx=(6, 0)
        )

        self.status_dots[task.name] = (dot_canvas, dot_id)

        row2 = ttk.Frame(card)
        row2.pack(fill="x", pady=(2, 10), padx=10)

        ttk.Button(row2, text="Start", command=task.start).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(row2, text="Stop", command=task.stop).pack(side="left", padx=(0, 8))
        ttk.Button(
            row2, text="Clear Log", command=lambda n=task.name: self._clear_task_log(n)
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            row2, text="View Log", command=lambda n=task.name: self._select_log_tab(n)
        ).pack(side="left")

    def _select_log_tab(self, task_name: str):
        # find tab index by title
        for i in range(self.nb.index("end")):
            if self.nb.tab(i, "text") == task_name:
                self.nb.select(i)
                break

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

        # 2) direct_control
        cmd2 = bash_cmd(
            CFG["ENV_DIRECT_CONTROL"],
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
            auto_retry_pattern=CFG["DIRECT_CONTROL_RETRY_PATTERN"],
            max_retries=int(CFG["DIRECT_CONTROL_MAX_RETRIES"]),
        )

        # 3) gaze.launch
        cmd3 = bash_cmd(
            CFG["ENV_GAZE"],
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

        # 4) yqy_audio main.py (TERM -> KILL)
        cmd4 = bash_cmd(
            CFG["ENV_YQY"],
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
        for k in ["roscore", "direct_control", "gaze", "yqy_audio"]:
            self.tasks[k].start()

    def stop_all(self):
        for k in ["yqy_audio", "gaze", "direct_control", "roscore"]:
            self.tasks[k].stop()


if __name__ == "__main__":
    App().mainloop()
