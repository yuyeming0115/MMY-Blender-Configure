"""在源 Blender 中运行的后台快照脚本，产出跨版本迁移配置包。

通过 ``blender --background --python export_worker.py -- <参数>`` 执行，
**不依赖本插件注册成功**——这正是它存在的意义：GUI 只需拖入 portable
文件夹 + 源 blender.exe 就能出迁移包，用户不必先在旧版 Blender 里装插件。

参数（``--`` 之后，顺序固定）::

    <user_root>        期望的源用户目录（portable/），用于校验拖对了 Blender
    <output_dir>       迁移包输出目录
    <report_json>      报告输出路径（成功/失败都写，供 GUI 读取）
    <options_json>     组件开关 JSON：{"datafiles": true, ...}

与 ``migration_worker.py``（目标端审计）对称：那边校验「目标 user_root 与
预期一致」，这边校验「源 user_root 与拖入的 portable 一致」，都是防止
用户拖错 Blender 却拿到一份来源不明的包。

⚠️ 已知限制：**快捷键指纹在后台模式下采不到**。Blender 后台不物化默认
键位（实测 5.1 后台 ``keyconfigs.default`` 仅 12 条，GUI 模式数千条），
差异项无法与默认表比对，``collect_keymap_fingerprint`` 返回 skipped。
快捷键本体仍然完整导出到 ``fallback/keymap.py``（实测 11978 字节），
目标端导入时会用；只是**「快捷键丢失审计」这项降级为不比对**。
需要完整审计时仍在源 Blender 里点插件的「一键导出」。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402

import collectors  # noqa: E402
from migration_core import (  # noqa: E402
    MigrationError,
    SourceSnapshot,
    create_profile,
    ensure_external_output,
)


_REPORT_MARKER = "MMY_EXPORT_REPORT:"
_OPTION_KEYS = (
    "presets",
    "datafiles",
    "startup_scripts",
    "app_templates",
    "history",
)


def _script_arguments() -> tuple[str, str, str, dict]:
    if "--" not in sys.argv:
        raise RuntimeError("缺少快照导出参数")
    args = sys.argv[sys.argv.index("--") + 1 :]
    if len(args) != 4:
        raise RuntimeError(f"快照导出参数数量错误：{len(args)}（应为 4）")
    user_root, output_dir, report_json, options_json = args
    try:
        options = json.loads(options_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"组件开关参数不是合法 JSON：{exc}") from exc
    if not isinstance(options, dict):
        raise RuntimeError("组件开关参数必须是 JSON 对象")
    return user_root, output_dir, report_json, options


def _write_report(path: Path, data: dict) -> None:
    """原子写报告。GUI 靠这个文件判断成败，务必保证写成功。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _resolve_user_root(expected: str) -> Path:
    """校验 Blender 实际使用的用户目录与传入的一致。

    不一致意味着用户拖入的 blender.exe 和 portable 文件夹不属于同一套
    Blender——这在「桌面上有好几个 Blender」时很容易发生。与其产出一份
    来源不明的包，不如直接报错让用户重新拖。
    """
    actual = Path(bpy.utils.resource_path("USER")).resolve(strict=False)
    wanted = Path(expected).resolve(strict=False)
    if os.path.normcase(str(actual)) != os.path.normcase(str(wanted)):
        raise MigrationError(
            f"拖入的 Blender 与 portable 文件夹不匹配：\n"
            f"  期望用户目录：{wanted}\n"
            f"  该 Blender 实际使用：{actual}\n\n"
            f"请改用这个 Blender 同级的 portable 文件夹"
            f"（即 {actual.parent}\\portable）。"
        )
    return actual


