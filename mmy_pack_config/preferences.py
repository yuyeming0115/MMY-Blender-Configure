import bpy
from bpy.props import BoolProperty, StringProperty, CollectionProperty


class BookmarkItem(bpy.types.PropertyGroup):
    """单个书签项"""
    path: StringProperty(
        name="路径",
        description="书签保存的路径",
        subtype='DIR_PATH'
    )
    name: StringProperty(
        name="名称",
        description="书签显示名称"
    )


class MMYConfigPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    include_keymap: BoolProperty(
        name="用户快捷键",
        description="备份/导入自定义快捷键设置",
        default=True
    )
    include_prefs: BoolProperty(
        name="软件设置",
        description="备份/导入 Blender 软件偏好设置（界面、视图、编辑器等）",
        default=True
    )
    include_addons: BoolProperty(
        name="插件",
        description="备份/导入已安装的插件及其设置",
        default=True
    )
    include_config: BoolProperty(
        name="用户配置 (bookmarks, recent-files 等)",
        description="备份/导入书签、最近文件等配置（可能包含绝对路径）",
        default=True
    )
    include_presets: BoolProperty(
        name="预设",
        description="备份/导入渲染预设、节点预设等",
        default=True
    )
    include_startup: BoolProperty(
        name="启动脚本",
        description="备份/导入启动时自动运行的 Python 脚本",
        default=True
    )
    include_datafiles: BoolProperty(
        name="数据文件 (笔刷/灯光/扩展)",
        description="备份/导入自定义笔刷、灯光工作室、扩展资源",
        default=True
    )
    backup_path: StringProperty(
        name="备份保存位置",
        description="备份文件的默认保存目录",
        subtype='DIR_PATH',
        default=""
    )
    bookmarks: CollectionProperty(
        type=BookmarkItem,
        name="路径书签",
        description="收藏的备份路径列表"
    )
    bookmark_index: bpy.props.IntProperty(
        name="当前书签索引",
        default=0
    )


classes = (BookmarkItem, MMYConfigPreferences)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
