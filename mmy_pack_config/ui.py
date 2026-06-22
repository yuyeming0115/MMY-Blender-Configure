"""
MMY Blender Configure — 顶部菜单栏 UI

在 Blender 顶部菜单栏最左侧添加「打包导出」按钮。
符合 AGENTS.md 规范：文件操作类功能挂载在顶部 Header。
"""

import bpy
import subprocess
import sys
import os
from pathlib import Path


class MMY_OT_PackPortable(bpy.types.Operator):
    """打包导出 Blender Portable 配置文件夹为 ZIP"""
    bl_idname = "mmy.pack_portable"
    bl_label = "打包导出 Portable"
    bl_description = "运行 pack.py，选择 portable 文件夹并导出为 ZIP"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # 找到 pack.py 的路径（项目根目录）
        pack_script = Path(__file__).parent.parent / "pack.py"

        if not pack_script.exists():
            self.report({"ERROR"}, f"找不到 pack.py: {pack_script}")
            return {"CANCELLED"}

        # 使用当前 Python 解释器运行 pack.py（独立进程）
        python_exe = sys.executable

        try:
            # 在独立进程中运行 pack.py（不阻塞 Blender）
            subprocess.Popen(
                [python_exe, str(pack_script)],
                cwd=str(pack_script.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            self.report({"INFO"}, "已启动打包工具，请查看弹出的窗口")
        except Exception as e:
            self.report({"ERROR"}, f"启动失败: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


# ============================================================
# 注册到顶部菜单栏（最左侧，Blender logo 旁边）
# ============================================================

def draw_pack_button(self, context):
    """在 TOPBAR_MT_editor_menus（编辑菜单区域）左侧绘制按钮"""
    layout = self.layout
    row = layout.row(align=True)
    row.scale_x = 0.8
    row.operator(
        "mmy.pack_portable",
        text="",
        icon="PACKAGE",
    )


def register():
    # 注册操作符
    bpy.utils.register_class(MMY_OT_PackPortable)

    # 挂载到顶部菜单栏最左侧（在文件菜单之前）
    # TOPBAR_MT_editor_menus 是包含 文件/编辑/渲染 等菜单的区域
    try:
        bpy.types.TOPBAR_MT_editor_menus.append(draw_pack_button)
    except Exception as e:
        print(f"[MMY] 无法注册顶部菜单按钮: {e}")


def unregister():
    # 移除按钮
    try:
        bpy.types.TOPBAR_MT_editor_menus.remove(draw_pack_button)
    except Exception:
        pass

    # 注销操作符
    bpy.utils.unregister_class(MMY_OT_PackPortable)
