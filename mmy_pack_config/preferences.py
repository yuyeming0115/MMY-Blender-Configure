import bpy
import os
from pathlib import Path
from .addon_timer import manager


# ============================================================
# 偏好设置面板：耗时监控显示 + 打包输出路径设置 + pack.py 路径配置
# ============================================================

class MMYConfigPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    # ---------- 打包输出路径（供 pack.py 读取） ----------
    pack_output_path: bpy.props.StringProperty(
        name="打包输出目录",
        description="pack.py 打包时的默认输出目录。留空则每次手动选择",
        subtype='DIR_PATH',
        default="",
        update=lambda self, ctx: _on_pack_output_path_changed(self),
    )

    # ---------- pack.py 路径（用于菜单栏按钮） ----------
    pack_script_path: bpy.props.StringProperty(
        name="pack.py 路径",
        description="pack.py 的完整路径。留空则自动搜索（向上查找或使用常用目录）",
        subtype='FILE_PATH',
        default="",
    )

    # ---------- 展开控制（UI 状态，不保存到文件） ----------
    show_early_addons: bpy.props.BoolProperty(
        name="显示早期加载的插件列表",
        description="展开查看所有早期加载插件的详细列表（默认折叠以节省空间）",
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

        # ========== 区块2：插件加载耗时监控 ==========
        box = layout.box()
        box.label(text="插件加载耗时", icon='TIME')

        records = manager.get_records()

        if not records:
            # 无数据时尝试加载历史
            loaded = manager.load_data()
            if not loaded:
                row = box.row()
                row.label(text="暂无数据", icon='INFO')
                row.label(text="重启 Blender 或重新启用此插件后可开始监控", icon='BLANK1')
                return
            records = manager.get_records()

        # ---- 分类统计 ----
        timed = [r for r in records if r.elapsed > 0]
        early = [r for r in records if r.elapsed == 0 and not r.error]
        errors_list = [r for r in records if r.error]

        # ---- 统计摘要行 ----
        summary_row = box.row(align=True)
        _draw_stat(summary_row, f"总计 {len(records)} 个", 'INFO')
        _draw_stat(summary_row, f"已计时 {len(timed)} 个", 'CHECKMARK' if timed else 'BLANK1')
        _draw_stat(summary_row, f"早期加载 {len(early)} 个", 'TIME')
        if errors_list:
            summary_row.alert = True
            _draw_stat(summary_row, f"异常 {len(errors_list)} 个", 'ERROR')
            summary_row.alert = False

        # ---- 有计时数据的插件（重点展示）----
        if timed:
            box.separator(factor=0.3)
            box.label(text="已计时插件（按耗时排序）:", icon='SORTSIZE')

            sorted_timed = sorted(timed, key=lambda r: -r.elapsed)
            for rec in sorted_timed[:15]:  # 最多显示 15 个有计时的
                self._draw_record_row(box, rec)

            if len(sorted_timed) > 15:
                box.label(text=f"... 还有 {len(sorted_timed) - 15} 个", icon='BLANK1')

        # ---- 异常插件 ----
        if errors_list:
            box.separator(factor=0.3)
            box.label(text="异常插件:", icon='ERROR', icon_color=(1, 0.3, 0.2))
            for rec in errors_list[:5]:
                self._draw_error_row(box, rec)

        # ---- 早期加载的插件（默认折叠）----
        if early:
            box.separator(factor=0.3)
            row = box.row(align=True)
            row.prop(self, "show_early_addons", text="", icon='DISCLOSURE_TRI_RIGHT' if not self.show_early_addons else 'DISCLOSURE_TRI_DOWN', emboss=False)

            # 早期插件摘要行（始终显示）
            early_names = [r.name.replace("addon_utils: ", "") for r in early]
            row.label(text=f"{len(early)} 个早期加载的插件（注入前已完成加载）", icon='INFO')

            if len(early) <= 8 and not self.show_early_addons:
                # 少量时直接显示名字，不用展开
                names_str = ", ".join(early_names[:8])
                if len(early) > 8:
                    names_str += "..."
                sub = box.column()
                sub.label(text=names_str, icon='BLANK1')

            elif self.show_early_addons:
                # 展开时显示完整列表（紧凑格式）
                sub = box.column(align=True)
                for i, rec in enumerate(early):
                    name = rec.name.replace("addon_utils: ", "")[:45]
                    sub.label(text=f"  {name}")

    @staticmethod
    def _draw_record_row(box, rec):
        """绘制一条有计时数据的记录"""
        row = box.row(align=True)
        name = rec.name.replace("addon_utils: ", "")[:38]

        row.label(text=name)

        # 根据耗时着色
        if rec.elapsed > 1.0:
            row.alert = True
            row.label(text=f"{rec.elapsed:.2f}s", icon='ERROR')
            row.alert = False
        elif rec.elapsed > 0.3:
            row.label(text=f"{rec.elapsed:.2f}s", icon='SORTTIME')
        else:
            row.label(text=f"{rec.elapsed:.3f}s", icon='CHECKMARK')

    @staticmethod
    def _draw_error_row(box, rec):
        """绘制异常记录"""
        row = box.row(align=True)
        name = rec.name.replace("addon_utils: ", "")[:35]
        row.alert = True
        row.label(text=name, icon='CANCEL')
        row.label(text=rec.error.split("\n")[-1][:30] if rec.error else "Error", icon='ERROR')
        row.alert = False


def _draw_stat(row, text, icon):
    """辅助：绘制统计标签"""
    row.label(text=text, icon=icon)


# ============================================================
# 当 pack_output_path 变化时，同步写入 .pack_config.json
# ============================================================
def _on_pack_output_path_changed(self):
    """用户修改「打包输出目录」时，写入 .pack_config.json"""
    import json

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
