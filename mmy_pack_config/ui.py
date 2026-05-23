import bpy
from . import addon_timer
from .utils import is_portable_mode, detect_path_dependencies
from pathlib import Path


class MMY_OT_AddBookmark(bpy.types.Operator):
    """添加当前路径到书签"""
    bl_idname = "mmy.add_bookmark"
    bl_label = "添加书签"
    bl_description = "将当前备份路径收藏到书签列表"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        current_path = prefs.backup_path

        if not current_path:
            self.report({'WARNING'}, "请先设置备份路径")
            return {'CANCELLED'}

        # 检查是否已存在
        for item in prefs.bookmarks:
            if item.path == current_path:
                self.report({'INFO'}, "此路径已在书签中")
                return {'CANCELLED'}

        # 添加新书签
        new_bookmark = prefs.bookmarks.add()
        new_bookmark.path = current_path
        # 使用路径最后一级目录名作为默认名称
        new_bookmark.name = Path(current_path).name or "书签"

        self.report({'INFO'}, f"已添加书签: {new_bookmark.name}")
        return {'FINISHED'}


class MMY_OT_SelectBookmark(bpy.types.Operator):
    """从书签中选择路径"""
    bl_idname = "mmy.select_bookmark"
    bl_label = "选择书签路径"
    bl_description = "从收藏的书签中选择一个路径"
    bl_options = {'REGISTER', 'UNDO'}

    bookmark_index: bpy.props.IntProperty()

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        if self.bookmark_index < len(prefs.bookmarks):
            prefs.backup_path = prefs.bookmarks[self.bookmark_index].path
            self.report({'INFO'}, f"已选择: {prefs.bookmarks[self.bookmark_index].name}")
        return {'FINISHED'}


class MMY_OT_RemoveBookmark(bpy.types.Operator):
    """删除书签"""
    bl_idname = "mmy.remove_bookmark"
    bl_label = "删除书签"
    bl_description = "从书签列表中删除此项"
    bl_options = {'REGISTER', 'UNDO'}

    bookmark_index: bpy.props.IntProperty()

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        if self.bookmark_index < len(prefs.bookmarks):
            prefs.bookmarks.remove(self.bookmark_index)
            # 调整索引
            if prefs.bookmark_index >= len(prefs.bookmarks):
                prefs.bookmark_index = max(0, len(prefs.bookmarks) - 1)
        return {'FINISHED'}


