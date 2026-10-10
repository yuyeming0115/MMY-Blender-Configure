#!/usr/bin/env python3
"""
MMY Blender Configure — 独立迁移工具（免插件安装）

把插件"一键导出"产生的迁移配置包应用到任意目标 Blender——
目标 Blender **不需要安装本插件**，适用于全新便携版 / 新机器。

用法：
  python migrate.py                              # 图形模式（tkinter 双选框）
  python migrate.py <profile.zip> <blender.exe>  # 命令行模式

原理：迁移执行链（migration_core）是纯 Python，通过
blender.exe --background --python migration_worker.py 注入审计脚本，
不依赖插件注册成功。本脚本只是这条链路的独立触发器。
"""

import json
import os
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ADDON_DIR = REPO_ROOT / "mmy_pack_config"
WORKER_SCRIPT = ADDON_DIR / "migration_worker.py"

# 与 migration_core.SCHEMA_VERSION 对齐的配置包格式版本
PROFILE_SCHEMA_VERSION = 2


class MigrateInputError(Exception):
    """配置包输入校验失败（面向用户的中文错误）。"""


def load_migration_core():
    """加载纯 Python 迁移核心（不 import bpy，无需 Blender 环境）。"""
    if not WORKER_SCRIPT.is_file():
        raise MigrateInputError(
            f"缺少迁移执行文件：{WORKER_SCRIPT}\n"
            "请从完整仓库运行 migrate.py（mmy_pack_config/ 必须随脚本一起）。"
        )
    sys.path.insert(0, str(ADDON_DIR))
    import migration_core  # noqa: PLC0415

    return migration_core


# ============================================================
# 输入校验与摘要（纯函数，可单测）
# ============================================================


