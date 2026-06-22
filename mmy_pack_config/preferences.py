import bpy
import os
from pathlib import Path
from .addon_timer import manager


# ============================================================
# 偏好设置面板：耗时监控显示 + 打包输出路径设置
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

    # ---------- 绘制面板 ----------
    def draw(self, context):
        layout = self.layout

        # --- 区块1：打包输出路径 ---
        box = layout.box()
        box.label(text="打包设置", icon='FILE_FOLDER')
        box.prop(self, "pack_output_path")

        # --- 区块2：插件加载耗时监控 ---
        box = layout.box()
        box.label(text="插件加载耗时", icon='TIME')

        records = manager.get_records()

        if not records:
            # 无数据时的提示
            col = box.column()
            col.label(text="暂无监控数据", icon='INFO')
            col.label(text="重启 Blender 或重新启用此插件后可开始监控", icon='BLANK1')

            # 尝试从历史文件加载
            if manager.load_data():
                records = manager.get_records()
                if records:
                    box.separator()
                    box.label(text="已加载上一次的监控数据:", icon='MEMORY')
                else:
                    return
            else:
                return

        # 表头
        row = box.row()
        row.label(text="插件名称", icon='PLUGIN')
        row.label(text="耗时")
        row.label(text="状态")

        # 按耗时排序，显示前 30 个（0.0s 的排后面）
        sorted_records = sorted(
            records,
            key=lambda r: (r.elapsed == 0.0, -r.elapsed),
        )[:30]

        for rec in sorted_records:
            row = box.row()
            # 截断过长的名称
            name = rec.name.replace("addon_utils: ", "")[:40]
            row.label(text=name)

            # 耗时显示
            if rec.elapsed > 0:
                elapsed_str = f"{rec.elapsed:.3f}s"
            else:
                elapsed_str = "—"

            # 状态判断
            if rec.error:
                row.alert = True
                row.label(text=elapsed_str, icon='ERROR')
                row.alert = True
                row.label(text="异常", icon='CANCEL')
            elif rec.elapsed == 0:
                row.label(text=elapsed_str, icon='TIME')
                row.label(text="早期加载", icon='INFO')
            elif rec.elapsed > 1.0:
                row.alert = True
                row.label(text=elapsed_str, icon='ERROR')
                row.alert = False
                row.label(text="慢", icon='SORTTIME')
            elif rec.elapsed > 0.2:
                row.label(text=elapsed_str, icon='TIME')
                row.label(text="较慢", icon='SORTTIME')
            else:
                row.label(text=elapsed_str, icon='CHECKMARK')
                row.label(text="正常", icon='CHECKMARK')

        # 统计信息
        box.separator()
        total = len(records)
        timed = sum(1 for r in records if r.elapsed > 0)
        errors = sum(1 for r in records if r.error)
        avg_time = (
            sum(r.elapsed for r in records if r.elapsed > 0) / timed
            if timed > 0 else 0
        )

        info_text = (
            f"总计 {total} 个 | 已计时 {timed} 个 | "
            f"平均 {avg_time:.3f}s | 异常 {errors} 个"
        )
        box.label(text=info_text)


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
