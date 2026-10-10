#!/usr/bin/env python3
"""MMY Blender 配置工具 —— 迁移与打包合一。

单窗口双标签页：

- **配置迁移**：把旧版 Blender 的配置（插件、偏好、工作区）迁到新的
  Blender，目标机器无需安装本插件。
- **配置打包**：把 Blender 的 portable/ 配置文件夹打成 zip，用于备份
  或同版本整体拷贝。

两侧的业务逻辑分别复用 ``migrate.py`` 与 ``pack.py``（它们也可独立作为
命令行工具使用）；UI 组件在 ``gui_kit.py``，纯逻辑在 ``gui_logic.py``。

用法：
  双击 run_mmy_tool.bat
  或 python mmy_tool_gui.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

import gui_kit as gk  # noqa: E402
import migrate as cli  # noqa: E402
import pack as packer  # noqa: E402


MIGRATE_STAGES: list[tuple[str, str]] = [
    ("parse", "读取配置包"),
    ("probe", "检测目标 Blender"),
    ("backup", "备份目标现有配置"),
    ("install", "安装配置"),
    ("audit", "目标审计（启动 Blender 校验，最耗时）"),
    ("report", "生成报告"),
]

PACK_STAGES: list[tuple[str, str]] = [
    ("scan", "扫描文件"),
    ("compress", "压缩写入 zip"),
]


# ============================================================
# 标签页 1：配置迁移
# ============================================================


class MigratePage:
    """迁移标签页。"""

    def __init__(self, parent, root, scroll: gk.ScrollPane) -> None:
        self.root = root
        self.scroll = scroll
        self.outer = parent
        self.manifest: dict | None = None
        self.preflight: dict | None = None
        self._file_busy = False
        self._current_stage = "parse"

        self._build_ui()
        self.runner = gk.TaskRunner(
            root,
            on_stage=self._on_stage,
            on_log=self.log.append,
            on_done=self._on_task_done,
            on_error=self._on_task_error,
        )

    def _build_ui(self) -> None:
        outer = self.outer

        files = gk.ttk.LabelFrame(outer, text=" ① 选择文件 ", padding=10)
        files.pack(fill="x")
        row = gk.ttk.Frame(files)
        row.pack(fill="x")
        self.zone_profile = gk.DropZone(
            row,
            title="迁移配置包（*.zip）",
            hint="把配置包拖到这里",
            on_path=self._on_profile,
            file_types=[("迁移配置包", "*.zip"), ("所有文件", "*.*")],
        )
        self.zone_profile.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.zone_exe = gk.DropZone(
            row,
            title="目标 Blender（blender.exe）",
            hint="把 blender.exe 拖到这里",
            on_path=self._on_exe,
            file_types=[("Blender 可执行文件", "blender.exe"), ("所有文件", "*.*")],
        )
        self.zone_exe.pack(side="left", fill="both", expand=True)

        confirm = gk.ttk.LabelFrame(outer, text=" ② 确认信息 ", padding=10)
        confirm.pack(fill="x", pady=(10, 0))
        self.summary = gk.ttk.Label(
            confirm,
            text="请先选择配置包与目标 Blender（选齐后会自动检测）。",
            font=(gk.FONT_FAMILY, gk.FONT_SIZE),
            foreground=gk.COLOR_MUTED,
            justify="left",
            wraplength=860,
        )
        self.summary.pack(anchor="w", fill="x")
        self.btn_start = gk.ttk.Button(
            confirm, text="开始导入", command=self._on_start, state="disabled", width=16
        )
        self.btn_start.pack(anchor="w", pady=(10, 0))

        progress = gk.ttk.LabelFrame(outer, text=" ③ 进度 ", padding=10)
        progress.pack(fill="both", expand=True, pady=(10, 0))
        self.stages = gk.StageProgress(progress, MIGRATE_STAGES)
        self.stages.pack(anchor="w", fill="x", pady=(0, 8))
        self.log = gk.LogPanel(progress, height=8)
        self.log.pack(fill="both", expand=True)

        self.result = gk.ResultCard(outer)
        self.result.pack(fill="x")

    # ---------------------------------------------------------- 事件

    def _on_profile(self, path: str) -> None:
        self.zone_profile.set_path(path)
        self._after_file_change()

    def _on_exe(self, path: str) -> None:
        self.zone_exe.set_path(path)
        self._after_file_change()

    def _after_file_change(self) -> None:
        self.preflight = None
        self.manifest = None
        self.result.clear()
        self.stages.reset()
        self.btn_start.configure(state="disabled")
        if not (self.zone_profile.path and self.zone_exe.path):
            return
        if self._file_busy or self.runner.is_running:
            return
        self._start_preflight()

    def _start_preflight(self) -> None:
        self._file_busy = True
        self.summary.configure(text="正在检测目标 Blender…", foreground=gk.COLOR_MUTED)
        self.log.append("── 预检 ──")

        def work(stage, log):
            stage("parse", "读取配置包")
            manifest = cli.validate_profile(self.zone_profile.path)
            log(f"配置包校验通过：{len(manifest.get('files', []))} 个文件")
            stage("probe", "启动目标 Blender 探针…")
            info = cli.preflight_target(manifest, self.zone_exe.path)
            return {"manifest": manifest, "preflight": info}

        self.runner.start(work)

    def _on_start(self) -> None:
        if not (self.zone_profile.path and self.zone_exe.path) or self.preflight is None:
            return
        self.result.clear()
        self.stages.reset()
        self.log.append("── 开始迁移 ──")

        def work(stage, log):
            return cli.run_migration(
                self.zone_profile.path,
                self.zone_exe.path,
                self.preflight,
                progress=stage,
            )

        self.runner.start(work)

    # ---------------------------------------------------------- 回调

    def _on_stage(self, key: str, message: str, ratio: float | None) -> None:
        self._current_stage = key
        self.stages.set_stage(key, message or "", ratio)

    def _on_task_done(self, result) -> None:
        self._file_busy = False
        if isinstance(result, dict) and "preflight" in result:
            self._on_preflight_done(result)
        else:
            self._on_migration_done(result)

    def _on_preflight_done(self, result: dict) -> None:
        self.manifest = result["manifest"]
        self.preflight = result["preflight"]
        self.summary.configure(
            text=cli.describe_profile(
                self.manifest, self.zone_exe.path, None, self.preflight
            ),
            foreground=gk.COLOR_PRIMARY,
        )
        self.btn_start.configure(state="normal")
        self.stages.reset()

    def _on_migration_done(self, info: dict) -> None:
        degraded = info.get("status") != "success"
        details = [
            ("目标版本", f"Blender {info.get('target_version', '?')}"),
            ("配置写入", info.get("user_root", "")),
            (
                "恢复工作区",
                "、".join(info.get("workspaces_restored") or []) or "无（新版已自带）",
            ),
            ("快捷键丢失", f"{info.get('keymap_lost_count', 0)} 条"),
        ]
        missing = cli.missing_folder_components(info.get("manifest") or {})
        if missing:
            details.append(("未包含", "、".join(missing)))

        actions = []
        report = info.get("report_html")
        if report:
            actions.append(("打开报告", lambda: gk.open_in_explorer(report)))
        user_root = info.get("user_root")
        if user_root:
            actions.append(("打开配置目录", lambda: gk.open_in_explorer(user_root)))
        actions.append(("重新开始", self._restart))
        self.stages.set_stage("report", "")
        self.result.show(
            ok=not degraded,
            title="导入成功" if not degraded else "导入完成（有降级项，请查看报告）",
            details=details,
            actions=actions,
            notify=True,
        )

    def _on_task_error(self, exc: BaseException) -> None:
        self._file_busy = False
        self.stages.mark_failed(self._current_stage)
        self.btn_start.configure(
            state="normal" if self.preflight else "disabled"
        )
        self.result.show(
            ok=False,
            title="操作失败",
            message=gk.explain_error(exc),
            notify=True,
        )

    def _restart(self) -> None:
        self.result.clear()
        self.stages.reset()
        self.log.clear()
        self.btn_start.configure(state="disabled" if self.preflight is None else "normal")

    def is_running(self) -> bool:
        return self.runner.is_running

    def confirm_close(self) -> bool:
        if not self.runner.is_running:
            return True
        return gk.messagebox.askyesno(
            "任务进行中",
            "迁移/检测还在运行。\n中途关闭可能导致目标配置处于不确定状态，"
            "确定要关闭吗？",
        )


# ============================================================
# 标签页 2：配置打包
# ============================================================


class PackPage:
    """打包标签页。"""

    def __init__(self, parent, root) -> None:
        self.root = root
        self.outer = parent
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

    def _build_ui(self) -> None:
        outer = self.outer

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

        progress = gk.ttk.LabelFrame(outer, text=" ③ 进度 ", padding=10)
        progress.pack(fill="both", expand=True, pady=(10, 0))
        self.stages = gk.StageProgress(progress, PACK_STAGES)
        self.stages.pack(anchor="w", fill="x", pady=(0, 8))
        self.log = gk.LogPanel(progress, height=8)
        self.log.pack(fill="both", expand=True)

        self.result = gk.ResultCard(outer)
        self.result.pack(fill="x")

    # ---------------------------------------------------------- 事件

    def _on_folder(self, path: str) -> None:
        self.zone.set_path(path)
        self.result.clear()
        self.stages.reset()
        self.btn_start.configure(state="normal")
        if not self.output_path:
            version = packer.get_blender_version(path) or "unknown"
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
        self._suggest_output(packer.get_unique_output_path(output))
        self.result.clear()
        self.stages.reset()
        self.log.append("── 开始打包 ──")

        def work(stage, log):
            return packer.pack_portable(
                self.zone.path,
                self.output_path,
                compress=bool(self.var_compress.get()),
                progress=stage,
            )

        self.runner.start(work)

    # ---------------------------------------------------------- 回调

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
            notify=True,
        )

    def _on_error(self, exc: BaseException) -> None:
        self.stages.mark_failed(self._current_stage)
        self.btn_start.configure(state="normal")
        self.result.show(
            ok=False, title="打包失败", message=gk.explain_error(exc), notify=True
        )

    def _restart(self) -> None:
        self.result.clear()
        self.stages.reset()
        self.log.clear()

    def is_running(self) -> bool:
        return self.runner.is_running

    def confirm_close(self) -> bool:
        if not self.runner.is_running:
            return True
        return gk.messagebox.askyesno(
            "打包进行中", "打包还没结束，确定要关闭吗？"
        )


# ============================================================
# 主窗口
# ============================================================


class ToolApp:
    """双标签页主窗口。每个标签页自带独立滚动容器与任务线程。"""

    def __init__(self, root) -> None:
        self.root = root
        header = gk.ttk.Frame(root, padding=(14, 12, 14, 0))
        header.pack(fill="x")
        gk.build_header(
            header,
            "MMY Blender 配置工具",
            "迁移：把旧版配置迁到新的 Blender（目标机器无需装插件）。"
            "打包：把 portable 配置文件夹打成 zip，用于备份或同版本拷贝。",
        )

        self.notebook = gk.ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 10))

        self.migrate_scroll = gk.ScrollPane(self.notebook)
        self.pack_scroll = gk.ScrollPane(self.notebook)
        self.notebook.add(self.migrate_scroll, text="  配置迁移  ")
        self.notebook.add(self.pack_scroll, text="  配置打包  ")

        self.migrate_page = MigratePage(self.migrate_scroll.body, root, self.migrate_scroll)
        self.pack_page = PackPage(self.pack_scroll.body, root)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_switch, add="bind")

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_tab_switch(self, _event=None) -> None:
        """切标签时刷新滚动区域尺寸，否则新页面的滚动条范围是算错的。"""
        self.notebook.update_idletasks()
        current = self.notebook.select()
        if current == str(self.migrate_scroll):
            self.migrate_scroll.body.update_idletasks()
        elif current == str(self.pack_scroll):
            self.pack_scroll.body.update_idletasks()

    def _on_close(self) -> None:
        for page in (self.migrate_page, self.pack_page):
            if not page.confirm_close():
                return
        self.root.destroy()


def main() -> int:
    root = gk.make_root("MMY Blender 配置工具 — 迁移 / 打包")
    ToolApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 —— 任何未预期异常都要可见
        import traceback

        detail = traceback.format_exc()
        try:
            gk.messagebox.showerror("MMY 配置工具启动失败", f"{exc}\n\n{detail}")
        except Exception:  # noqa: BLE001 —— 连 tk 都不可用时退回控制台
            print(detail, file=sys.stderr)
        sys.exit(1)