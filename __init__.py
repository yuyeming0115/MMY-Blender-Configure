bl_info = {
    "name": "MMY Blender Configure",
    "author": "会叫喵的鱼",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "category": "MMY-Tools",
    "description": "Blender 配置备份、导入、导出及插件耗时监控",
    "location": "顶部菜单栏左侧",
}

from . import preferences, operators, ui, addon_timer


def register():
    addon_timer.manager.patch()
    addon_timer.manager.register_fallback()
    preferences.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    preferences.unregister()
    addon_timer.manager.unpatch()
