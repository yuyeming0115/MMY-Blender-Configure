"""运行中 Blender 的配置采集层（依赖 bpy，但不依赖插件注册）。

这里集中放三类「只能在运行中的 Blender 里读到」的数据：

1. 插件清单（含 extension 的 blender_manifest 版本声明）
2. 用户差异键位指纹
3. 工作区清单（供 donor 模式选择性移植自定义工作区）

v1.6.0：从 ``migration.py`` 抽出。原先这些函数只在插件 Operator 里用，
导出必须先装插件、注册成功才能触发——这让「目标机器无需装插件」的
迁移链路在**导出端**留了个缺口：用户必须先在旧版 Blender 里装上插件。
抽出后 ``export_worker.py`` 可以被 ``blender --background --python``
直接调用，GUI 拖入 portable 文件夹 + 源blender.exe 就能出迁移包，
全程不需要打开过 Blender 界面。

导入方式必须同时兼容两种上下文：

- 包内：``from .migration_core import FACTORY_WORKSPACE_NAMES``（插件注册）
- 独立：``import migration_core``（``--python`` 脚本，父目录在 sys.path）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import addon_utils
import bpy

try:  # 包内导入（插件正常注册时）
    from .migration_core import FACTORY_WORKSPACE_NAMES
except ImportError:  # 独立脚本导入（blender --background --python）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from migration_core import FACTORY_WORKSPACE_NAMES  # type: ignore[no-redef]


# 后台审计（blender --background）不会物化默认键位——实测 5.1 工厂模式后台
# keyconfigs.default 仅 12 条；GUI 模式才会完整加载（数千条）。因此指纹只采集
# 「用户相对默认键位与插件键位的差异项」，与目标侧后台能拿到的口径一致；
# 若默认键位条目数异常低（未物化），返回 skipped 标记让审计侧跳过比对，
# 宁可不审计也不产生误报。
_KEYMAP_DEFAULT_MATERIALIZED_MIN = 500


def version_list(value: Any) -> list[int]:
    """把 "4.5" / (4,5,0) / "4.5.0" 统一成 [4,5,0]。"""
    if not value:
        return []
    if isinstance(value, str):
        parts = value.split(".")
    else:
        parts = list(value)
    result: list[int] = []
    for part in parts:
        try:
            result.append(int(part))
        except (TypeError, ValueError):
            break
    return result[:3]


def user_resource_path(resource_type: str, user_root: Path, fallback_name: str) -> Path:
    """解析用户资源目录，取不到时回退到 user_root/<fallback_name>。"""
    value = bpy.utils.user_resource(resource_type)
    return Path(value) if value else user_root / fallback_name


def is_portable_mode() -> bool:
    """检测当前 Blender 是否便携模式（exe 同级存在 portable 目录）。

    不复用 ``utils.py``：那模块在插件包内以 ``from . import utils`` 引用，
    独立脚本里拿不到；判定逻辑本身只有两行，重复比耦合更划算。
    """
    blender_dir = Path(bpy.app.binary_path or sys.executable).resolve().parent
    return (blender_dir / "portable").is_dir()


def find_extension_manifest(module: Any) -> dict:
    """向上最多 4 层找 extension 的 blender_manifest.toml。"""
    module_file = getattr(module, "__file__", "")
    if not module_file:
        return {}
    current = Path(module_file).resolve(strict=False).parent
    for _ in range(4):
        manifest_path = current / "blender_manifest.toml"
        if manifest_path.is_file():
            try:
                import tomllib

                with manifest_path.open("rb") as handle:
                    return tomllib.load(handle)
            except Exception:  # noqa: BLE001
                return {}
        if current == current.parent:
            break
        current = current.parent
    return {}


def collect_addon_inventory() -> list[dict]:
    """采集已启用插件清单 + 版本声明（供目标端兼容性预判与自动禁用）。"""
    enabled = set(bpy.context.preferences.addons.keys())
    modules = {}
    try:
        modules = {module.__name__: module for module in addon_utils.modules(refresh=False)}
    except Exception as exc:  # noqa: BLE001
        print(f"[MMY Collect] 读取插件清单失败: {exc}")

    inventory = []
    for module_name in sorted(enabled.union(modules), key=str.casefold):
        module = modules.get(module_name)
        bl_info = getattr(module, "bl_info", {}) if module else {}
        kind = "extension" if module_name.startswith("bl_ext.") else "legacy"
        entry = {
            "module": module_name,
            "kind": kind,
            "enabled": module_name in enabled,
            "version": version_list(bl_info.get("version")),
            "blender_version_min": version_list(bl_info.get("blender")),
        }
        if kind == "extension" and module is not None:
            extension_manifest = find_extension_manifest(module)
            if extension_manifest.get("version"):
                entry["version"] = str(extension_manifest["version"])
            if extension_manifest.get("blender_version_min"):
                entry["blender_version_min"] = str(
                    extension_manifest["blender_version_min"]
                )
            if extension_manifest.get("blender_version_max"):
                entry["blender_version_max"] = str(
                    extension_manifest["blender_version_max"]
                )
        inventory.append(entry)
    return inventory


def keymap_item_signature(keymap: Any, item: Any) -> str:
    """单个键位项的稳定签名（字段齐全 + 排序序列化，跨进程可比对）。"""
    fields = {
        "keymap": keymap.name,
        "space_type": keymap.space_type,
        "region_type": keymap.region_type,
        "idname": item.idname,
        "map_type": getattr(item, "map_type", "KEYBOARD"),
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


def collect_keymap_fingerprint() -> dict:
    """采集用户差异键位指纹。

    schema_version=2：items 为 [{"sig", "kind", "idname", "keymap"}]。
    先剔除与默认键位表、插件键位表完全一致的条目（这些由目标版本/插件
    自行提供，不属于用户数据），剩余差异项按锚点分类：
    kind = "added"    —— 全新绑定（默认与插件键位中均无此条目锚点），
                        目标侧可可靠验证，缺失即真丢失；
           "modified" —— 修改默认绑定，依赖目标版本键位结构，后台不可验证；
           "addon"    —— 修改插件注册绑定，依赖插件在目标侧注册，后台不可验证。
    若默认键位条目数异常低（未物化），返回 skipped 标记让审计侧跳过比对。
    """
    keyconfigs = bpy.context.window_manager.keyconfigs
    user = keyconfigs.user
    if user is None:
        return {"schema_version": 2, "skipped": "no_user_keyconfig", "items": []}

    def _harvest(config):
        sigs: set[str] = set()
        pairs: set[tuple[str, str]] = set()
        if config is not None:
            for keymap in config.keymaps:
                for item in keymap.keymap_items:
                    sigs.add(keymap_item_signature(keymap, item))
                    pairs.add((keymap.name, item.idname))
        return sigs, pairs

    default_sigs, default_pairs = _harvest(keyconfigs.default)
    if len(default_sigs) < _KEYMAP_DEFAULT_MATERIALIZED_MIN:
        return {
            "schema_version": 2,
            "skipped": "default_keymaps_not_materialized",
            "items": [],
        }
    addon_sigs, addon_pairs = _harvest(keyconfigs.addon)
    baseline_sigs = default_sigs | addon_sigs

    items = []
    seen: set[str] = set()
    for keymap in user.keymaps:
        for item in keymap.keymap_items:
            sig = keymap_item_signature(keymap, item)
            if sig in baseline_sigs or sig in seen:
                continue
            seen.add(sig)
            pair = (keymap.name, item.idname)
            if pair in default_pairs:
                kind = "modified"
            elif pair in addon_pairs:
                kind = "addon"
            else:
                kind = "added"
            items.append(
                {
                    "sig": sig,
                    "kind": kind,
                    "idname": item.idname or "",
                    "keymap": keymap.name,
                }
            )
    items.sort(key=lambda entry: entry["sig"])
    return {"schema_version": 2, "items": items}


def collect_workspace_inventory() -> dict:
    """采集当前会话工作区清单（供 donor 模式选择性移植）。

    每个工作区记录：名称 / 是否出厂工作区 / 区域类型摘要。
    donor 模式只移植 factory=False 的自定义工作区，出厂工作区保持
    目标版本默认布局（新版 UI 的载体），避免旧布局快照遮蔽新 UI。
    """
    items = []
    try:
        workspaces = list(bpy.data.workspaces)
    except Exception as exc:  # noqa: BLE001
        print(f"[MMY Collect] 读取工作区清单失败: {exc}")
        return {"schema_version": 1, "items": []}
    for workspace in workspaces:
        area_types: set[str] = set()
        try:
            for screen in workspace.screens:
                for area in screen.areas:
                    area_types.add(str(getattr(area, "ui_type", "") or area.type))
        except Exception as exc:  # noqa: BLE001
            print(f"[MMY Collect] 读取工作区 {workspace.name} 布局失败: {exc}")
        items.append(
            {
                "name": workspace.name,
                "factory": workspace.name in FACTORY_WORKSPACE_NAMES,
                "area_types": sorted(area_types),
            }
        )
    items.sort(key=lambda entry: entry["name"])
    return {"schema_version": 1, "items": items}