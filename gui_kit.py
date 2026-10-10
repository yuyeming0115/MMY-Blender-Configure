"""MMY GUI 公共组件库。

迁移 GUI（mmy_migrate_gui.py）与打包 GUI（mmy_pack_gui.py）共用本模块，
保证两个工具的字体、间距、配色、组件形态完全一致。

设计要点（见 开发文档/GUI迁移打包工具设计.md）：

1. **拖拽优雅降级**：``tkinterdnd2`` 可用时投放区支持拖入，不可用时自动
   退化为「点击选择」，功能完整不缺失——绝不允许因缺依赖而双击无反应。
2. **子线程执行**：迁移/打包是长任务，必须离开主线程。所有 UI 更新经由
   ``queue.Queue`` + ``root.after`` 回到主线程完成（tkinter 非线程安全）。
3. **进度回调绝不引发故障**：回调由本模块提供，核心层已用 try/except 包裹，
   GUI 自身的问题绝不能影响迁移结果。
"""

from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from collections.abc import Callable, Iterable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# 纯逻辑层单独成模块（零依赖，便于无 tkinter 环境测试）；此处转出，
# 使调用方统一从 gui_kit 取用，UI 与逻辑的边界只在这里一处。
from gui_logic import (  # noqa: F401
    explain_error,
    first_matching_path,
    parse_drop_paths,
)

# ============================================================
# 主题常量（迁移与打包 GUI 共用，确保视觉一致）
# ============================================================

FONT_FAMILY = "Microsoft YaHei UI"
FONT_SIZE = 11          # 正文；视力适配，不低于 11pt
MONO_FAMILY = "Consolas"
MONO_SIZE = 10
TITLE_SIZE = 15

COLOR_PRIMARY = "#2d6cdf"
COLOR_SUCCESS = "#1a7f37"
COLOR_ERROR = "#cf222e"
COLOR_MUTED = "#57606a"
COLOR_BORDER = "#d0d7de"
COLOR_LOG_BG = "#f6f8fa"
COLOR_CARD_BG = "#ffffff"

# 阶段状态符号
ICON_PENDING = "○"
ICON_ACTIVE = "⏳"
ICON_DONE = "✔"
ICON_FAILED = "✖"


# ============================================================
# 拖拽能力探测
# ============================================================

try:  # 拖拽是可选增强：缺失时降级为点击选择，不能因此无法启动
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:  # noqa: BLE001 —— 任何导入失败都视为不可用
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False


def _enable_dpi_awareness() -> None:
    """让 tkinter 在高 DPI 屏幕下不发虚（必须在创建窗口前调用）。"""
    if os.name != "nt":
        return
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 —— 老系统没有该 API，忽略
        pass


def make_root(title: str, geometry: str = "1000x760") -> tk.Tk:
    """创建统一风格的主窗口（有拖拽库时用 TkinterDnD 根窗口）。"""
    _enable_dpi_awareness()
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    root.title(title)
    root.geometry(geometry)
    root.minsize(860, 560)
    try:
        style = ttk.Style(root)
        style.theme_use("clam")  # 跨平台一致的外观
    except tk.TclError:  # pragma: no cover —— 极老 Tcl 缺 clam
        pass
    return root


# ============================================================
# 可滚动容器
# ============================================================


