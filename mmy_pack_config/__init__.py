bl_info = {
    "name": "MMY Blender Configure（Portable配置打包输出）",
    "author": "会叫喵的鱼",
    "version": (1, 1, 3),
    "blender": (4, 5, 0),
    "category": "MMY-Tools",
    "description": "Blender Portable 配置打包 + 插件加载耗时监控",
    "location": "顶部菜单栏左侧 + 偏好设置 > MMY Blender Configure",
}

import bpy
from . import preferences, ui, addon_timer, auto_pack


def register():
    # 1. 先注册偏好设置与 UI（确保能读取「启用加载耗时监控」开关）
    preferences.register()
    ui.register()
    auto_pack.register()

    # 2. 读取开关：关闭时完全跳过计时相关开销（零启动负担）
    prefs = bpy.context.preferences.addons.get(__package__)
    monitor_enabled = bool(getattr(prefs, "enable_load_monitoring", True)) if prefs else True

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
    auto_pack.unregister()
    ui.unregister()
    preferences.unregister()
    addon_timer.manager.unpatch()
