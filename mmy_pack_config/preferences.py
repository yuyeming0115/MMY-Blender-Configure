"""
MMY Blender Configure — 偏好设置面板

功能：
  1. 打包输出路径设置（同步到 .pack_config.json）
  2. pack.py 路径配置（供菜单栏按钮使用）
  3. 插件加载耗时监控（天梯条形图展示）
"""

import bpy
import json
from pathlib import Path
from .addon_timer import manager


# ============================================================
# 常量：条形图参数
# ============================================================
BAR_WIDTH = 18          # 条形最大字符宽度
BAR_FULL = "\u2588"      # █ 实心块
BAR_EMPTY = "\u2591"     # ░ 浅色块（或用空格）


class MMYConfigPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    # ---------- 打包输出路径 ----------
    pack_output_path: bpy.props.StringProperty(
        name="打包输出目录",
        description="pack.py 打包时的默认输出目录。留空则每次手动选择",
        subtype='DIR_PATH',
        default="",
        update=lambda self, ctx: _on_pack_output_path_changed(self),
    )

    # ---------- pack.py 路径 ----------
    pack_script_path: bpy.props.StringProperty(
        name="pack.py 路径",
        description="菜单栏「打包导出」按钮使用的脚本路径。留空则自动搜索",
        subtype='FILE_PATH',
        default="",
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
        col.prop(self, "pack_script_path")
        col.prop(self, "pack_output_path")

        # ========== 区块2：插件加载耗时天梯图 ==========
        self._draw_timer_panel(layout)

    # ---- 耗时监控面板 ----
    def _draw_timer_panel(self, layout):
        """绘制天梯图风格的耗时监控面板"""
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

        max_elapsed = max(r.elapsed for r in timed) if timed else 1.0

        # ---- 统计摘要行（3 列自适应）----
        row = box.row(align=True)
        _draw_stat_chip(row, f"总计 {len(records)}", 'INFO', (0.15, 0.15, 0.17))
        _draw_stat_chip(row, f"计时 {len(timed)}", 'CHECKMARK' if timed else 'BLANK1', (0.12, 0.35, 0.14))
        if errors_list:
            row.alert = True
            _draw_stat_chip(row, f"异常 {len(errors_list)}", 'ERROR', (0.85, 0.20, 0.18))
            row.alert = False

        # ---- 有计时数据的天梯图 ----
        if timed:
            box.separator(factor=0.4)
            sorted_timed = sorted(timed, key=lambda r: -r.elapsed)
            display_count = min(len(sorted_timed), 25)

            for i, rec in enumerate(sorted_timed[:display_count]):
                self._draw_bar_row(box, rec, max_elapsed, rank=i + 1)

            if len(sorted_timed) > display_count:
                sub = box.column()
                sub.label(
                    text=f"... 还有 {len(sorted_timed) - display_count} 个已计时插件",
                    icon='BLANK1'
                )

        # ---- 异常插件 ----
        if errors_list:
            box.separator(factor=0.4)
            box.label(text="异常插件:", icon='ERROR')
            for rec in errors_list[:5]:
                self._draw_error_bar_row(box, rec)

        # ---- 早期加载的插件（折叠摘要）----
        if early:
            box.separator(factor=0.4)
            header_row = box.row(align=True)
            header_row.prop(self, "show_early_addons", text="", icon='DISCLOSURE_TRI_RIGHT' if not self.show_early_addons else 'DISCLOSURE_TRI_DOWN', emboss=False)
            header_row.label(
                text=f"{len(early)} 个早期加载（注入前已完成，无耗时数据）",
                icon='INFO'
            )

            if self.show_early_addons or len(early) <= 10:
                # 少量或展开时显示名字网格
                sub = box.column(align=True)
                names = [r.name.replace("addon_utils: ", "") for r in early]
                # 按每行 3 个排列
                for i in range(0, len(names), 3):
                    line_names = names[i:i + 3]
                    line_text = "  |  ".join(line_names)
                    sub.label(text=line_text, icon='BLANK1')

    @staticmethod
    def _draw_bar_row(box, rec, max_elapsed, rank=0):
        """
        绘制一条天梯条形行：
          排名  插件名          [████████░░░░░░]  耗时
        """
        name = rec.name.replace("addon_utils: ", "")[:30]

        # 计算条形长度
        ratio = min(rec.elapsed / max_elapsed, 1.0) if max_elapsed > 0 else 0
        filled = int(ratio * BAR_WIDTH)
        empty = BAR_WIDTH - filled
        bar = BAR_FULL * filled + ("." * empty)  # 用 . 作为空白部分更轻量

        # 格式化耗时
        if rec.elapsed >= 1.0:
            time_str = f"{rec.elapsed:.2f}s"
        elif rec.elapsed >= 0.01:
            time_str = f"{rec.elapsed:.3f}s"
        else:
            time_str = f"{rec.elapsed:.4f}s"

        # 绘制行
        row = box.row(align=True)

        # 排名号
        rank_str = f"{rank:>2}" if rank else "  "
        row.label(text=rank_str, icon='BLANK1')

        # 插件名（固定宽度区域，避免列错位）
        row.label(text=name, icon='BLANK1')

        # 条形图 + 耗时数字
        row.label(text=f"[{bar}]  {time_str}", icon='BLANK1')

        # 状态图标（根据耗时着色）
        if rec.error:
            row.alert = True
            row.label(text="", icon='CANCEL')
            row.alert = False
        elif rec.elapsed > 1.0:
            row.alert = True
            row.label(text="", icon='ERROR')
            row.alert = False
        elif rec.elapsed > 0.3:
            row.label(text="", icon='SORTTIME')
        else:
            row.label(text="", icon='CHECKMARK')

    @staticmethod
    def _draw_error_bar_row(box, rec):
        """绘制异常记录行"""
        name = rec.name.replace("addon_utils: ", "")[:35]
        err_msg = rec.error.split("\n")[-1][:40] if rec.error else "Error"

        row = box.row(align=True)
        row.alert = True
        row.label(text=name, icon='CANCEL')
        row.label(text=f"[!!! ERROR !!!]", icon='ERROR')
        row.label(text=err_msg, icon='BLANK1')
        row.alert = False


def _draw_stat_chip(row, text, icon, color_hint=None):
    """辅助：绘制统计标签芯片"""
    row.label(text=text, icon=icon)


# ============================================================
# 当 pack_output_path 变化时，同步写入 .pack_config.json
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
