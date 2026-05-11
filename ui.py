import bpy
from . import addon_timer


class MMY_OT_OpenConfigPanel(bpy.types.Operator):
    bl_idname = "mmy.open_config_panel"
    bl_label = "MMY 配置管理"

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons[__package__].preferences

        box = layout.box()
        box.label(text="选择配置类型")
        box.prop(prefs, "include_keymap")
        box.prop(prefs, "include_prefs")
        box.prop(prefs, "include_addons")
        box.prop(prefs, "include_config")
        box.prop(prefs, "include_presets")
        box.prop(prefs, "include_startup")
        box.prop(prefs, "include_datafiles")

        layout.separator()
        row = layout.row(align=True)
        row.operator("mmy.backup_config", text="备份", icon='FILE_TICK')
        row.operator("mmy.import_config", text="导入", icon='IMPORT')
        row.operator("mmy.export_config", text="导出", icon='EXPORT')

        records = addon_timer.manager.get_records()
        if records:
            layout.separator()
            layout.label(text="插件加载耗时", icon='TIME')
            for rec in records:
                row = layout.row()
                row.label(text=f"{rec.name}  {rec.elapsed * 1000:.1f}ms")
                if rec.error:
                    row.alert = True
                    row.label(text="ERROR", icon='ERROR')


def draw_header_button(self, context):
    self.layout.operator("mmy.open_config_panel", text="", icon='WORDWRAP_ON')


def register():
    bpy.utils.register_class(MMY_OT_OpenConfigPanel)
    bpy.types.TOPBAR_HT_upper_bar.prepend(draw_header_button)


def unregister():
    bpy.types.TOPBAR_HT_upper_bar.remove(draw_header_button)
    bpy.utils.unregister_class(MMY_OT_OpenConfigPanel)
