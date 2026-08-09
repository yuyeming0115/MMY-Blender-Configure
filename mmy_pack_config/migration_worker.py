"""在目标 Blender 中运行的迁移审计脚本。

此文件通过 ``blender --background --python`` 直接执行，不依赖插件注册成功。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import addon_utils
import bpy


def _script_arguments() -> tuple[Path, Path, Path, Path]:
    if "--" not in sys.argv:
        raise RuntimeError("缺少迁移审计参数")
    args = sys.argv[sys.argv.index("--") + 1 :]
    if len(args) != 4:
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
    keyconfig = bpy.context.window_manager.keyconfigs.user
    if keyconfig is None:
        return set()
    signatures = set()
    for keymap in keyconfig.keymaps:
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
    expected_items = set(expected_data.get("items", []))
    actual_items = _current_keymap_signatures()
    missing = sorted(expected_items - actual_items)
    orphan_operators = []
    for signature in sorted(expected_items & actual_items):
        try:
            idname = json.loads(signature).get("idname", "")
        except json.JSONDecodeError:
            continue
        if idname and not _operator_exists(idname):
            orphan_operators.append(idname)
    return {
        "source_count": len(expected_items),
        "target_count": len(actual_items),
        "missing_count": len(missing),
        "missing": missing[:100],
        "orphan_operators": sorted(set(orphan_operators)),
    }


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


def run() -> dict:
    manifest_path, fingerprint_path, report_path, expected_root = _script_arguments()
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
    save_result = bpy.ops.wm.save_userpref()
    if "FINISHED" not in save_result:
        raise RuntimeError("目标 Blender 无法保存迁移后的偏好设置")

    degraded = bool(disabled or keymap["missing_count"])
    if missing_paths:
        warnings.append(f"发现 {len(missing_paths)} 个失效绝对路径")
    report = {
        "status": "degraded" if degraded else "success",
        "audited_at": datetime.now().isoformat(timespec="seconds"),
        "source_version": list(source_version),
        "target_version": list(target_version),
        "target_user_root": str(actual_root),
        "disabled_addons": disabled,
        "keymap": keymap,
        "missing_paths": missing_paths,
        "warnings": warnings,
    }
    _write_json(report_path, report)
    return report


if __name__ == "__main__":
    report_target = None
    try:
        _, _, report_target, _ = _script_arguments()
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
