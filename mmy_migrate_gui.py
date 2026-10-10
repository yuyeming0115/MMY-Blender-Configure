#!/usr/bin/env python3
"""MMY 迁移工具（图形版）。

把插件「一键导出」产生的迁移配置包应用到任意目标 Blender——
目标 Blender **不需要安装本插件**，适用于全新便携版 / 新机器。

与 migrate.py（命令行版）的关系：
  - 校验、预检、执行逻辑全部复用 migrate.py，本文件只负责界面
  - 核心执行链是纯 Python，通过 blender.exe --background 注入审计脚本

用法：
  双击 run_migrate_gui.bat
  或 python mmy_migrate_gui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

import gui_kit as gk  # noqa: E402
import migrate as cli  # noqa: E402


STAGES: list[tuple[str, str]] = [
    ("parse", "读取配置包"),
    ("probe", "检测目标 Blender"),
    ("backup", "备份目标现有配置"),
    ("install", "安装配置"),
    ("audit", "目标审计（启动 Blender 校验，最耗时）"),
    ("report", "生成报告"),
]


class MigrateApp:
    """迁移 GUI 主窗口。"""

    def __init__(self, root) -> None:
        self.root = root
        self.manifest: dict | None = None
        self.preflight: dict | None = None
        self._file_busy = False  # 预检进行中（此时忽略新的文件变更）
        self._current_stage = "parse"

        self._build_ui()
        self.runner = gk.TaskRunner(
            root,
            on_stage=self._on_stage,
            on_log=self.log.append,
            on_done=self._on_task_done,
            on_error=self._on_task_error,
        )
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        outer = gk.ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        gk.build_header(
            outer,
            "MMY 迁移工具",
            "把旧版 Blender 的配置（插件、偏好、工作区）迁移到新的 Blender。"
            "目标机器无需安装本插件。",
        ).pack(anchor="w", pady=(0, 12))

        # ① 选择文件
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

        # ② 确认信息
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

    # ---------------------------------------------------------- 任务回调

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
        text = cli.describe_profile(
            self.manifest, self.zone_exe.path, None, self.preflight
        )
        self.summary.configure(text=text, foreground=gk.COLOR_PRIMARY)
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
        actions = []
        report = info.get("report_html")
        if report:
            actions.append(("打开报告", lambda: gk.open_in_explorer(report)))
        user_root = info.get("user_root")
        if user_root:
            actions.append(("打开配置目录", lambda: gk.open_in_explorer(user_root)))
        actions.append(("重新开始", self._restart))
        self.result.show(
            ok=not degraded,
            title="导入成功" if not degraded else "导入完成（有降级项，请查看报告）",
            details=details,
            actions=actions,
        )
        self.stages.set_stage("report", "")

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
        )

    def _restart(self) -> None:
        self.result.clear()
        self.stages.reset()
        self.log.clear()
        self.btn_start.configure(state="disabled" if self.preflight is None else "normal")

    def _on_close(self) -> None:
        if self.runner.is_running:
            if not gk.messagebox.askyesno(
                "任务进行中",
                "迁移/检测还在运行。\n中途关闭可能导致目标配置处于不确定状态，"
                "确定要关闭吗？",
            ):
                return
        self.root.destroy()


def main() -> int:
    root = gk.make_root("MMY 迁移工具 — 把配置迁到新的 Blender")
    MigrateApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 —— 任何未预期异常都要可见
        import traceback

        cli._show_fatal_error(
            f"迁移工具启动失败：\n\n{exc}\n\n详细信息：\n\n{traceback.format_exc()}"
        )
        sys.exit(1)
