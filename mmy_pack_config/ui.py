"""
MMY Blender Configure — 顶部菜单栏 UI

在 Blender 顶部菜单栏最左侧添加「打包导出」按钮。
打包脚本内嵌在插件包中（mmy_pack_config/pack_script.py），无需用户配置路径。
"""

import bpy
import subprocess
import sys
import os
from pathlib import Path


# 打包脚本就在本插件包内，直接定位
_PACK_SCRIPT = Path(__file__).parent / "pack_script.py"


class MMY_OT_PackPortable(bpy.types.Operator):
    """打包导出 Blender Portable 配置文件夹为 ZIP"""
    bl_idname = "mmy.pack_portable"
    bl_label = "打包导出 Portable"
    bl_description = "运行内置打包脚本，选择 portable 文件夹并导出为 ZIP"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if not _PACK_SCRIPT.exists():
            self.report({"ERROR"}, f"找不到打包脚本: {_PACK_SCRIPT}")
            return {"CANCELLED"}

        python_exe = sys.executable

        try:
            subprocess.Popen(
                [python_exe, str(_PACK_SCRIPT)],
                cwd=str(_PACK_SCRIPT.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            self.report({"INFO"}, "已启动打包工具")
        except Exception as e:
            self.report({"ERROR"}, f"启动失败: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


# ============================================================
# 注册到顶部菜单栏
# ============================================================

def draw_pack_button(self, context):
    layout = self.layout
    row = layout.row(align=True)
    row.scale_x = 0.85
    row.operator("mmy.pack_portable", text="打包导出", icon="PACKAGE")


def register():
    bpy.utils.register_class(MMY_OT_PackPortable)
    try:
        bpy.types.TOPBAR_MT_editor_menus.append(draw_pack_button)
    except Exception as e:
        print(f"[MMY] 无法注册顶部菜单按钮: {e}")


def unregister():
    try:
        bpy.types.TOPBAR_MT_editor_menus.remove(draw_pack_button)
    except Exception:
        pass
    try:
        bpy.utils.unregister_class(MMY_OT_PackPortable)
    except Exception:
        pass