def run() -> dict:
    expected_root, output_dir, report_json, options = _script_arguments()
    report_path = Path(report_json)

    user_root = _resolve_user_root(expected_root)
    # 输出目录必须在用户配置目录之外——把备份写进自己的源目录会在下一次
    # 导出时把自己打进包里，且覆盖式安装时可能自毁。
    output = ensure_external_output(Path(output_dir), [user_root])
    work_dir = Path(tempfile.mkdtemp(prefix="mmy_export_", dir=str(output)))

    options = {key: bool(options.get(key, True)) for key in _OPTION_KEYS}
    warnings: list[str] = []
    notes: list[str] = []

    keymap_export = work_dir / "keymap.py"
    keymap_fingerprint = work_dir / "keymap_fingerprint.json"
    workspaces_inventory = work_dir / "workspaces.json"

    try:
        # 保存偏好：确保磁盘上的 userpref.blend 是最新状态。
        # 不做这步会拿到「用户上次点保存时」的快照，而非当前实际配置。
        save_result = bpy.ops.wm.save_userpref()
        if "FINISHED" not in save_result:
            raise MigrationError("源 Blender 无法保存偏好设置（userpref.blend）")

        export_result = bpy.ops.preferences.keyconfig_export(
            filepath=str(keymap_export), all=False
        )
        if "FINISHED" not in export_result or not keymap_export.is_file():
            raise MigrationError("源 Blender 快捷键导出失败")
        notes.append(f"快捷键已导出（{keymap_export.stat().st_size} 字节）")

        fingerprint = collectors.collect_keymap_fingerprint()
        keymap_fingerprint.write_text(
            json.dumps(fingerprint, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if fingerprint.get("skipped"):
            # 如实告知用户，而不是静默降级——用户需要知道这份包少了什么能力
            warnings.append(
                "后台模式无法物化默认键位，本次未采集快捷键差异指纹，"
                "导入时不会做「快捷键丢失」校验（快捷键本体仍已完整导入）"
            )
        else:
            notes.append(f"快捷键差异指纹：{len(fingerprint['items'])} 条")

        inventory = collectors.collect_workspace_inventory()
        workspaces_inventory.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        custom = [item["name"] for item in inventory.get("items", []) if not item.get("factory")]
        notes.append(f"工作区 {len(inventory.get('items', []))} 个（自定义 {len(custom)}）")

        addons = collectors.collect_addon_inventory()
        enabled_count = sum(1 for entry in addons if entry.get("enabled"))
        notes.append(f"插件 {enabled_count} 个已启用 / 共 {len(addons)} 个")

        snapshot = SourceSnapshot(
            version=tuple(bpy.app.version[:3]),
            platform=sys.platform,
            install_mode="portable" if collectors.is_portable_mode() else "normal",
            binary_path=Path(getattr(bpy.app, "binary_path", "") or sys.executable),
            user_root=user_root,
            config_dir=collectors.user_resource_path("CONFIG", user_root, "config"),
            scripts_dir=collectors.user_resource_path("SCRIPTS", user_root, "scripts"),
            datafiles_dir=collectors.user_resource_path("DATAFILES", user_root, "datafiles"),
            extensions_dir=collectors.user_resource_path(
                "EXTENSIONS", user_root, "extensions"
            ),
            keymap_export_path=keymap_export,
            keymap_fingerprint_path=keymap_fingerprint,
            keymap_item_count=len(fingerprint.get("items", [])),
            workspaces_inventory_path=workspaces_inventory,
            addons=addons,
            include_presets=options["presets"],
            include_datafiles=options["datafiles"],
            include_startup_scripts=options["startup_scripts"],
            include_app_templates=options["app_templates"],
            include_history=options["history"],
        )

        result = create_profile(snapshot, output)
        report = {
            "status": "success",
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "profile_path": str(result.path),
            "user_root": str(user_root),
            "source_version": list(bpy.app.version[:3]),
            "install_mode": snapshot.install_mode,
            "components": list(result.manifest.get("components", [])),
            "file_count": len(result.manifest.get("files", [])),
            "size_bytes": result.path.stat().st_size,
            "notes": notes,
            "warnings": warnings + list(result.warnings),
        }
        _write_report(report_path, report)
        return report
    finally:
        # 临时目录在 create_profile 完成后即可删除：包内已有全部字节与哈希
        import shutil

        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    report_target = None
    try:
        report_target = Path(_script_arguments()[2])
        result = run()
        print(_REPORT_MARKER + json.dumps(
            {"status": result["status"], "profile_path": result["profile_path"]},
            ensure_ascii=True,
        ))
    except Exception as exc:  # noqa: BLE001 —— 任何失败都必须让 GUI 看见
        traceback.print_exc()
        if report_target is not None:
            try:
                _write_report(
                    report_target,
                    {
                        "status": "failed",
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
            except Exception:  # noqa: BLE001 —— 连报告都写不了就只能靠退出码
                pass
        sys.exit(1)