class ScrollPane(ttk.Frame):
    """带竖向滚动条的容器，窗口再小也不会把内容挤到看不见的地方。

    这是本工具的一个硬需求：结果卡片 pack 在最底部，而进度区会
    ``expand=True`` 抢走全部剩余空间——不做滚动的话，小屏用户永远看不到
    「导入成功」，看起来就像程序没反应。
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._canvas = tk.Canvas(
            self,
            highlightthickness=0,
            borderwidth=0,
            background=COLOR_CARD_BG,
        )
        self._bar = ttk.Scrollbar(
            self, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._on_scroll_set)
        self._bar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # 内容容器：嵌在 Canvas 里，随内容高度自动扩展
        self.body = ttk.Frame(self._canvas, padding=14)
        self._window = self._canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.body.bind(
            "<Configure>", lambda _e: self._on_body_configure(), add="bind"
        )
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # 滚轮支持（Windows 下 tkinter 默认不绑定）
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind_all(sequence, self._on_wheel, add="bind")

    def _on_scroll_set(self, first: str, last: str) -> None:
        """滚到底时自动隐藏滚动条，避免长期占一条无用灰条。"""
        self._bar.set(first, last)
        self._bar.pack_forget() if first == "0.0" and last == "1.0" else self._bar.pack(
            side="right", fill="y"
        )

    def _on_body_configure(self) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:  # noqa: ANN001
        self._canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event) -> str:  # noqa: ANN001
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self._canvas.yview_scroll(delta, "units")
        return "break"

    def scroll_to(self, widget: tk.Misc) -> None:
        """把指定控件滚动到可视区域（用于完成时露出结果卡片）。"""
        self.body.update_idletasks()
        offset = widget.winfo_rooty() - self._canvas.winfo_rooty()
        total = max(1, self.body.winfo_height())
        # 40px 视作控件自身高度的一部分，避免控件顶部被边缘切掉
        self._canvas.yview_moveto(max(0.0, (offset - 40) / total))


# ============================================================
# stdout 捕获（把核心层的 print 变成实时日志）
# ============================================================


class _StdoutTee:
    """逐行缓冲的 stdout 替身：避免半行刷屏，同时兜住 pythonw 的 None 流。"""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._original = sys.stdout
        self._buffer = ""

    def write(self, text: str) -> int:  # noqa: D102 - 遵循文件对象协议
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._emit(line.rstrip())
        return len(text)

    def flush(self) -> None:  # noqa: D102
        if self._buffer.strip():
            self._emit(self._buffer.strip())
            self._buffer = ""
        if self._original is not None:
            with contextlib.suppress(Exception):
                self._original.flush()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:  # 部分库会读这个属性
        return "utf-8"


# ============================================================
# 子线程执行器
# ============================================================


class TaskRunner:
    """在子线程执行长任务，把阶段/日志/结果经队列送回主线程。

    ``work`` 签名：``work(stage, log) -> result``

    - ``stage(stage_key, message=None, ratio=None)`` 上报阶段
    - ``log(message)`` 写一行日志
    """

    POLL_MS = 80

    def __init__(
        self,
        root: tk.Misc,
        on_stage: Callable[[str, str, float | None], None],
        on_log: Callable[[str], None],
        on_done: Callable[[object], None],
        on_error: Callable[[BaseException], None],
    ) -> None:
        self._root = root
        self._on_stage = on_stage
        self._on_log = on_log
        self._on_done = on_done
        self._on_error = on_error
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._poll_id: str | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, work: Callable[..., object]) -> None:
        """启动任务（重复调用会被忽略）。"""
        if self.is_running:
            return
        self._queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, args=(work,), daemon=True
        )
        self._thread.start()
        self._poll()

    def _worker(self, work: Callable[..., object]) -> None:
        def stage(key: str, message: str = "", ratio: float | None = None) -> None:
            self._queue.put(("stage", key, message, ratio))

        def log(message: str) -> None:
            self._queue.put(("log", message))

        try:
            with contextlib.redirect_stdout(_StdoutTee(log)):
                result = work(stage=stage, log=log)
        except BaseException as exc:  # noqa: BLE001 —— 统一交给主线程展示
            self._queue.put(("error", exc))
        else:
            self._queue.put(("done", result))

    def _poll(self) -> None:
        """主线程侧：排空队列后继续轮询。"""
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "stage":
                    self._on_stage(item[1], item[2], item[3])
                elif kind == "log":
                    self._on_log(item[1])
                elif kind == "done":
                    self._poll_id = None
                    self._on_done(item[1])
                    return
                elif kind == "error":
                    self._poll_id = None
                    self._on_error(item[1])
                    return
        except queue.Empty:
            pass
        if self.is_running or not self._queue.empty():
            self._poll_id = self._root.after(self.POLL_MS, self._poll)


# ============================================================
# UI 组件
# ============================================================


class DropZone(ttk.Frame):
    """投放区：支持拖入 + 点击选择双通道。

    ``tkinterdnd2`` 不可用时自动退化为纯点击选择，界面提示同步调整。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        hint: str,
        on_path: Callable[[str], None],
        want_directory: bool = False,
        file_types: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(master, padding=2)
        self._on_path = on_path
        self._want_directory = want_directory
        self._file_types = file_types or [("所有文件", "*.*")]
        self._path: str = ""
        self._drop = DND_AVAILABLE

        style = ttk.Style(self)
        style.configure(
            "Drop.TFrame", relief="groove", borderwidth=2, background="#ffffff"
        )
        outer = ttk.Frame(self, style="Drop.TFrame", padding=10)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer, text=title, font=(FONT_FAMILY, FONT_SIZE, "bold")
        ).pack(anchor="w")
        hint_text = hint if self._drop else f"{hint}（点击下方按钮选择）"
        self._path_label = ttk.Label(
            outer,
            text=hint_text,
            font=(FONT_FAMILY, FONT_SIZE),
            foreground=COLOR_MUTED,
            wraplength=330,
            justify="left",
        )
        self._path_label.pack(anchor="w", fill="x", pady=(4, 8))
        ttk.Button(
            outer, text="选择…", command=self._browse, width=12
        ).pack(anchor="w")

        if self._drop:
            self._bind_drop(self)

    def _bind_drop(self, widget: tk.Misc) -> None:
        """递归为 widget 及其所有子孙注册投放区。

        tkdnd 只把事件投递给**光标下最深**的那个已注册控件。若只注册外层
        Frame，光标落在中间的标签/按钮上时事件会被子控件吞掉而不冒泡，
        表现为「有的地方能拖、有的地方没反应」。这里整棵子树都注册，
        无论落在哪一块都能接住。
        """
        for target in self._walk(widget):
            with contextlib.suppress(Exception):
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<Drop>>", self._on_drop)
                target.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                target.dnd_bind("<<DragLeave>>", self._on_drag_leave)

    def _walk(self, widget: tk.Misc) -> Iterable[tk.Misc]:
        yield widget
        for child in widget.winfo_children():
            yield from self._walk(child)

    def _on_drag_enter(self, event) -> None:  # noqa: ANN001
        self._highlight(True)

    def _on_drag_leave(self, event) -> None:  # noqa: ANN001
        self._highlight(False)

    def _highlight(self, active: bool) -> None:
        color = COLOR_PRIMARY if active else "#ffffff"
        try:
            self.winfo_toplevel().tk.call(
                "ttk::style", "map", "Drop.TFrame",
                "-background", "active", color,
                "-background", "!active", "#ffffff",
            )
        except Exception:  # noqa: BLE001 —— 高亮是纯装饰，失败不影响功能
            pass

    @property
    def path(self) -> str:
        return self._path

    def set_path(self, value: str) -> None:
        self._path = value
        self._path_label.configure(
            text=str(value), foreground=COLOR_PRIMARY
        )

    def _browse(self) -> None:
        if self._want_directory:
            chosen = filedialog.askdirectory(title="选择文件夹")
        else:
            chosen = filedialog.askopenfilename(
                title="选择文件", filetypes=self._file_types
            )
        if chosen:
            self._on_path(chosen)

    def _on_drop(self, event) -> None:  # noqa: ANN001 - tkinter 事件对象
        # tkinter 对事件回调里的异常只在 stderr 打一行；本工具经 pythonw 启动
        # 没有控制台，任何异常都会表现为「拖进去完全没反应」。因此这里必须
        # 全部兜住并弹窗，绝不允许静默失败。
        try:
            self._handle_drop(event)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            messagebox.showerror(
                "拖拽处理失败",
                f"读取拖入内容时出错：\n\n{exc}\n\n请改用下方的「选择…」按钮。",
            )

    def _handle_drop(self, event) -> None:
        """多策略解析拖入数据，取第一个能命中真实路径的结果。

        为什么不能只信一种：tkdnd 的 ``%D`` 替换**没有**用大括号包裹，
        Tcl 层会把它当词列表处理——路径里有空格就断成两截，有反斜杠就
        被当转义符吃掉。于是同一份 data 可能被解析成
        ``['C:\\a b\\x.zip', 'D:\\y\\z.exe']``（正确）或
        ``['C:abx.zip', 'D:y\\z.exe']``（反斜杠被吃），
        而**只有磁盘上真实存在的那个才是对的**。

        所以这里按「splitlist → 正则 → 原文」三种策略依次尝试，
        并要求候选路径必须真实存在且类型匹配才采纳。
        任何一步失败都不静默——最后弹窗告诉用户改用「选择…」按钮。
        """
        raw = getattr(event, "data", None)
        candidates: list[str] = []

        # 策略 1：官方推荐的 splitlist
        try:
            candidates.extend(parse_drop_paths(self.tk.splitlist(raw)))
        except Exception:  # noqa: BLE001
            pass
        # 策略 2：花括号感知的正则（splitlist 不可用或结构被破坏时）
        candidates.extend(parse_drop_paths(raw))
        # 策略 3：整体当单个路径（Windows 上单文件无空格时最可靠）
        text = str(raw or "").strip().strip("{}").strip()
        if text:
            candidates.append(text)

        for candidate in candidates:
            picked = first_matching_path([candidate], self._want_directory)
            if picked is not None:
                self._on_path(picked)
                return

        if not candidates:
            messagebox.showwarning(
                "没识别到文件",
                "拖入的内容里没有可用的文件路径。\n\n请改用下方的「选择…」按钮。",
            )
        else:
            want = "文件夹" if self._want_directory else "文件"
            messagebox.showwarning(
                "拖拽没能用上",
                f"这里需要的是一个真实存在的{want}，但拖入的内容没能匹配上：\n\n"
                + "\n".join(candidates[:5])
                + "\n\n请改用下方的「选择…」按钮，效果完全一样。",
            )


