import bpy
from bpy.props import StringProperty
from pathlib import Path
from . import utils


def _get_prefs(context):
    return context.preferences.addons[__package__].preferences


class MMY_OT_BackupConfig(bpy.types.Operator):
    bl_idname = "mmy.backup_config"
    bl_label = "备份配置"
    bl_description = "将当前 Blender 配置备份为 zip 文件"

    directory: StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        prefs = _get_prefs(context)
        if prefs.backup_path:
            self.directory = prefs.backup_path
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        prefs = _get_prefs(context)
        dest = Path(self.directory)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            zip_path = utils.pack_config(
                dest,
                include_keymap=prefs.include_keymap,
                include_prefs=prefs.include_prefs,
                include_addons=prefs.include_addons,
                include_config=prefs.include_config,
                include_presets=prefs.include_presets,
                include_startup=prefs.include_startup,
                include_datafiles=prefs.include_datafiles,
            )
            self.report({'INFO'}, f"备份成功：{zip_path.name}")
        except Exception as e:
            self.report({'ERROR'}, f"备份失败：{e}")
        return {'FINISHED'}


class MMY_OT_ExportConfig(bpy.types.Operator):
    bl_idname = "mmy.export_config"
    bl_label = "导出配置"
    bl_description = "将配置导出到指定目录"

    directory: StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        prefs = _get_prefs(context)
        dest = Path(self.directory)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            zip_path = utils.pack_config(
                dest,
                include_keymap=prefs.include_keymap,
                include_prefs=prefs.include_prefs,
                include_addons=prefs.include_addons,
                include_config=prefs.include_config,
                include_presets=prefs.include_presets,
                include_startup=prefs.include_startup,
                include_datafiles=prefs.include_datafiles,
            )
            self.report({'INFO'}, f"导出成功：{zip_path}")
        except Exception as e:
            self.report({'ERROR'}, f"导出失败：{e}")
        return {'FINISHED'}


class MMY_OT_ImportConfig(bpy.types.Operator):
    bl_idname = "mmy.import_config"
    bl_label = "导入配置"
    bl_description = "从 zip 文件导入 Blender 配置"

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.zip", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        prefs = _get_prefs(context)
        try:
            manifest = utils.unpack_config(
                Path(self.filepath),
                include_keymap=prefs.include_keymap,
                include_prefs=prefs.include_prefs,
                include_addons=prefs.include_addons,
                include_config=prefs.include_config,
                include_presets=prefs.include_presets,
                include_startup=prefs.include_startup,
                include_datafiles=prefs.include_datafiles,
            )
            self._check_version(manifest)
            self.report({'INFO'}, "导入成功，请重启 Blender 生效")
        except Exception as e:
            self.report({'ERROR'}, f"导入失败：{e}")
        return {'FINISHED'}

    def _check_version(self, manifest):
        backup_ver = manifest.get("blender_version", "")
        current_ver = ".".join(str(v) for v in bpy.app.version)
        if not backup_ver:
            return
        backup_major = backup_ver.split(".")[0]
        current_major = current_ver.split(".")[0]
        if backup_major != current_major:
            self.report(
                {'WARNING'},
                f"版本不匹配：备份来自 Blender {backup_ver}，当前为 {current_ver}，可能存在兼容性问题"
            )


classes = (MMY_OT_BackupConfig, MMY_OT_ExportConfig, MMY_OT_ImportConfig)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