class MMY_OT_SelectBackupPath(bpy.types.Operator):
    """选择备份保存位置"""
    bl_idname = "mmy.select_backup_path"
    bl_label = "选择备份路径"
    bl_description = "选择备份文件的默认保存目录"
    bl_options = {'REGISTER'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        prefs.backup_path = self.directory
        return {'FINISHED'}


class MMY_OT_ShowHelp(bpy.types.Operator):
    """显示详细使用说明"""
    bl_idname = "mmy.show_help"
    bl_label = "使用帮助"
    bl_description = "查看详细使用说明和 Portable 模式迁移指南"
    bl_options = {'REGISTER'}

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        layout = self.layout

        # 功能说明
        box = layout.box()
        box.label(text="功能说明", icon='INFO')
        col = box.column(align=True)
        col.label(text="• 备份：保存当前 Blender 配置为 zip 文件")
        col.label(text="• 导入：从 zip 文件恢复配置（会覆盖现有配置）")
        col.label(text="• 导出：将配置保存到指定位置（便于分享给他人）")

        # Portable 模式说明
        box = layout.box()
        box.label(text="Portable 模式迁移说明", icon='QUESTION')
        col = box.column(align=True)
        col.label(text="• Portable → 普通：配置导入到系统用户目录")
        col.label(text="• 普通 → Portable：配置导入到 portable 目录")
        col.label(text="• 跨模式迁移时，书签、资产库路径可能需要手动调整")

        # 路径依赖说明
        box = layout.box()
        box.label(text="路径依赖说明", icon='WARNING')
        col = box.column(align=True)
        col.label(text="• 书签、资产库、最近文件等包含绝对路径")
        col.label(text="• 迁移到不同环境后，这些路径可能失效")
        col.label(text="• 导入时会有警告提示，请检查并手动调整")


class MMY_OT_OpenConfigPanel(bpy.types.Operator):
    """打开配置管理面板"""
    bl_idname = "mmy.open_config_panel"
    bl_label = "MMY 配置管理"
    bl_description = "打开配置备份、导入、导出管理面板"
    bl_options = {'REGISTER'}

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons[__package__].preferences

        # === 环境信息区 ===
        self._draw_environment_info(layout)

        # === 备份路径设置区 ===
        self._draw_backup_path(layout, prefs)

        # === 使用说明区 ===
        self._draw_usage_guide(layout)

        # === 配置类型选择区（含路径依赖警告）===
        self._draw_config_options(layout, prefs)

        # === 操作按钮区 ===
        self._draw_action_buttons(layout)

        # === 插件耗时监控区 ===
        self._draw_addon_timer(layout)

    def _draw_environment_info(self, layout):
        """绘制运行环境信息区"""
        box = layout.box()

        # 版本号
        version = ".".join(str(v) for v in bpy.app.version)

        # 模式状态
        portable = is_portable_mode()
        mode_text = "Portable 模式" if portable else "普通模式"

        # 配置路径
        config_path = bpy.utils.user_resource('CONFIG')

        row = box.row()
        row.label(text=f"Blender {version} | {mode_text}", icon='INFO')

        row = box.row()
        row.label(text=f"配置路径: {config_path}", icon='FILE_FOLDER')

    def _draw_backup_path(self, layout, prefs):
        """绘制备份路径设置区，含书签功能"""
        box = layout.box()

        # 路径输入和按钮
        row = box.row(align=True)
        row.prop(prefs, "backup_path", text="备份保存位置")
        # 书签按钮（收藏当前路径）
        row.operator("mmy.add_bookmark", text="", icon='BOOKMARKS')

        # 书签列表
        if prefs.bookmarks:
            col = box.column(align=True)
            col.label(text="收藏路径:", icon='BOOKMARKS')
            for i, bookmark in enumerate(prefs.bookmarks):
                row = col.row(align=True)
                # 选择书签按钮
                op = row.operator("mmy.select_bookmark", text=bookmark.name, icon='FILE_FOLDER')
                op.bookmark_index = i
                # 删除书签按钮
                op = row.operator("mmy.remove_bookmark", text="", icon='X')
                op.bookmark_index = i

    def _draw_usage_guide(self, layout):
        """绘制使用说明区"""
        box = layout.box()
        box.label(text="使用说明:", icon='HELP')

        col = box.column(align=True)
        col.label(text="• 备份 → 保存当前配置到 zip 文件")
        col.label(text="• 导入 → 从 zip 恢复配置（会覆盖现有配置）")
        col.label(text="• 导出 → 保存配置到指定位置（便于分享）")

    def _draw_config_options(self, layout, prefs):
        """绘制配置类型选项区，含路径依赖警告"""
        box = layout.box()
        box.label(text="选择要备份/导入的配置类型")

        # 检测路径依赖
        path_deps = detect_path_dependencies()

        box.prop(prefs, "include_keymap")
        box.prop(prefs, "include_prefs")
        box.prop(prefs, "include_addons")

        # 用户配置项
        row = box.row()
        row.prop(prefs, "include_config")

        # 路径依赖警告（更清晰的提示）
        if prefs.include_config and path_deps:
            # 使用单独的 box 显示警告，而不是挤在一起
            warning_box = box.box()
            warning_row = warning_box.row()
            warning_row.alert = True
            warning_row.label(text="⚠ 路径依赖检测", icon='ERROR')
            warning_col = warning_box.column(align=True)
            warning_col.label(text=f"检测到: {', '.join(path_deps)}")
            warning_col.label(text="迁移后这些路径可能失效，需手动检查")

        box.prop(prefs, "include_presets")
        box.prop(prefs, "include_startup")
        box.prop(prefs, "include_datafiles")

    def _draw_action_buttons(self, layout):
        """绘制操作按钮区"""
        layout.separator()
        row = layout.row(align=True)
        row.operator("mmy.backup_config", text="备份", icon='FILE_TICK')
        row.operator("mmy.import_config", text="导入", icon='IMPORT')
        row.operator("mmy.export_config", text="导出", icon='EXPORT')

        # 帮助按钮（右侧）
        row.operator("mmy.show_help", text="", icon='QUESTION')

    def _draw_addon_timer(self, layout):
        """绘制插件耗时监控区"""
        records = addon_timer.manager.get_records()
        if records:
            layout.separator()
            box = layout.box()
            box.label(text="插件加载耗时", icon='TIME')
            for rec in records:
                row = box.row()
                row.label(text=f"{rec.name}  {rec.elapsed * 1000:.1f}ms")
                if rec.error:
                    row.alert = True
                    row.label(text="ERROR", icon='ERROR')


def draw_header_button(self, context):
    """顶部栏按钮绘制"""
    layout = self.layout
    row = layout.row(align=True)
    row.operator("mmy.open_config_panel", text="", icon='WORDWRAP_ON')


classes = (
    MMY_OT_AddBookmark,
    MMY_OT_SelectBookmark,
    MMY_OT_RemoveBookmark,
    MMY_OT_SelectBackupPath,
    MMY_OT_ShowHelp,
    MMY_OT_OpenConfigPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_HT_upper_bar.prepend(draw_header_button)


def unregister():
    bpy.types.TOPBAR_HT_upper_bar.remove(draw_header_button)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)