"""
MMY Blender Configure — 顶部菜单栏 UI + 打包弹窗

打包脚本内嵌在插件包中（mmy_pack_config/pack_script.py），
通过命令行参数传递路径，不依赖 tkinter。
"""

import bpy
import subprocess
import sys
import os
import re
from pathlib import Path
from datetime import datetime
from .path_memory import save_path_memory


_PACK_SCRIPT = Path(__file__).parent / "pack_script.py"


# ============================================================
# 辅助：生成标准文件名
# ============================================================

def _build_default_filename(portable_path_str, version_override=""):
    """生成标准输出文件名：MMY_Backup_Portable_v{版本}_{时间戳}.zip"""
    version = version_override.strip() if version_override else ""
    if not version:
        portable_path = Path(portable_path_str)
        parent_name = portable_path.parent.name
        m = re.search(r"(\d+\.\d+(\.\d+)?)", parent_name)
        if m:
            version = m.group(1)
            if version.count(".") == 1:
                version += ".0"
        else:
            version = "unknown"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    return f"MMY_Backup_Portable_v{version}_{timestamp}.zip"


def _resolve_output_path(raw_output, portable_path_str):
    """
    将用户输入的输出路径规范化为 .zip 文件路径。

    规则：
      空/留空     → 自动生成到配置的目录（默认桌面）
      目录路径    → 在该目录下自动生成文件名
      文件路径    → 补齐 .zip 后缀后使用
    """
    if not raw_output or not raw_output.strip():
        # 自动生成
        prefs = bpy.context.preferences.addons.get(__package__)
        out_dir = None
        if prefs:
            out_dir = getattr(prefs.preferences, "pack_output_path", "") or None
        if not out_dir or not Path(out_dir).exists():
            out_dir = str(Path.home() / "Desktop")
        return str(Path(out_dir) / _build_default_filename(portable_path_str))

    p = Path(raw_output)

    # 用户选了目录 → 在目录下生成文件名
    if p.is_dir():
        return str(p / _build_default_filename(portable_path_str))

    # 无后缀或非 zip 后缀 → 强制补 .zip
    if p.suffix.lower() != ".zip":
        return str(p.with_suffix(".zip"))

    return raw_output


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
        subtype='DIR_PATH',
        default="",
    )
    output_dir: bpy.props.StringProperty(
        name="输出目录",
        description="ZIP 文件保存位置。留空则使用桌面或偏好设置中的目录",
        subtype='DIR_PATH',
        default="",
    )
    version_override: bpy.props.StringProperty(
        name="版本号（可选）",
        default="",
    )
    exclude_cache: bpy.props.BoolProperty(
        name="排除缓存/临时文件",
        description="排除 __pycache__、*.pyc、.git、*.log 等文件（建议勾选，ZIP 更小）",
        default=True,
    )

    def invoke(self, context, event):
        prefs = context.preferences.addons.get(__package__)
        if prefs:
            self.portable_path = getattr(prefs.preferences, "last_portable_path", "")
            self.output_dir = getattr(prefs.preferences, "pack_output_path", "")
        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "portable_path")
        layout.prop(self, "output_dir")
        layout.prop(self, "version_override")
        layout.prop(self, "exclude_cache")
        layout.label(text="文件名自动生成：MMY_Backup_Portable_v{版本}_{时间}.zip",
                     icon='INFO')
        layout.label(text="zip 内含 manifest.json（版本/机器/文件清单），可随时辨识",
                     icon='BLANK1')

    def execute(self, context):
        # ---- 校验 portable 路径 ----
        if not self.portable_path or not Path(self.portable_path).exists():
            self.report({"ERROR"}, "请先选择有效的 Portable 文件夹")
            return {"CANCELLED"}

        portable_path = Path(self.portable_path)

        # ---- 解析输出路径（关键修复）----
        output_path = _resolve_output_path(
            self.output_dir,
            str(portable_path),
        )
        # 如果有手动版本号也传进去
        if self.version_override.strip():
            # 重新构建带自定义版本号的文件名
            fname = _build_default_filename(str(portable_path), self.version_override)
            out_dir = str(Path(output_path).parent)
            output_path = str(Path(out_dir) / fname)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"[MMY] 打包参数：portable={portable_path}  output={output_path}")

        # ---- 保存上次使用的路径 ----
        prefs = context.preferences.addons.get(__package__)
        if prefs:
            try:
                prefs.preferences.last_portable_path = str(portable_path)
                prefs.preferences.pack_output_path = str(output_path.parent)
                save_path_memory(str(portable_path), str(output_path.parent))

                import json
                config_path = Path(__file__).parent.parent / ".pack_config.json"
                config = {}
                if config_path.exists():
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                config["last_portable_path"] = str(portable_path)
                config["last_output_dir"] = str(output_path.parent)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[MMY] 保存路径失败: {e}")

        # ---- 启动子进程 ----
        args = [
            sys.executable,
            str(_PACK_SCRIPT),
            str(portable_path),
            str(output_path),
        ]
        if self.version_override.strip():
            args.extend(["--version", self.version_override.strip()])
        if not self.exclude_cache:
            args.append("--all")
            print(f"[MMY] 使用 --all 模式（不排除任何文件）")

        try:
            subprocess.Popen(
                args,
                cwd=str(_PACK_SCRIPT.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            self.report({"INFO"}, f"已启动打包：{output_path.name}")
        except Exception as e:
            self.report({"ERROR"}, f"启动失败: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


# ============================================================
# 配置管理总入口
# ============================================================

class MMY_OT_OpenConfigManager(bpy.types.Operator):
    bl_idname = "mmy.open_config_manager"
    bl_label = "MMY 配置管理"
    bl_description = "备份、跨版本迁移与恢复"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        # confirm_text 明确为「关闭」：本面板只作为入口，点关闭不触发任何操作，
        # 避免旧版「确定」按钮造成的"点了却没反应"误导。
        return context.window_manager.invoke_props_dialog(
            self, width=480, confirm_text="关闭"
        )

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        from . import utils

        layout = self.layout
        addon = context.preferences.addons.get(__package__)
        prefs = addon.preferences if addon else None

        # ---------- 状态区 ----------
        status_box = layout.box()
        version = ".".join(str(v) for v in bpy.app.version[:3])
        mode = "Portable" if utils.is_portable_mode() else "普通安装"
        row = status_box.row()
        row.label(text=f"当前 Blender {version}（{mode}）", icon='BLENDER')
        out_dir = getattr(prefs, "pack_output_path", "") if prefs else ""
        if not out_dir:
            out_dir = str(Path.home() / "Desktop")
        status_box.label(text=f"备份目录：{out_dir}", icon='FILE_FOLDER')
        last_status = getattr(prefs, "last_migration_status", "") if prefs else ""
        if last_status:
            icon = 'CHECKMARK' if "成功" in last_status else (
                'ERROR' if ("失败" in last_status or "回滚" in last_status) else 'INFO'
            )
            status_box.label(text=f"上次迁移：{last_status}", icon=icon)

        # ---------- 备份 ----------
        backup_box = layout.box()
        backup_box.label(text="备份", icon='PACKAGE')
        backup_box.operator(
            "mmy.pack_portable",
            text="立即备份（全量 Portable）",
            icon='PACKAGE',
        )
        backup_box.operator(
            "mmy.backup_history",
            text="备份记录（恢复入口）",
            icon='RECOVER_LAST',
        )
        backup_box.label(text="回到旧版本 = 恢复该版本的备份", icon='INFO')

        # ---------- 迁移 ----------
        migration_box = layout.box()
        migration_box.label(text="迁移（仅同主版本、仅升级）", icon='FILE_REFRESH')
        row = migration_box.row(align=True)
        row.operator(
            "mmy.migrate_to_blender",
            text="迁移到新版",
            icon='FILE_REFRESH',
        )
        row.operator(
            "mmy.export_migration_profile",
            text="导出配置包",
            icon='EXPORT',
        )

        # ---------- 维护 ----------
        maintenance_box = layout.box()
        maintenance_box.label(text="维护", icon='SETTINGS')
        row = maintenance_box.row(align=True)
        report_row = row.row(align=True)
        has_report = bool(
            prefs and getattr(prefs, "last_migration_report", "")
        )
        report_row.enabled = has_report
        report_row.operator(
            "mmy.open_migration_report",
            text="打开报告",
            icon='TEXT',
        )
        row.operator(
            "mmy.cleanup_migration_artifacts",
            text="清理迁移残留",
            icon='TRASH',
        )


# ============================================================
# 注册到顶部菜单栏
# ============================================================

def draw_pack_button(self, context):
    layout = self.layout
    row = layout.row(align=True)
    row.scale_x = 0.85
    row.operator("mmy.open_config_manager", text="配置管理", icon="PREFERENCES")


def register():
    bpy.utils.register_class(MMY_OT_PackPortable)
    bpy.utils.register_class(MMY_OT_OpenConfigManager)
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
        bpy.utils.unregister_class(MMY_OT_OpenConfigManager)
    except Exception:
        pass
    try:
        bpy.utils.unregister_class(MMY_OT_PackPortable)
    except Exception:
        pass
