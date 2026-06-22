import bpy
import os
from pathlib import Path
from .addon_timer import manager


# ============================================================
# 偏好设置面板：只保留耗时监控显示 + 打包输出路径设置
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
            box.label(text="暂无数据（请重启 Blender 以开始监控）", icon='INFO')
            return

        # 表头
        row = box.row()
        row.label(text="插件名称", icon='PLUGIN')
        row.label(text="耗时 (s)")

        # 按耗时排序，显示前 30 个
        sorted_records = sorted(records, key=lambda r: r.elapsed, reverse=True)[:30]

        for rec in sorted_records:
            row = box.row()
            name = rec.name.replace("addon_utils: ", "")
            row.label(text=name)

            # 耗时显示：正常 / 警告 / 错误
            elapsed_str = f"{rec.elapsed:.3f}"
            if rec.error:
                row.alert = True
                row.label(text=elapsed_str, icon='ERROR')
            elif rec.elapsed > 1.0:
                row.alert = True
                row.label(text=elapsed_str, icon='ERROR')
            elif rec.elapsed > 0.2:
                row.label(text=elapsed_str, icon='ERROR')
            else:
                row.label(text=elapsed_str, icon='CHEC/MARK')

        # 统计信息
        box.separator()
        total = len(records)
        errors = sum(1 for r in records if r.error)
        avg_time = sum(r.elapsed for r in records) / total if total else 0

        col = box.column()
        col.label(text=f"总计 {total} 个插件 | 平均 {avg_time:.3f}s | 错误 {errors} 个")


# ============================================================
# 当 pack_output_path 变化时，同步写入 .pack_config.json
# ============================================================
def _on_pack_output_path_changed(self):
    """用户修改「打包输出目录」时，写入 .pack_config.json，
    这样 pack.py（独立脚本）就能读取到这个路径。"""
    import json

    config_path = Path(__file__).parent.parent / ".pack_config.json"

    # 读取现有配置（如果存在）
    config = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass

    # 更新 last_output_dir
    output_path = self.pack_output_path
    if output_path and Path(output_path).exists():
        config["last_output_dir"] = output_path

    # 写回文件
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
