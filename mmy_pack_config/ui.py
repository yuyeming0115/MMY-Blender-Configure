"""
MMY Blender Configure — 顶部菜单栏 UI

在 Blender 顶部菜单栏最左侧添加「打包导出」按钮。
符合 AGENTS.md 规范：文件操作类功能挂载在顶部 Header。

路径查找策略（多级回退）：
  1. 偏好设置中手动配置的路径
  2. 从 __file__ 向上搜索父目录，找 pack.py
  3. 检查常见开发目录
"""

import bpy
import subprocess
import sys
import os
from pathlib import Path


def _find_pack_script():
    """
    多级策略查找 pack.py 路径。

    返回：Path 对象或 None
    """
    # 策略 1：向上搜索（最多 8 层）
    current = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = current / "pack.py"
        if candidate.exists():
            print(f"[MMY] 找到 pack.py: {candidate}")
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 策略 2：常见开发目录
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

    def execute(self, context):
        # 优先用偏好设置中的路径
        prefs = context.preferences.addons.get(__package__)
        manual_path = ""
        if prefs:
            manual_path = getattr(prefs.preferences, "pack_script_path", "")

        pack_path = None
        if manual_path and Path(manual_path).exists():
            pack_path = Path(manual_path)
        else:
            pack_path = _find_pack_script()

        if not pack_path or not pack_path.exists():
            self.report(
                {"ERROR"},
                "找不到 pack.py。请在偏好设置 > MMY Blender Configure 中配置 pack.py 路径"
            )
            return {"CANCELLED"}

        python_exe = sys.executable

        try:
            subprocess.Popen(
                [python_exe, str(pack_path)],
                cwd=str(pack_path.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            self.report({"INFO"}, f"已启动打包工具")
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
