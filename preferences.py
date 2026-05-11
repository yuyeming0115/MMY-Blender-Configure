import bpy
from bpy.props import BoolProperty, StringProperty


class MMYConfigPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    include_keymap: BoolProperty(name="用户快捷键", default=True)
    include_prefs: BoolProperty(name="软件设置", default=True)
    include_addons: BoolProperty(name="插件", default=True)
    include_config: BoolProperty(name="用户配置 (bookmarks, recent-files 等)", default=True)
    include_presets: BoolProperty(name="预设", default=True)
    include_startup: BoolProperty(name="启动脚本", default=True)
    include_datafiles: BoolProperty(name="数据文件 (笔刷/灯光/扩展)", default=True)
    backup_path: StringProperty(name="备份路径", subtype='DIR_PATH', default="")


def register():
    bpy.utils.register_class(MMYConfigPreferences)


def unregister():
    bpy.utils.unregister_class(MMYConfigPreferences)
