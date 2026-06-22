bl_info = {
    "name": "MMY Blender Configure",
    "author": "会叫喵的鱼",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "category": "MMY-Tools",
    "description": "Blender Portable 配置打包 + 插件加载耗时监控",
    "location": "偏好设置 > 插件 > MMY Blender Configure",
}

from . import preferences, addon_timer


def register():
    addon_timer.manager.patch()
    addon_timer.manager.register_fallback()
    preferences.register()


def unregister():
    preferences.unregister()
    addon_timer.manager.unpatch()