class StageProgress(ttk.Frame):
    """阶段进度：步骤列表 + 当前步高亮 + 可选百分比。"""

    def __init__(self, master: tk.Misc, stages: list[tuple[str, str]]) -> None:
        super().__init__(master)
        self._stages = list(stages)
        self._rows: dict[str, tuple[ttk.Label, ttk.Label]] = {}
        for key, label in self._stages:
            row = ttk.Frame(self)
            row.pack(fill="x", pady=1)
            icon = ttk.Label(
                row, text=ICON_PENDING, font=(FONT_FAMILY, FONT_SIZE), width=2
            )
            icon.pack(side="left")
            text = ttk.Label(row, text=label, font=(FONT_FAMILY, FONT_SIZE))
            text.pack(side="left")
            self._rows[key] = (icon, text)
        self.reset()

    def reset(self) -> None:
        for key in self._rows:
            self._mark(key, "pending", None)

    def set_stage(
        self, key: str, message: str = "", ratio: float | None = None
    ) -> None:
        keys = [k for k, _ in self._stages]
        if key not in keys:
            return
        index = keys.index(key)
        for position, stage_key in enumerate(keys):
            if position < index:
                self._mark(stage_key, "done", None)
            elif position == index:
                self._mark(stage_key, "active", message or ratio)
            else:
                self._mark(stage_key, "pending", None)

    def mark_failed(self, key: str) -> None:
        self._mark(key, "failed", None)

    def _mark(self, key: str, state: str, detail: str | float | None) -> None:
        icon, text = self._rows[key]
        label = dict(self._stages)[key]
        percent = ""
        if isinstance(detail, float) and state == "active" and 0 <= detail <= 1:
            percent = f"（{round(detail * 100)}%）"
        elif isinstance(detail, str) and detail:
            percent = f"（{detail}）"
        text.configure(
            text=f"{label}{percent}",
            foreground={
                "pending": COLOR_MUTED,
                "active": COLOR_PRIMARY,
                "done": COLOR_SUCCESS,
                "failed": COLOR_ERROR,
            }[state],
            font=(
                FONT_FAMILY,
                FONT_SIZE,
                "bold" if state == "active" else "normal",
            ),
        )
        icon.configure(
            text={
                "pending": ICON_PENDING,
                "active": ICON_ACTIVE,
                "done": ICON_DONE,
                "failed": ICON_FAILED,
            }[state],
            foreground={
                "pending": COLOR_MUTED,
                "active": COLOR_PRIMARY,
                "done": COLOR_SUCCESS,
                "failed": COLOR_ERROR,
            }[state],
        )