def validate_profile(profile_path) -> dict:
    """校验迁移配置包，返回 manifest。失败抛 MigrateInputError。"""
    path = Path(profile_path)
    if not path.is_file():
        raise MigrateInputError(f"配置包不存在：{path}")
    if path.suffix.lower() != ".zip":
        raise MigrateInputError(f"配置包必须是 .zip 文件：{path.name}")

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names:
                raise MigrateInputError(
                    f"{path.name} 中没有 manifest.json，不是插件导出的迁移配置包。"
                )
            try:
                manifest = json.loads(
                    zf.read("manifest.json").decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MigrateInputError(f"manifest.json 解析失败：{exc}")
    except zipfile.BadZipFile:
        raise MigrateInputError(f"{path.name} 不是有效的 ZIP 文件。")

    schema = manifest.get("schema_version")
    if schema != PROFILE_SCHEMA_VERSION:
        if schema == 1 and manifest.get("type") == "portable":
            raise MigrateInputError(
                f"{path.name} 是 pack.py 生成的便携版整目录备份，不是迁移配置包。\n"
                "整目录备份请直接解压覆盖 portable/ 文件夹（适合同版本）；\n"
                "跨版本迁移请先在源 Blender 中用插件「一键导出」生成配置包。"
            )
        raise MigrateInputError(
            f"不支持的配置包格式（schema_version={schema!r}），"
            f"本工具需要 schema_version={PROFILE_SCHEMA_VERSION}。"
        )

    source = manifest.get("source") or {}
    if not source.get("blender_version"):
        raise MigrateInputError("配置包缺少 source.blender_version 字段。")
    if source.get("platform") != "win32":
        raise MigrateInputError("跨版本迁移第一版只接受 Windows 配置快照。")
    return manifest


def version_string(version) -> str:
    """[5, 1, 0] -> '5.1'（末位为 0 时省略，与插件内展示一致）。"""
    parts = [int(p) for p in list(version or [])[:3]]
    parts.extend([0] * (3 - len(parts)))
    if parts[2] == 0:
        return f"{parts[0]}.{parts[1]}"
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def detect_target_version(executable_path) -> str | None:
    """从 blender.exe 上级目录名猜测版本（如 Blender5.1 -> 5.1）。

    仅用于确认摘要展示；真实目标版本以迁移时的目标探针为准。
    """
    parent_name = Path(executable_path).parent.name
    match = re.search(r"(\d+\.\d+(\.\d+)?)", parent_name)
    return match.group(1) if match else None


def describe_profile(manifest: dict, target_exe, target_version: str | None) -> str:
    """生成确认弹窗用的人类可读摘要。"""
    source = manifest.get("source") or {}
    workspaces = manifest.get("workspaces") or {}
    addons = manifest.get("addons") or []
    enabled_addons = [a for a in addons if isinstance(a, dict) and a.get("enabled")]

    lines = [
        "请确认迁移信息：",
        "",
        f"  配置包版本：Blender {version_string(source.get('blender_version'))}",
        f"  目标 Blender：{target_exe}",
    ]
    if target_version:
        lines.append(f"  目标版本（按目录名推测）：{target_version}")
    lines.append(f"  已启用插件：{len(enabled_addons)} 个（不兼容的将在导入时自动禁用）")
    if workspaces:
        lines.append(
            f"  自定义工作区：{workspaces.get('custom_count', 0)} 个"
            f"（跨次版本时将追加到新版默认工作区）"
        )
    lines += [
        "",
        "目标 Blender 的现有配置将备份，可随时回滚。",
        "⚠ 请确认目标不是配置包的来源 Blender（本工具无法自动识别）。",
    ]
    return "\n".join(lines)


# ============================================================
# 迁移执行
# ============================================================


def run_migration(profile_path, executable_path) -> dict:
    """执行迁移，返回 {outcome, report_html, recovery_dir} 供展示。"""
    core = load_migration_core()
    manifest = validate_profile(profile_path)
    profile = Path(profile_path)
    target = Path(executable_path)

    if not target.is_file():
        raise MigrateInputError(f"目标 Blender 不存在：{target}")
    if target.name.lower() != "blender.exe":
        print(f"[MMY] [WARN] 目标文件名不是 blender.exe：{target.name}（继续执行）")

    _, outcomes = core.execute_existing_profile_migration_multi(
        profile_path=profile,
        target_executables=[target],
        output_dir=profile.parent,  # recovery/报告放配置包旁边，天然在目标用户目录之外
        current_user_root=[],  # CLI 无源用户目录；确认弹窗已提示勿对来源 Blender 导入
        worker_script=WORKER_SCRIPT,
        current_pid=os.getpid(),
    )

    outcome = outcomes[0]
    if outcome.result is None:
        message = outcome.error or "迁移失败"
        if outcome.exception is not None:
            raise outcome.exception
        raise RuntimeError(message)

    result = outcome.result
    return {
        "target_version": version_string(result.target_version),
        "status": result.status,
        "recovery_dir": str(result.recovery_dir),
        "report_html": str(result.report_html_path) if result.report_html_path else "",
        "workspaces_restored": result.workspaces_restored,
        "keymap_lost_count": result.keymap_lost_count,
        "manifest": manifest,
    }


def format_result_summary(info: dict) -> str:
    lines = [
        "迁移完成！",
        "",
        f"  目标版本：Blender {info['target_version']}",
        f"  状态：{'成功' if info['status'] == 'success' else '完成（有降级警告，详见报告）'}",
    ]
    if info.get("workspaces_restored"):
        lines.append(
            f"  已恢复自定义工作区：{len(info['workspaces_restored'])} 个"
            f"（{', '.join(info['workspaces_restored'])}）"
        )
    if info.get("keymap_lost_count"):
        lines.append(f"  快捷键丢失：{info['keymap_lost_count']} 条（详见报告）")
    lines += [
        "",
        f"回滚备份：{info['recovery_dir']}",
    ]
    if info.get("report_html"):
        lines.append(f"审计报告：{info['report_html']}")
    lines += [
        "",
        "直接启动目标 Blender 即可使用迁移后的配置。",
    ]
    return "\n".join(lines)


# ============================================================
# 图形模式（tkinter）
# ============================================================


def run_tkinter_ui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print("[MMY] [ERROR] 当前环境无 tkinter，请使用命令行模式：")
        print("       python migrate.py <profile.zip> <blender.exe>")
        return 1

    root = tk.Tk()
    root.withdraw()

    profile_path = filedialog.askopenfilename(
        title="选择迁移配置包（插件「一键导出」生成的 .zip）",
        filetypes=[("迁移配置包", "*.zip"), ("所有文件", "*.*")],
    )
    if not profile_path:
        return 0

    try:
        manifest = validate_profile(profile_path)
    except MigrateInputError as exc:
        messagebox.showerror("配置包无效", str(exc))
        return 1

    executable_path = filedialog.askopenfilename(
        title="选择目标 Blender 可执行文件（blender.exe）",
        filetypes=[("Blender 可执行文件", "blender.exe"), ("所有文件", "*.*")],
    )
    if not executable_path:
        return 0

    summary = describe_profile(
        manifest, executable_path, detect_target_version(executable_path)
    )
    if not messagebox.askyesno("确认迁移", summary):
        return 0

    root.update()  # 关闭弹窗后保持事件循环活跃，避免后台进程期间窗口僵死提示
    try:
        info = run_migration(profile_path, executable_path)
    except Exception as exc:  # noqa: BLE001 —— 错误上屏，统一出口
        messagebox.showerror("迁移失败", str(exc))
        return 1

    messagebox.showinfo("迁移完成", format_result_summary(info))
    return 0


# ============================================================
# 主函数
# ============================================================


def main() -> int:
    print("=" * 60)
    print("MMY Blender Configure — 独立迁移工具（免插件安装）")
    print("=" * 60)
    print()

    if len(sys.argv) == 1:
        return run_tkinter_ui()

    if len(sys.argv) != 3:
        print("用法：")
        print("  python migrate.py                              # 图形模式")
        print("  python migrate.py <profile.zip> <blender.exe>  # 命令行模式")
        return 2

    profile_path, executable_path = sys.argv[1], sys.argv[2]
    try:
        manifest = validate_profile(profile_path)
        print(describe_profile(manifest, executable_path,
                               detect_target_version(executable_path)))
        print()
        info = run_migration(profile_path, executable_path)
    except MigrateInputError as exc:
        print(f"[MMY] [ERROR] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[MMY] [ERROR] 迁移失败：{exc}")
        import traceback

        traceback.print_exc()
        return 1

    print(format_result_summary(info))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[MMY] 已取消。")
        sys.exit(0)
