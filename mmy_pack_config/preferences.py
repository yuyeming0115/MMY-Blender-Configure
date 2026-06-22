"""
MMY Blender Configure — 偏好设置面板

功能：
  1. 打包输出路径设置（同步到 .pack_config.json）
  2. 插件加载耗时监控（双列彩虹色展示）
"""

import bpy
import json
from pathlib import Path
from .addon_timer import manager


# ============================================================
# 彩虹色时间标签：根据耗时返回 (图标, 是否alert)
# ============================================================

def _time_tier(elapsed):
    """耗时分级 → (icon, alert, label)"""
    if elapsed <= 0:
        return "BLANK1", False, "--"
    elif elapsed < 0.05:
        return "CHECKMARK", False, "极速"
    elif elapsed < 0.15:
        return "CHECKMARK", None, "快"          # None = 不设 alert，正常显示
    elif elapsed < 0.4:
        return "SORTTIME", None, "较慢"
    elif elapsed < 1.0:
        return "ERROR", True, "慢"
    else:
        return "CANCEL", True, "很慢"


def _fmt_time(elapsed):
    """格式化耗时字符串"""
    if elapsed <= 0:
        return "--"
    elif elapsed >= 1.0:
        return f"{elapsed:.2f}s"
    elif elapsed >= 0.01:
        return f"{elapsed:.3f}s"
    else:
        return f"{elapsed:.4f}s"


class MMYConfigPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    # ---------- 打包路径记忆 ----------
    last_portable_path: bpy.props.StringProperty(
        name="上次 Portable 路径",
        description="上次打包时选择的 portable 文件夹（自动记录）",
        subtype='DIR_PATH',
        default="",
    )

    # ---------- 打包输出路径 ----------
    pack_output_path: bpy.props.StringProperty(
        name="打包输出目录",
        description="打包时的默认输出目录。留空则每次手动选择",
        subtype='DIR_PATH',
        default="",
        update=lambda self, ctx: _on_pack_output_path_changed(self),
    )

    # ---------- 展开控制 ----------
    show_early_addons: bpy.props.BoolProperty(
        name="显示早期加载插件列表",
        default=False,
    )

    # ---------- 绘制面板 ----------
    def draw(self, context):
        layout = self.layout

        # ========== 区块1：打包设置 ==========
        box = layout.box()
        box.label(text="打包设置", icon='FILE_FOLDER')
        col = box.column(align=True)
        col.prop(self, "last_portable_path")
        col.prop(self, "pack_output_path")

        # ========== 区块2：插件加载耗时（双列彩虹色）==========
        self._draw_timer_panel(layout)

    # ---- 耗时监控面板（双列彩虹色） ----
    def _draw_timer_panel(self, layout):
        """双列布局 + 彩虹色表示耗时长短"""
        box = layout.box()
        box.label(text="插件加载耗时", icon='TIME')

        records = manager.get_records()

        if not records:
            loaded = manager.load_data()
            if not loaded:
                row = box.row(align=True)
                row.label(text="暂无数据", icon='INFO')
                row.label(text="重启 Blender 后开始监控", icon='BLANK1')
                return
            records = manager.get_records()

        # 分类
        timed = [r for r in records if r.elapsed > 0]
        early = [r for r in records if r.elapsed == 0 and not r.error]
        errors_list = [r for r in records if r.error]

        # ---- 统计摘要行 ----
        row = box.row(align=True)
        _chip(row, f"总计 {len(records)}", 'INFO')
        _chip(row, f"计时 {len(timed)}", 'CHECKMARK' if timed else 'BLANK1')
        if errors_list:
            row.alert = True
            _chip(row, f"异常 {len(errors_list)}", 'ERROR')
            row.alert = False

        # ---- 有计时数据：双列彩虹色 ----
        if timed:
            box.separator(factor=0.3)
            sorted_timed = sorted(timed, key=lambda r: -r.elapsed)

            split = box.split(factor=0.5)
            col_left = split.column(align=True)
            col_right = split.column(align=True)

            for i, rec in enumerate(sorted_timed[:30]):
                target = col_left if i % 2 == 0 else col_right
                self._draw_rainbow_cell(target, rec, rank=i + 1)

            if len(sorted_timed) > 30:
                sub = box.column()
                sub.label(text=f"... 还有 {len(sorted_timed) - 30} 个", icon='BLANK1')

        # ---- 异常插件 ----
        if errors_list:
            box.separator(factor=0.3)
            box.label(text="异常插件:", icon='ERROR')
            for rec in errors_list[:5]:
                self._draw_error_cell(box, rec)

        # ---- 早期加载（折叠）----
        if early:
            box.separator(factor=0.3)
            hdr = box.row(align=True)
            hdr.prop(self, "show_early_addons", text="",
                     icon='DISCLOSURE_TRI_RIGHT' if not self.show_early_addons else 'DISCLOSURE_TRI_DOWN',
                     emboss=False)
            hdr.label(text=f"{len(early)} 个早期加载（注入前已完成）", icon='INFO')

            if self.show_early_addons or len(early) <= 12:
                names = [r.name.replace("addon_utils: ", "") for r in early]
                split = box.split(factor=0.5)
                cA, cB = split.column(align=True), split.column(align=True)
                for i, n in enumerate(names):
                    (cA if i % 2 == 0 else cB).label(text=n, icon='DOT')

    @staticmethod
    def _draw_rainbow_cell(col, rec, rank=0):
        """
        单个单元格：图标 | 名称(截断) | 耗时 | 状态标识
        彩虹色通过 icon + alert 组合表达：
           🟢 极速/快 → CHECKMARK
           🟡 较慢   → SORTTIME
           🔴 慢     → ERROR (alert红)
           🔴 很慢   → CANCEL  (alert红)
        """
        name = rec.name.replace("addon_utils: ", "")[:22]
        icon, alert, tier_label = _time_tier(rec.elapsed)
        time_str = _fmt_time(rec.elapsed)

        row = col.row(align=True)

        # 时间数字作为主要视觉指标（alert 让慢的变红）
        if alert:
            row.alert = True
        row.label(text=time_str, icon=icon)
        if alert:
            row.alert = False

        # 名称
        row.label(text=name, icon='BLANK1')

    @staticmethod
    def _draw_error_cell(col, rec):
        """异常记录行"""
        name = rec.name.replace("addon_utils: ", "")[:28]
        err_msg = rec.error.split("\n")[-1][:35] if rec.error else "Error"

        row = col.row(align=True)
        row.alert = True
        row.label(text=name, icon='CANCEL')
        row.label(text=err_msg, icon='ERROR')
        row.alert = False


def _chip(row, text, icon):
    """辅助：绘制统计标签"""
    row.label(text=text, icon=icon)


# ============================================================
# pack_output_path 变化时同步写入 .pack_config.json
# ============================================================
def _on_pack_output_path_changed(self):
    config_path = Path(__file__).parent.parent / ".pack_config.json"

    config = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass

    output_path = self.pack_output_path
    if output_path and Path(output_path).exists():
        config["last_output_dir"] = output_path

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[MMY] 无法写入 .pack_config.json: {e}")


# ============================================================
# 注册 / 注销
# ============================================================
classes = (MMYConfigPreferences,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