class LogPanel(ttk.Frame):
    """实时日志面板（自动滚到底，超量滚动裁剪）。"""

    MAX_LINES = 5000

    def __init__(self, master: tk.Misc, height: int = 10) -> None:
        super().__init__(master)
        self._text = tk.Text(
            self,
            height=height,
            font=(MONO_FAMILY, MONO_SIZE),
            background=COLOR_LOG_BG,
            relief="solid",
            borderwidth=1,
            wrap="none",
            state="disabled",
        )
        scroll = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

    def append(self, line: str) -> None:
        self._text.configure(state="normal")
        self._text.insert("end", line + "\n")
        total = int(self._text.index("end-1c").split(".")[0])
        if total > self.MAX_LINES:
            self._text.delete("1.0", f"{total - self.MAX_LINES}.0")
        self._text.see("end")
        self._text.configure(state="disabled")

    def clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")


class ResultCard(ttk.Frame):
    """结果卡片：成功/失败 + 明细 + 快捷动作按钮。

    ``show(..., notify=True)`` 时额外弹一个模态提示框——**双保险**。
    小白用户经常在任务跑完后去看 Blender 而不是盯着窗口；卡片可能被滚动
    出视野，光靠卡片就又变成「好像没反应」。模态框能强制引起注意，
    关掉后卡片依然在下方可查。
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._body = ttk.Frame(self)

    def clear(self) -> None:
        for child in self._body.winfo_children():
            child.destroy()
        self._body.pack_forget()

    @property
    def visible(self) -> bool:
        return bool(self._body.winfo_ismapped())

    def reveal(self) -> None:
        """滚动祖先容器，让本卡片进入视野。"""
        widget = self
        while widget is not None:
            widget.update_idletasks()
            parent = widget.master
            if isinstance(parent, ScrollPane):
                parent.scroll_to(self)
                return
            widget = parent if isinstance(parent, ttk.Frame) else None

    def show(
        self,
        *,
        ok: bool,
        title: str,
        details: Iterable[tuple[str, str]] = (),
        message: str = "",
        actions: Iterable[tuple[str, Callable[[], None]]] = (),
        notify: bool = False,
    ) -> None:
        self.clear()
        self._body.configure(relief="solid", borderwidth=1, padding=12)
        self._body.pack(fill="x", pady=(8, 0))
        self.reveal()

        ttk.Label(
            self._body,
            text=("✅ " if ok else "❌ ") + title,
            font=(FONT_FAMILY, TITLE_SIZE, "bold"),
            foreground=COLOR_SUCCESS if ok else COLOR_ERROR,
        ).pack(anchor="w")
        if message:
            ttk.Label(
                self._body,
                text=message,
                font=(FONT_FAMILY, FONT_SIZE),
                foreground=COLOR_ERROR if not ok else COLOR_MUTED,
                wraplength=840,
                justify="left",
            ).pack(anchor="w", pady=(6, 0))
        for key, value in details:
            row = ttk.Frame(self._body)
            row.pack(fill="x", pady=(4, 0))
            ttk.Label(
                row,
                text=f"{key}：",
                font=(FONT_FAMILY, FONT_SIZE),
                foreground=COLOR_MUTED,
                width=14,
                anchor="w",
            ).pack(side="left")
            ttk.Label(
                row, text=value, font=(FONT_FAMILY, FONT_SIZE), wraplength=680,
                justify="left",
            ).pack(side="left", fill="x", expand=True)
        buttons = list(actions)
        if buttons:
            bar = ttk.Frame(self._body)
            bar.pack(anchor="w", pady=(10, 0))
            for label, command in buttons:
                ttk.Button(bar, text=label, command=command, width=16).pack(
                    side="left", padx=(0, 8)
                )

        if notify:
            self.root = self.winfo_toplevel()
            summary = "\n".join(f"{k}：{v}" for k, v in details) or message
            try:
                messagebox.showinfo(
                    title if ok else f"❌ {title}", summary or title, parent=self.root
                )
            except tk.TclError:  # pragma: no cover —— 无父窗口可用
                messagebox.showinfo(title if ok else f"❌ {title}", summary or title)


# ============================================================
# 辅助
# ============================================================


def open_in_explorer(path: str | Path) -> None:
    """在资源管理器中定位文件或打开目录。"""
    target = str(path)
    if not target or not os.path.exists(target):
        return
    if os.name != "nt":
        with contextlib.suppress(Exception):
            subprocess.Popen(["xdg-open", target])
        return
    if os.path.isdir(target):
        os.startfile(target)  # noqa: S606 - Windows 打开目录
    else:
        with contextlib.suppress(Exception):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])


def build_header(master: tk.Misc, title: str, subtitle: str) -> ttk.Frame:
    """统一的标题区。"""
    frame = ttk.Frame(master)
    ttk.Label(
        frame, text=title, font=(FONT_FAMILY, TITLE_SIZE, "bold")
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text=subtitle,
        font=(FONT_FAMILY, FONT_SIZE),
        foreground=COLOR_MUTED,
        wraplength=860,
        justify="left",
    ).pack(anchor="w", pady=(2, 0))
    return frame
