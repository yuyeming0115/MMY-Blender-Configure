#!/usr/bin/env python3
"""MMY 打包工具（图形版）。

把 Blender 的 portable/ 配置文件夹打包成一个 zip，可用于备份或同版本迁移。

与 pack.py 的关系：
  - 打包逻辑完全复用 pack.py，本文件只负责界面
  - pack.py 的 print 输出会被捕获到日志面板，打包过程可见

用法：
  双击 run_pack_gui.bat
  或 python mmy_pack_gui.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

import gui_kit as gk  # noqa: E402
import pack as core  # noqa: E402


STAGES: list[tuple[str, str]] = [
    ("scan", "扫描文件"),
    ("compress", "压缩写入 zip"),
]


class PackApp:
    """打包 GUI 主窗口。"""

    def __init__(self, root) -> None:
        self.root = root
        self.output_path: str = ""
        self._current_stage = "scan"

        self._build_ui()
        self.runner = gk.TaskRunner(
            root,
            on_stage=self._on_stage,
            on_log=self.log.append,
            on_done=self._on_done,
            on_error=self._on_error,
        )
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        outer = gk.ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        gk.build_header(
            outer,
            "MMY 打包工具",
            "把 Blender 的 portable 配置文件夹打包成 zip。"
            "适合备份，或在同版本机器之间直接拷贝。",
        ).pack(anchor="w", pady=(0, 12))

        # ① 选择文件夹
        files = gk.ttk.LabelFrame(outer, text=" ① 选择要打包的文件夹 ", padding=10)
        files.pack(fill="x")
        self.zone = gk.DropZone(
            files,
            title="Blender portable 文件夹",
            hint="把 portable 文件夹拖到这里",
            on_path=self._on_folder,
            want_directory=True,
        )
        self.zone.pack(fill="x")

        # ② 输出设置
        output = gk.ttk.LabelFrame(outer, text=" ② 输出设置 ", padding=10)
        output.pack(fill="x", pady=(10, 0))
        self.var_compress = gk.tk.BooleanVar(value=False)
        row = gk.ttk.Frame(output)
        row.pack(fill="x")
        gk.ttk.Checkbutton(
            row,
            text="压缩文件体积（更小，但慢很多）",
            variable=self.var_compress,
        ).pack(side="left")
        gk.ttk.Button(
            output, text="选择输出位置…", command=self._on_pick_output, width=18
        ).pack(anchor="w", pady=(8, 0))
        self.output_label = gk.ttk.Label(
            output,
            text="（打包时自动生成文件名）",
            font=(gk.FONT_FAMILY, gk.FONT_SIZE),
            foreground=gk.COLOR_MUTED,
            wraplength=840,
            justify="left",
        )
        self.output_label.pack(anchor="w", fill="x", pady=(6, 0))
        self.btn_start = gk.ttk.Button(
            output, text="开始打包", command=self._on_start, state="disabled", width=16
        )
        self.btn_start.pack(anchor="w", pady=(10, 0))

        # ③ 进度
        progress = gk.ttk.LabelFrame(outer, text=" ③ 进度 ", padding=10)
        progress.pack(fill="both", expand=True, pady=(10, 0))
        self.stages = gk.StageProgress(progress, STAGES)
        self.stages.pack(anchor="w", fill="x", pady=(0, 8))
        self.log = gk.LogPanel(progress)
        self.log.pack(fill="both", expand=True)

        # ④ 结果
        self.result = gk.ResultCard(outer)
        self.result.pack(fill="x")

    # ---------------------------------------------------------- 事件

    def _on_folder(self, path: str) -> None:
        self.zone.set_path(path)
        self.result.clear()
        self.stages.reset()
        self.btn_start.configure(state="normal")
        if not self.output_path:
            version = core.get_blender_version(path) or "unknown"
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            self._suggest_output(
                str(Path.home() / "Desktop" / f"MMY_Backup_Portable_v{version}_{stamp}.zip")
            )

    def _suggest_output(self, path: str) -> None:
        self.output_path = path
        self.output_label.configure(text=path, foreground=gk.COLOR_PRIMARY)

    def _on_pick_output(self) -> None:
        chosen = gk.filedialog.asksaveasfilename(
            title="保存 ZIP 文件",
            initialdir=str(Path.home() / "Desktop"),
            initialfile=Path(self.output_path).name if self.output_path else "backup.zip",
            defaultextension=".zip",
            filetypes=[("ZIP 文件", "*.zip")],
        )
        if chosen:
            self._suggest_output(chosen)

    def _on_start(self) -> None:
        if not self.zone.path:
            return
        output = self.output_path or str(
            Path.home() / "Desktop" / f"MMY_Backup_{datetime.now():%Y%m%d_%H%M}.zip"
        )
        self._suggest_output(core.get_unique_output_path(output))
        self.result.clear()
        self.stages.reset()
        self.log.append("── 开始打包 ──")

        def work(stage, log):
            return core.pack_portable(
                self.zone.path,
                self.output_path,
                compress=bool(self.var_compress.get()),
                progress=stage,
            )

        self.runner.start(work)

    # ---------------------------------------------------------- 任务回调

    def _on_stage(self, key: str, message: str, ratio: float | None) -> None:
        self._current_stage = key
        self.stages.set_stage(key, message or "", ratio)

    def _on_done(self, result) -> None:
        path = Path(result)
        size_mb = path.stat().st_size / 1024 / 1024
        self.stages.set_stage("compress", "")
        self.result.show(
            ok=True,
            title="打包完成",
            details=[
                ("输出文件", str(path)),
                ("文件大小", f"{size_mb:.1f} MB"),
                (
                    "压缩模式",
                    "已压缩（体积小）" if self.var_compress.get() else "未压缩（速度快）",
                ),
            ],
            actions=[
                ("打开所在文件夹", lambda: gk.open_in_explorer(path)),
                ("重新打包", self._restart),
            ],
        )

    def _on_error(self, exc: BaseException) -> None:
        self.stages.mark_failed(self._current_stage)
        self.btn_start.configure(state="normal")
        self.result.show(ok=False, title="打包失败", message=gk.explain_error(exc))

    def _restart(self) -> None:
        self.result.clear()
        self.stages.reset()
        self.log.clear()

    def _on_close(self) -> None:
        if self.runner.is_running:
            if not gk.messagebox.askyesno(
                "打包进行中", "打包还没结束，确定要关闭吗？"
            ):
                return
        self.root.destroy()


def main() -> int:
    root = gk.make_root("MMY 打包工具 — 备份 Blender 配置")
    PackApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 —— 任何未预期异常都要可见
        import traceback

        detail = traceback.format_exc()
        try:
            gk.messagebox.showerror("打包工具启动失败", f"{exc}\n\n{detail}")
        except Exception:  # noqa: BLE001 —— 连 tk 都不可用时退回控制台
            print(detail, file=sys.stderr)
        sys.exit(1)
