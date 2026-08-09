bl_info = {
    "name": "MMY Blender Configure（配置迁移与打包）",
    "author": "会叫喵的鱼",
    "version": (1, 2, 0),
    "blender": (4, 5, 0),
    "category": "MMY-Tools",
    "description": "Blender 跨版本配置迁移、Portable 打包与插件加载耗时监控",
    "location": "顶部菜单栏左侧 + 偏好设置 > MMY Blender Configure",
}

import os

import bpy
from . import preferences, ui, addon_timer, auto_pack, migration


_audit_mode = False


def register():
    global _audit_mode
    _audit_mode = os.environ.get("MMY_MIGRATION_AUDIT") == "1"

    # 先注册偏好设置、迁移 Operator 与 UI。
    preferences.register()
    migration.register()
    ui.register()

    # 目标版本后台审计期间不启动定时器或耗时监控。
    if _audit_mode:
        return

    auto_pack.register()

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
