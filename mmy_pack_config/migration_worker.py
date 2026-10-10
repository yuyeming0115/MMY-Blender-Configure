"""在目标 Blender 中运行的迁移审计脚本。

此文件通过 ``blender --background --python`` 直接执行，不依赖插件注册成功。

v1.5.0：可选 donor 模式——启动文件不再被旧版快照整体替换，而是在目标
自己的启动文件（新版默认工作区布局）基础上，追加旧配置中的自定义工作区。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import addon_utils
import bpy

from migration_core import classify_keymap_audit


# v1.5.0 工作区级布局恢复：出厂工作区是新版 UI 的载体，一律不移植
FACTORY_WORKSPACE_NAMES = frozenset(
    {
        "Layout",
        "Modeling",
        "Sculpting",
        "UV Editing",
        "Texture Paint",
        "Shading",
        "Rendering",
        "Compositing",
        "Geometry Nodes",
        "Scripting",
        "Motion Tracking",
        "Masking",
        "VFX",
        "Video Editing",
    }
)


def _script_arguments() -> tuple[Path, ...]:
    if "--" not in sys.argv:
        raise RuntimeError("缺少迁移审计参数")
    args = sys.argv[sys.argv.index("--") + 1 :]
    if len(args) not in {4, 6}:
        raise RuntimeError("迁移审计参数数量错误")
    return tuple(Path(value) for value in args)  # type: ignore[return-value]


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _version_tuple(value) -> tuple[int, int, int]:
    if isinstance(value, str):
        parts = value.split(".")
    else:
        parts = list(value or [])
    result = []
    for part in parts[:3]:
        try:
            result.append(int(part))
        except (TypeError, ValueError):
            result.append(0)
    result.extend([0] * (3 - len(result)))
    return tuple(result[:3])


def _addon_declared_compatible(addon: dict, target_version: tuple[int, int, int]) -> bool:
    minimum = addon.get("blender_version_min")
    maximum = addon.get("blender_version_max")
    if minimum and target_version < _version_tuple(minimum):
        return False
    if maximum and target_version > _version_tuple(maximum):
        return False
    return True


def _disable_addon(module_name: str) -> tuple[bool, str]:
    try:
        addon_utils.disable(module_name, default_set=True)
        if module_name in bpy.context.preferences.addons:
            return False, "禁用后仍存在于启用列表"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _audit_addons(manifest: dict) -> tuple[list[dict], list[str]]:
    disabled: list[dict] = []
    warnings: list[str] = []
    target_version = tuple(bpy.app.version[:3])
    try:
        available = {module.__name__ for module in addon_utils.modules(refresh=True)}
    except Exception as exc:
        available = set()
        warnings.append(f"刷新插件清单失败：{exc}")

    for addon in manifest.get("addons", []):
        if not isinstance(addon, dict) or not addon.get("enabled"):
            continue
        module_name = str(addon.get("module") or "")
        if not module_name:
            continue

        reason = ""
        if not _addon_declared_compatible(addon, target_version):
            reason = "扩展清单声明不兼容目标 Blender"
        elif available and module_name not in available:
            reason = "目标版本未发现插件模块"
        else:
            try:
                _enabled_by_default, loaded = addon_utils.check(module_name)
                if not loaded:
                    reason = "插件未能在目标版本加载"
            except Exception as exc:
                reason = f"检查插件状态失败：{exc}"

        if reason:
            disabled_ok, disable_error = _disable_addon(module_name)
            disabled.append(
                {
                    "module": module_name,
                    "kind": addon.get("kind", "legacy"),
                    "reason": reason,
                    "disabled": disabled_ok,
                    "disable_error": disable_error,
                }
            )
            if not disabled_ok:
                warnings.append(f"无法持久禁用 {module_name}：{disable_error}")
    return disabled, warnings


def _keymap_item_signature(keymap, item) -> str:
    fields = {
        "keymap": keymap.name,
        "space_type": keymap.space_type,
        "region_type": keymap.region_type,
        "idname": item.idname,
        "map_type": item.map_type,
        "type": item.type,
        "value": item.value,
        "any": bool(item.any),
        "shift": bool(item.shift),
        "ctrl": bool(item.ctrl),
        "alt": bool(item.alt),
        "oskey": bool(item.oskey),
        "key_modifier": item.key_modifier,
        "direction": getattr(item, "direction", "ANY"),
        "repeat": bool(getattr(item, "repeat", False)),
        "active": bool(item.active),
    }
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _current_keymap_signatures() -> set[str]:
    """采集目标侧键位签名（user ∪ addon 两张键位表）。

    后台模式只落地 userpref 存储的差异项（默认键位不物化）；部分插件在
    后台仍会注册自己的键位（可能落在 addon 表），并集可避免误判。
    """
    keyconfigs = bpy.context.window_manager.keyconfigs
    signatures = set()
    for config in (keyconfigs.user, keyconfigs.addon):
        if config is None:
            continue
        for keymap in config.keymaps:
            for item in keymap.keymap_items:
                signatures.add(_keymap_item_signature(keymap, item))
    return signatures


def _operator_exists(idname: str) -> bool:
    if "." not in idname:
        return False
    namespace, operator = idname.split(".", 1)
    group = getattr(bpy.ops, namespace, None)
    return bool(group is not None and hasattr(group, operator))


def _audit_keymap(fingerprint_path: Path) -> dict:
    expected_data = json.loads(fingerprint_path.read_text(encoding="utf-8"))
    if expected_data.get("skipped"):
        return {
            "skipped": str(expected_data["skipped"]),
            "source_count": 0,
            "target_count": 0,
            "matched_count": 0,
            "lost_count": 0,
            "lost": [],
            "orphan_operators": [],
            "unverifiable_count": 0,
        }
    expected_items = list(expected_data.get("items", []))
    actual_items = _current_keymap_signatures()
    result = classify_keymap_audit(expected_items, actual_items, _operator_exists)
    result["source_count"] = len(expected_items)
    result["target_count"] = len(actual_items)
    return result


def _path_value_is_missing(value: str) -> bool:
    if not value or value.startswith("//") or "://" in value:
        return False
    expanded = os.path.expandvars(os.path.expanduser(value))
    try:
        resolved = bpy.path.abspath(expanded)
    except Exception:
        resolved = expanded
    return bool(os.path.isabs(resolved) and not os.path.exists(resolved))


def _rna_missing_paths(owner, owner_name: str) -> list[dict]:
    missing = []
    rna = getattr(owner, "bl_rna", None)
    if rna is None:
        return missing
    for prop in rna.properties:
        if prop.identifier == "rna_type" or prop.type != "STRING":
            continue
        if prop.subtype not in {"FILE_PATH", "DIR_PATH"}:
            continue
        try:
            value = getattr(owner, prop.identifier)
        except Exception:
            continue
        if isinstance(value, str) and _path_value_is_missing(value):
            missing.append(
                {
                    "owner": owner_name,
                    "property": prop.identifier,
                    "path": value,
                }
            )
    return missing


def _audit_paths() -> list[dict]:
    missing = _rna_missing_paths(bpy.context.preferences.filepaths, "Blender 文件路径")
    for module_name, addon in bpy.context.preferences.addons.items():
        preferences = getattr(addon, "preferences", None)
        if preferences is not None:
            missing.extend(_rna_missing_paths(preferences, f"插件 {module_name}"))
    return missing[:200]


def _restore_workspaces(donor_startup: Path, inventory_path: Path) -> dict:
    """donor 模式：在目标自己的启动文件上追加旧配置的自定义工作区。

    设计要点：
    - 默认工作区（FACTORY_WORKSPACE_NAMES）是新版 UI 的载体，一律不移植；
    - 追加走 bpy.data.libraries.load（数据经内核 do_versions 升级，
      比拷贝文件更可靠），仅追加成功后才 save_as_mainfile；
    - 任何失败只记录 errors，不抛出——工作区恢复是附加价值，
      不应阻断或回滚整个迁移。
    """
    result = {"mode": "donor", "restored": [], "skipped": [], "errors": []}
    startup_path = Path(bpy.utils.user_resource("CONFIG")) / "startup.blend"

    try:
        bpy.ops.wm.read_homefile()
    except Exception as exc:
        result["errors"].append(f"读取目标启动文件失败：{exc}")
        return result

    try:
        inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
        items = inventory.get("items") or []
    except Exception as exc:
        result["errors"].append(f"工作区清单解析失败：{exc}")
        items = []

    # 清单缺失/损坏时兜底：把捐赠文件中非默认工作区全部视为自定义
    if items:
        wanted = [item.get("name", "") for item in items if not item.get("factory")]
    else:
        wanted = []
        try:
            with bpy.data.libraries.load(str(donor_startup), link=True) as (
                data_from,
                _data_to,
            ):
                wanted = [
                    name
                    for name in data_from.workspaces
                    if name not in FACTORY_WORKSPACE_NAMES
                ]
        except Exception as exc:
            result["errors"].append(f"读取捐赠文件工作区失败：{exc}")
            return result

    existing = {workspace.name for workspace in bpy.data.workspaces}
    to_append = [name for name in wanted if name and name not in existing]
    result["skipped"] = [name for name in wanted if name in existing]

    if to_append:
        appended_names: list[str] = []
        try:
            with bpy.data.libraries.load(str(donor_startup), link=False) as (
                data_from,
                data_to,
            ):
                available = set(data_from.workspaces)
                data_to.workspaces = [name for name in to_append if name in available]
                result["skipped"].extend(
                    name for name in to_append if name not in available
                )
            appended_names = [
                workspace.name
                for workspace in bpy.data.workspaces
                if workspace.name not in existing
            ]
        except Exception as exc:
            result["errors"].append(f"追加自定义工作区失败：{exc}")
            return result

        result["restored"] = sorted(appended_names)
        # 只有确认追加成功才写回启动文件；中途失败不污染目标启动文件
        try:
            save_result = bpy.ops.wm.save_as_mainfile(
                filepath=str(startup_path),
                check_existing=False,
            )
            if "FINISHED" not in save_result:
                result["errors"].append("启动文件保存未确认完成")
        except Exception as exc:
            result["errors"].append(f"保存启动文件失败：{exc}")

    return result


def run() -> dict:
    manifest_path, fingerprint_path, report_path, expected_root = _script_arguments()[:4]
    donor_startup: Path | None = None
    workspaces_inventory: Path | None = None
    extra_args = _script_arguments()[4:]
    if len(extra_args) == 2:
        donor_startup, workspaces_inventory = extra_args
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_root = Path(bpy.utils.resource_path("USER")).resolve(strict=False)
    expected = expected_root.resolve(strict=False)
    if os.path.normcase(str(actual_root)) != os.path.normcase(str(expected)):
        raise RuntimeError(f"目标用户目录不一致：预期 {expected}，实际 {actual_root}")

    source_version = _version_tuple(manifest.get("source", {}).get("blender_version"))
    target_version = tuple(bpy.app.version[:3])
    if source_version[0] != target_version[0] or target_version <= source_version:
        raise RuntimeError("目标版本不满足同主版本正向迁移要求")

    disabled, warnings = _audit_addons(manifest)
    disable_failures = [item for item in disabled if not item.get("disabled")]
    if disable_failures:
        modules = ", ".join(item["module"] for item in disable_failures)
        raise RuntimeError(f"无法安全禁用不兼容插件：{modules}")
    keymap = _audit_keymap(fingerprint_path)
    missing_paths = _audit_paths()

    workspace_report = None
    if donor_startup is not None and workspaces_inventory is not None:
        try:
            workspace_report = _restore_workspaces(donor_startup, workspaces_inventory)
        except Exception as exc:  # 防御：绝不因布局恢复中断审计
            workspace_report = {
                "mode": "donor",
                "restored": [],
                "skipped": [],
                "errors": [f"工作区恢复异常：{exc}"],
            }
        for ws_error in workspace_report.get("errors") or []:
            warnings.append(f"工作区恢复：{ws_error}")

    save_result = bpy.ops.wm.save_userpref()
    if "FINISHED" not in save_result:
        raise RuntimeError("目标 Blender 无法保存迁移后的偏好设置")

    degraded = bool(disabled or keymap.get("lost_count"))
    if missing_paths:
        warnings.append(f"发现 {len(missing_paths)} 个失效绝对路径")
    orphan_operators = keymap.get("orphan_operators") or []
    if orphan_operators:
        warnings.append(
            f"{len(orphan_operators)} 个快捷键操作在目标审计环境不存在"
            "（插件后台未注册或目标版本已移除，详见报告）"
        )
    report = {
        "status": "degraded" if degraded else "success",
        "audited_at": datetime.now().isoformat(timespec="seconds"),
        "source_version": list(source_version),
        "target_version": list(target_version),
        "target_user_root": str(actual_root),
        "disabled_addons": disabled,
        "keymap": keymap,
        "missing_paths": missing_paths,
        "workspaces": workspace_report,
        "warnings": warnings,
    }
    _write_json(report_path, report)
    return report


if __name__ == "__main__":
    report_target = None
    try:
        _args = _script_arguments()
        report_target = _args[2] if len(_args) >= 3 else None
        result = run()
        print(f"[MMY Migration] {result['status']}")
    except Exception as exc:
        if report_target is not None:
            _write_json(
                report_target,
                {
                    "status": "failed",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        traceback.print_exc()
        raise
