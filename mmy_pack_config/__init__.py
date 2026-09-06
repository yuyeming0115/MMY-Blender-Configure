bl_info = {
    "name": "MMY Blender Configure（配置迁移与打包）",
    "author": "会叫喵的鱼",
    "version": (1, 4, 0),
    "blender": (4, 5, 0),
    "category": "MMY-Tools",
    "description": "Blender 跨版本配置迁移、Portable 备份与插件加载耗时监控",
    "location": "顶部菜单栏左侧 + 偏好设置 > MMY Blender Configure",
}

import os

import bpy
from . import preferences, ui, addon_timer, auto_pack, migration


_audit_mode = False
_startup_cleanup_registered = False


def _startup_cleanup():
    """启动 5 秒后清理超过 24 小时的迁移事务残留目录（.mmy_old_/.mmy_stage_ 等）。"""
    global _startup_cleanup_registered
    _startup_cleanup_registered = False
    try:
        from pathlib import Path
        from .migration_core import cleanup_stale_migration_artifacts

        parents = {Path(bpy.utils.resource_path("USER")).parent}
        addon_entry = bpy.context.preferences.addons.get(__package__)
        if addon_entry:
            out_dir = getattr(addon_entry.preferences, "pack_output_path", "")
            if out_dir:
                parents.add(Path(out_dir))
        removed = cleanup_stale_migration_artifacts(sorted(parents), max_age_hours=24.0)
        if removed:
            print(f"[MMY] 已自动清理 {len(removed)} 个迁移残留目录")
    except Exception as exc:
        print(f"[MMY] 迁移残留自动清理失败: {exc}")
    return None


def register():
    global _audit_mode, _startup_cleanup_registered
    _audit_mode = os.environ.get("MMY_MIGRATION_AUDIT") == "1"

    # 先注册偏好设置、迁移 Operator 与 UI。
    preferences.register()
    migration.register()
    ui.register()

    # 目标版本后台审计期间不启动定时器或耗时监控。
    if _audit_mode:
        return

    auto_pack.register()

    if not _startup_cleanup_registered:
        try:
            bpy.app.timers.register(_startup_cleanup, first_interval=5.0)
            _startup_cleanup_registered = True
        except Exception as exc:
            print(f"[MMY] 注册迁移残留清理失败: {exc}")

    addon_entry = bpy.context.preferences.addons.get(__package__)
    addon_prefs = addon_entry.preferences if addon_entry else None
    monitor_enabled = bool(
        getattr(addon_prefs, "enable_load_monitoring", True)
    )

    if monitor_enabled:
        # 开启本次监控会话，并加载上一次的监控数据用于临时展示
        addon_timer.manager.begin_session()
        addon_timer.manager.load_data()
        addon_timer.manager.load_probe_data()

        # 注入 monkey-patch（捕获后续加载的插件）
        addon_timer.manager.patch()

        # 注册 fallback 扫描（2秒后扫描已加载但未捕获的插件，写盘已改后台线程）
        addon_timer.manager.register_fallback()


def unregister():
    if not _audit_mode:
        auto_pack.unregister()
    ui.unregister()
    migration.unregister()
    preferences.unregister()
    addon_timer.manager.unpatch()
