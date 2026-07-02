bl_info = {
    "name": "MMY Blender Configure（Portable配置打包输出）",
    "author": "会叫喵的鱼",
    "version": (1, 1, 0),
    "blender": (4, 5, 0),
    "category": "MMY-Tools",
    "description": "Blender Portable 配置打包 + 插件加载耗时监控",
    "location": "顶部菜单栏左侧 + 偏好设置 > MMY Blender Configure",
}

from . import preferences, ui, addon_timer


def register():
    # 1. 加载上一次的监控数据（重启后仍可查看）
    addon_timer.manager.load_data()

    # 2. 注入 monkey-patch（捕获后续加载的插件）
    addon_timer.manager.patch()

    # 3. 注册 fallback 扫描（2秒后扫描已加载但未捕获的插件）
    addon_timer.manager.register_fallback()

    # 4. 注册 UI 和偏好设置
    preferences.register()
    ui.register()


def unregister():
    ui.unregister()
    preferences.unregister()
    addon_timer.manager.unpatch()
