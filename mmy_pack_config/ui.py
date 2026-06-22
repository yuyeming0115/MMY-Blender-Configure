"""
MMY Blender Configure — 顶部菜单栏 UI + 打包弹窗

打包脚本内嵌在插件包中（mmy_pack_config/pack_script.py），
通过命令行参数传递路径，不依赖 tkinter。
"""

import bpy
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime


_PACK_SCRIPT = Path(__file__).parent / "pack_script.py"


# ============================================================
# 打包弹窗 Operator
# ============================================================

class MMY_OT_PackPortable(bpy.types.Operator):
    """选择路径并打包导出 Blender Portable 配置"""
    bl_idname = "mmy.pack_portable"
    bl_label = "打包导出 Portable"
    bl_description = "选择 portable 文件夹和输出路径，执行打包"
    bl_options = {"REGISTER"}

    portable_path: bpy.props.StringProperty(
        name="Portable 文件夹",
        description="Blender portable/ 配置文件夹路径",
        subtype='DIR_PATH',
        default="",
    )
    output_path: bpy.props.StringProperty(
        name="输出 ZIP 路径",
        description="打包输出的 ZIP 文件路径（留头自动生成）",
        subtype='FILE_PATH',
        default="",
    )
    version_override: bpy.props.StringProperty(
        name="版本号（可选）",
        description="留空则自动从路径中检测 Blender 版本号",
        default="",
    )

    def invoke(self, context, event):
        # 从偏好设置预填路径
        prefs = context.preferences.addons.get(__package__)
        if prefs:
            self.portable_path = getattr(prefs.preferences, "last_portable_path", "")
            out_dir = getattr(prefs.preferences, "pack_output_path", "")
            if out_dir and Path(out_dir).exists():
                # 先不填 output_path，让用户决定
                pass
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "portable_path")
        layout.prop(self, "output_path")
        layout.prop(self, "version_override")
        layout.label(text="输出路径留空则自动生成（桌面/版本号_时间戳.zip）", icon='INFO')

    def execute(self, context):
        # ---- 校验 portable 路径 ----
        if not self.portable_path or not Path(self.portable_path).exists():
            self.report({"ERROR"}, "请先选择有效的 Portable 文件夹")
            return {"CANCELLED"}

        portable_path = Path(self.portable_path)

        # ---- 生成输出路径 ----
        if not self.output_path:
            version = self.version_override.strip()
            if not version:
                # 尝试从路径自动检测
                import re
                parent_name = portable_path.parent.name
                m = re.search(r"(\d+\.\d+(\.\d+)?)", parent_name)
                if m:
                    version = m.group(1)
                    if version.count(".") == 1:
                        version += ".0"
                else:
                    version = "unknown"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            default_name = f"Blender_Portable_v{version}_{timestamp}.zip"

            # 优先用偏好设置中的输出目录
            out_dir = None
            prefs = context.preferences.addons.get(__package__)
            if prefs:
                out_dir = getattr(prefs.preferences, "pack_output_path", "") or None
            if not out_dir or not Path(out_dir).exists():
                out_dir = str(Path.home() / "Desktop")
            self.output_path = str(Path(out_dir) / default_name)

        output_path = Path(self.output_path)
        if not output_path.parent.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # ---- 保存上次路径到偏好设置 ----
        prefs = context.preferences.addons.get(__package__)
        if prefs:
            try:
                prefs.preferences.last_portable_path = str(portable_path)
                # 触发配置文件写入
                from .preferences import _on_pack_output_path_changed
                # 只更新 portable 路径，不碰 output 路径
                import json
                config_path = Path(__file__).parent.parent / ".pack_config.json"
                config = {}
                if config_path.exists():
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                config["last_portable_path"] = str(portable_path)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[MMY] 保存路径失败: {e}")

        # ---- 组装命令行参数 ----
        args = [sys.executable, str(_PACK_SCRIPT), str(portable_path)]
        if self.output_path:
            args.append(str(output_path))
        if self.version_override.strip():
            args.extend(["--version", self.version_override.strip()])

        # ---- 启动子进程 ----
        try:
            subprocess.Popen(
                args,
                cwd=str(_PACK_SCRIPT.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            self.report({"INFO"}, f"已启动打包：{portable_path.name}")
            print(f"[MMY] 打包进程已启动：{' '.join(str(a) for a in args)}")
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
