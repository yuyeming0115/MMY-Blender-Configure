"""
MMY Blender Configure — 顶部菜单栏 UI

在 Blender 顶部菜单栏最左侧添加「打包导出」按钮。
符合 AGENTS.md 规范：文件操作类功能挂载在顶部 Header。

路径查找策略（多级回退）：
  1. 用户在偏好设置中配置的路径
  2. 从 __file__ 向上搜索父目录，找 pack.py
  3. 兜底：弹出文件选择对话框让用户选择
"""

import bpy
import subprocess
import sys
import os
from pathlib import Path


def _find_pack_script():
    """
    多级策略查找 pack.py 路径：
    
    策略 1：从 __file__ 向上搜索（最多 6 层）
      addons/mmy_pack_config/ui.py → addons/ → scripts/ → portable/ → Blender5.1/ → ...
      
    策略 2：检查常见开发目录
      
    返回：Path 对象或 None
    """
    # --- 策略 1：向上搜索 ---
    current = Path(__file__).resolve().parent
    for _ in range(8):  # 最多向上搜 8 层
        candidate = current / "pack.py"
        if candidate.exists():
            print(f"[MMY] 找到 pack.py: {candidate}")
            return candidate
        parent = current.parent
        if parent == current:  # 到达根目录
            break
        current = parent

    # --- 策略 2：常见开发目录 ---
    home = Path.home()
    candidates = [
        home / "GitWork" / "MMY-Blender-Configure" / "pack.py",
        home / "Desktop" / "MMY-Blender-Configure" / "pack.py",
        home / "Documents" / "MMY-Blender-Configure" / "pack.py",
    ]
    for c in candidates:
        if c.exists():
            print(f"[MMY] 找到 pack.py (常用目录): {c}")
            return c

    return None


class MMY_OT_PackPortable(bpy.types.Operator):
    """打包导出 Blender Portable 配置文件夹为 ZIP"""
    bl_idname = "mmy.pack_portable"
    bl_label = "打包导出 Portable"
    bl_description = "运行 pack.py，选择 portable 文件夹并导出为 ZIP"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(
        name="pack.py 路径",
        description="如果自动找不到 pack.py，请手动选择",
        subtype='FILE_PATH',
        default="",
        options={'HIDDEN', 'SKIP_SAVE'},
    )

    def execute(self, context):
        pack_script = Path(self.filepath) if self.filepath else _find_pack_script()

        if not pack_script or not pack_script.exists():
            # 兜底：让用户选择 pack.py 文件
            self.report({"ERROR"}, "找不到 pack.py。请在插件偏好设置中配置路径，或确保项目目录可访问")
            # 打开文件浏览器让用户选择
            context.window_manager.fileselect_add(self.properties)
            return {'CANCELLED'} if not hasattr(bpy.context, 'window') else {'RUNNING_MODAL'}

        python_exe = sys.executable

        try:
            subprocess.Popen(
                [python_exe, str(pack_script)],
                cwd=str(pack_script.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            self.report({"INFO"}, f"已启动: {pack_script}")
        except Exception as e:
            self.report({"ERROR"}, f"启动失败: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}

    def invoke(self, context, event):
        """首次调用时尝试找 pack.py，找不到则弹出选择对话框"""
        pack_script = _find_pack_script()

        if pack_script and pack_script.exists():
            self.filepath = str(pack_script)
            return self.execute(context)

        # 找不到，弹出文件选择
        context.window_manager.fileselect_add(self.properties)
        return {'RUNNING_MODAL'}


# ============================================================
# 注册到顶部菜单栏
# ============================================================

def draw_pack_button(self, context):
    layout = self.layout
    row = layout.row(align=True)
    row.scale_x = 0.85
    op = row.operator("mmy.pack_portable", text="打包导出", icon="PACKAGE")


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
