"""Blender 跨版本配置迁移的 Operator 与当前会话采集逻辑。"""

import json
import os
import queue
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import addon_utils
import bpy

from . import utils
from .migration_core import (
    MigrationError,
    SourceSnapshot,
    create_profile,
    ensure_external_output,
    execute_existing_profile_migration,
    execute_migration,
    restore_recovery,
    suggest_blender_executable,
)


_WORKER_SCRIPT = Path(__file__).parent / "migration_worker.py"
_JOB_LOCK = threading.Lock()


def _get_preferences(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _default_output_dir(preferences) -> str:
    configured = getattr(preferences, "pack_output_path", "") if preferences else ""
    if configured:
        return configured
    return str(Path.home() / "Desktop")


def _user_resource_path(resource_type: str, user_root: Path, fallback_name: str) -> Path:
    value = bpy.utils.user_resource(resource_type)
    return Path(value) if value else user_root / fallback_name


def _version_list(value) -> list[int]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(".")
    result = []
    for part in value:
        try:
            result.append(int(part))
        except (TypeError, ValueError):
            break
    return result[:3]


def _find_extension_manifest(module) -> dict:
    module_file = getattr(module, "__file__", "")
    if not module_file:
        return {}
    current = Path(module_file).resolve(strict=False).parent
    for _ in range(4):
        manifest_path = current / "blender_manifest.toml"
        if manifest_path.is_file():
            try:
                import tomllib

                with manifest_path.open("rb") as handle:
                    return tomllib.load(handle)
            except Exception:
                return {}
        if current == current.parent:
            break
        current = current.parent
    return {}


def collect_addon_inventory() -> list[dict]:
    enabled = set(bpy.context.preferences.addons.keys())
    modules = {}
    try:
        modules = {module.__name__: module for module in addon_utils.modules(refresh=False)}
    except Exception as exc:
        print(f"[MMY Migration] 读取插件清单失败: {exc}")

    inventory = []
    for module_name in sorted(enabled.union(modules), key=str.casefold):
        module = modules.get(module_name)
        bl_info = getattr(module, "bl_info", {}) if module else {}
        kind = "extension" if module_name.startswith("bl_ext.") else "legacy"
        entry = {
            "module": module_name,
            "kind": kind,
            "enabled": module_name in enabled,
            "version": _version_list(bl_info.get("version")),
            "blender_version_min": _version_list(bl_info.get("blender")),
        }
        if kind == "extension" and module is not None:
            extension_manifest = _find_extension_manifest(module)
            if extension_manifest.get("version"):
                entry["version"] = str(extension_manifest["version"])
            if extension_manifest.get("blender_version_min"):
                entry["blender_version_min"] = str(
                    extension_manifest["blender_version_min"]
                )
            if extension_manifest.get("blender_version_max"):
                entry["blender_version_max"] = str(
                    extension_manifest["blender_version_max"]
                )
        inventory.append(entry)
    return inventory


def _keymap_item_signature(keymap, item) -> str:
    fields = {
        "keymap": keymap.name,
        "space_type": keymap.space_type,
        "region_type": keymap.region_type,
        "idname": item.idname,
        "map_type": getattr(item, "map_type", "KEYBOARD"),
        "type": item.type,
        "value": item.value,
        "any": bool(item.any),
        "shift": bool(item.shift),
        "ctrl": bool(item.ctrl),
        "alt": bool(item.alt),
        "oskey": bool(item.oskey),
        "key_modifier": item.key_modifier,
        "direction": getattr(item, "direction", "ANY"),
        "repeat": bool(getattr(item, "repeat", False)),
        "active": bool(item.active),
    }
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def collect_keymap_fingerprint() -> dict:
    keyconfig = bpy.context.window_manager.keyconfigs.user
    items = set()
    if keyconfig is not None:
        for keymap in keyconfig.keymaps:
            for item in keymap.keymap_items:
                items.add(_keymap_item_signature(keymap, item))
    return {"schema_version": 1, "items": sorted(items)}


def _prepare_source_snapshot(
    context,
    output_dir: str,
    include_presets: bool,
    include_datafiles: bool,
    include_startup_scripts: bool,
    include_history: bool,
) -> tuple[SourceSnapshot, Path]:
    save_result = bpy.ops.wm.save_userpref()
    if "FINISHED" not in save_result:
        raise MigrationError("无法保存当前 Blender 偏好设置")
    user_root = Path(bpy.utils.resource_path("USER"))
    output = ensure_external_output(Path(output_dir), [user_root])
    work_dir = Path(tempfile.mkdtemp(prefix="mmy_migration_", dir=str(output)))
    keymap_export = work_dir / "keymap.py"
    keymap_fingerprint = work_dir / "keymap_fingerprint.json"
    try:
        result = bpy.ops.preferences.keyconfig_export(
            filepath=str(keymap_export),
            all=False,
        )
        if "FINISHED" not in result:
            raise MigrationError("Blender 快捷键导出失败")
        fingerprint = collect_keymap_fingerprint()
        keymap_fingerprint.write_text(
            json.dumps(fingerprint, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        binary_path = Path(getattr(bpy.app, "binary_path", "") or sys.executable)
        snapshot = SourceSnapshot(
            version=tuple(bpy.app.version[:3]),
            platform=sys.platform,
            install_mode="portable" if utils.is_portable_mode() else "normal",
            binary_path=binary_path,
            user_root=user_root,
            config_dir=_user_resource_path("CONFIG", user_root, "config"),
            scripts_dir=_user_resource_path("SCRIPTS", user_root, "scripts"),
            datafiles_dir=_user_resource_path("DATAFILES", user_root, "datafiles"),
            extensions_dir=_user_resource_path("EXTENSIONS", user_root, "extensions"),
            keymap_export_path=keymap_export,
            keymap_fingerprint_path=keymap_fingerprint,
            keymap_item_count=len(fingerprint["items"]),
            addons=collect_addon_inventory(),
            include_presets=include_presets,
            include_datafiles=include_datafiles,
            include_startup_scripts=include_startup_scripts,
            include_history=include_history,
        )
        return snapshot, work_dir
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def _set_status_text(context, value):
    try:
        context.workspace.status_text_set(value)
    except Exception:
        pass


class _AsyncOperatorMixin:
    _timer = None
    _thread = None
    _result_queue = None
    _job_reserved = False

    def _reserve_job(self):
        self._job_reserved = _JOB_LOCK.acquire(blocking=False)
        return self._job_reserved

    def _release_job(self):
        if self._job_reserved:
            self._job_reserved = False
            try:
                _JOB_LOCK.release()
            except RuntimeError:
                pass

    def _start_job(self, context, job, status_text):
        self._result_queue = queue.Queue(maxsize=1)
        result_queue = self._result_queue

        def runner():
            try:
                result_queue.put((True, job()))
            except Exception as exc:
                result_queue.put((False, exc))
            finally:
                try:
                    _JOB_LOCK.release()
                except RuntimeError:
                    pass

        try:
            self._thread = threading.Thread(target=runner, daemon=True)
            self._thread.start()
        except Exception:
            self._release_job()
            raise
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        _set_status_text(context, status_text)
        self.report({"INFO"}, status_text)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if self._thread is None:
            return {"PASS_THROUGH"}
        if event.type == "ESC":
            self.report({"WARNING"}, "迁移任务正在执行，完成前不能安全取消")
            return {"RUNNING_MODAL"}
        if event.type != "TIMER" or self._thread.is_alive():
            return {"PASS_THROUGH"}

        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        _set_status_text(context, None)
        ok, value = self._result_queue.get_nowait()
        if ok:
            try:
                self._handle_success(context, value)
            except Exception as exc:
                self.report({"ERROR"}, f"任务完成，但更新界面状态失败：{exc}")
                return {"CANCELLED"}
            return {"FINISHED"}
        try:
            self._handle_failure(context, value)
        except Exception as exc:
            print(f"[MMY Migration] 保存失败任务状态时出错: {exc}")
        message = str(value) or value.__class__.__name__
        self.report({"ERROR"}, message)
        return {"CANCELLED"}

    def _handle_success(self, context, result):
        raise NotImplementedError

    def _handle_failure(self, context, error):
        pass


class _SnapshotOptionsMixin:
    def _load_option_defaults(self, preferences):
        self.output_dir = _default_output_dir(preferences)
        if preferences:
            self.include_presets = preferences.migration_include_presets
            self.include_datafiles = preferences.migration_include_datafiles
            self.include_startup_scripts = preferences.migration_include_startup_scripts
            self.include_history = preferences.migration_include_history

    def _draw_options(self, layout):
        layout.prop(self, "output_dir")
        box = layout.box()
        box.label(text="迁移组件", icon="PREFERENCES")
        box.prop(self, "include_presets")
        box.prop(self, "include_datafiles")
        box.prop(self, "include_startup_scripts")
        box.prop(self, "include_history")

    def _snapshot(self, context):
        return _prepare_source_snapshot(
            context,
            self.output_dir,
            self.include_presets,
            self.include_datafiles,
            self.include_startup_scripts,
            self.include_history,
        )

    def _save_options(self, preferences):
        if not preferences:
            return
        preferences.pack_output_path = self.output_dir
        preferences.migration_include_presets = self.include_presets
        preferences.migration_include_datafiles = self.include_datafiles
        preferences.migration_include_startup_scripts = self.include_startup_scripts
        preferences.migration_include_history = self.include_history


class MMY_OT_MigrateToBlender(
    _AsyncOperatorMixin,
    _SnapshotOptionsMixin,
    bpy.types.Operator,
):
    bl_idname = "mmy.migrate_to_blender"
    bl_label = "迁移到新版 Blender"
    bl_description = "备份目标配置后，将当前工作环境迁移到更高的同主版本 Blender"
    bl_options = {"REGISTER"}

    output_dir: bpy.props.StringProperty(name="输出目录", subtype="DIR_PATH")
    include_presets: bpy.props.BoolProperty(name="用户预设", default=True)
    include_datafiles: bpy.props.BoolProperty(
        name="数据文件与 Studio Lights",
        default=False,
    )
    include_startup_scripts: bpy.props.BoolProperty(
        name="启动脚本（存在代码执行风险）",
        default=False,
    )
    include_history: bpy.props.BoolProperty(name="书签与最近文件", default=False)
    source_mode: bpy.props.EnumProperty(
        name="配置来源",
        items=(
            ("CURRENT", "当前 Blender", ""),
            ("PROFILE", "已有配置包", ""),
        ),
        default="CURRENT",
    )
    source_profile: bpy.props.StringProperty(
        name="配置包",
        subtype="FILE_PATH",
    )
    target_executable: bpy.props.StringProperty(
        name="目标 blender.exe",
        subtype="FILE_PATH",
    )
    audit_timeout: bpy.props.IntProperty(
        name="验证超时（秒）",
        default=300,
        min=60,
        max=1800,
    )

    def invoke(self, context, event):
        preferences = _get_preferences(context)
        self._load_option_defaults(preferences)
        remembered = getattr(preferences, "last_target_blender", "") if preferences else ""
        self.source_profile = (
            getattr(preferences, "last_migration_profile", "") if preferences else ""
        )
        current = Path(getattr(bpy.app, "binary_path", "") or sys.executable)
        self.target_executable = suggest_blender_executable(current, remembered)
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "source_mode", expand=True)
        if self.source_mode == "PROFILE":
            layout.prop(self, "source_profile")
        layout.prop(self, "target_executable")
        if self.source_mode == "CURRENT":
            self._draw_options(layout)
        else:
            layout.prop(self, "output_dir")
        advanced = layout.box()
        advanced.label(text="高级", icon="SETTINGS")
        advanced.prop(self, "audit_timeout")

    def execute(self, context):
        if sys.platform != "win32":
            self.report({"ERROR"}, "跨版本一键迁移第一版仅支持 Windows")
            return {"CANCELLED"}
        target = Path(self.target_executable)
        if not target.is_file() or target.name.casefold() != "blender.exe":
            self.report({"ERROR"}, "请选择有效的目标 blender.exe")
            return {"CANCELLED"}
        if not self._reserve_job():
            self.report({"WARNING"}, "已有配置任务正在执行")
            return {"CANCELLED"}
        output_dir = Path(self.output_dir)
        audit_timeout = int(self.audit_timeout)

        if self.source_mode == "PROFILE":
            profile_path = Path(self.source_profile)
            if not profile_path.is_file():
                self._release_job()
                self.report({"ERROR"}, "请选择有效的跨版本配置包")
                return {"CANCELLED"}
            current_user_root = Path(bpy.utils.resource_path("USER"))

            def existing_profile_job():
                return execute_existing_profile_migration(
                    profile_path,
                    target,
                    output_dir,
                    current_user_root,
                    _WORKER_SCRIPT,
                    os.getpid(),
                    audit_timeout=audit_timeout,
                )

            return self._start_job(
                context,
                existing_profile_job,
                "正在验证配置包并迁移配置...",
            )

        try:
            snapshot, work_dir = self._snapshot(context)
        except Exception as exc:
            self._release_job()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        def job():
            try:
                return execute_migration(
                    snapshot,
                    target,
                    output_dir,
                    _WORKER_SCRIPT,
                    os.getpid(),
                    audit_timeout=audit_timeout,
                )
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

        return self._start_job(context, job, "正在创建快照并迁移配置...")

    def _handle_success(self, context, result):
        preferences = _get_preferences(context)
        self._save_options(preferences)
        if preferences:
            preferences.last_target_blender = self.target_executable
            preferences.last_migration_profile = str(result.profile_path)
            preferences.last_migration_recovery = str(result.recovery_dir / "recovery.json")
            preferences.last_migration_report = str(result.report_path)
        bpy.ops.wm.save_userpref()
        if result.status == "degraded":
            self.report(
                {"WARNING"},
                f"迁移完成，已禁用 {len(result.disabled_addons)} 个不兼容插件，"
                "请查看报告",
            )
        else:
            self.report({"INFO"}, f"迁移完成：Blender {'.'.join(map(str, result.target_version))}")

    def _handle_failure(self, context, error):
        recovery_dir = getattr(error, "recovery_dir", None)
        if not recovery_dir:
            return
        preferences = _get_preferences(context)
        if not preferences:
            return
        recovery_dir = Path(recovery_dir)
        preferences.last_migration_recovery = str(recovery_dir / "recovery.json")
        report_path = recovery_dir / "migration_report.json"
        if report_path.is_file():
            preferences.last_migration_report = str(report_path)
        bpy.ops.wm.save_userpref()


class MMY_OT_ExportMigrationProfile(
    _AsyncOperatorMixin,
    _SnapshotOptionsMixin,
    bpy.types.Operator,
):
    bl_idname = "mmy.export_migration_profile"
    bl_label = "导出跨版本配置包"
    bl_description = "生成可重复迁移到更高同主版本 Blender 的配置快照"
    bl_options = {"REGISTER"}

    output_dir: bpy.props.StringProperty(name="输出目录", subtype="DIR_PATH")
    include_presets: bpy.props.BoolProperty(name="用户预设", default=True)
    include_datafiles: bpy.props.BoolProperty(
        name="数据文件与 Studio Lights",
        default=False,
    )
    include_startup_scripts: bpy.props.BoolProperty(
        name="启动脚本（存在代码执行风险）",
        default=False,
    )
    include_history: bpy.props.BoolProperty(name="书签与最近文件", default=False)

    def invoke(self, context, event):
        self._load_option_defaults(_get_preferences(context))
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        self._draw_options(self.layout)

    def execute(self, context):
        if not self._reserve_job():
            self.report({"WARNING"}, "已有配置任务正在执行")
            return {"CANCELLED"}
        try:
            snapshot, work_dir = self._snapshot(context)
        except Exception as exc:
            self._release_job()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        output_dir = Path(self.output_dir)

        def job():
            try:
                return create_profile(snapshot, output_dir)
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

        return self._start_job(context, job, "正在导出跨版本配置包...")

    def _handle_success(self, context, result):
        preferences = _get_preferences(context)
        self._save_options(preferences)
        if preferences:
            preferences.last_migration_profile = str(result.path)
        bpy.ops.wm.save_userpref()
        self.report({"INFO"}, f"配置包已导出：{result.path.name}")


class MMY_OT_RestoreMigrationBackup(_AsyncOperatorMixin, bpy.types.Operator):
    bl_idname = "mmy.restore_migration_backup"
    bl_label = "恢复迁移前配置"
    bl_description = "使用 recovery.json 将目标 Blender 恢复到迁移前状态"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(name="恢复记录", subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        preferences = _get_preferences(context)
        remembered = getattr(preferences, "last_migration_recovery", "") if preferences else ""
        if remembered:
            self.filepath = remembered
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        self.layout.prop(self, "filepath")

    def execute(self, context):
        if sys.platform != "win32":
            self.report({"ERROR"}, "跨版本恢复第一版仅支持 Windows")
            return {"CANCELLED"}
        recovery_file = Path(self.filepath)
        if recovery_file.name != "recovery.json" or not recovery_file.is_file():
            self.report({"ERROR"}, "请选择迁移生成的 recovery.json")
            return {"CANCELLED"}
        if not self._reserve_job():
            self.report({"WARNING"}, "已有配置任务正在执行")
            return {"CANCELLED"}
        return self._start_job(
            context,
            lambda: restore_recovery(recovery_file, os.getpid()),
            "正在恢复迁移前配置...",
        )

    def _handle_success(self, context, result):
        self.report({"INFO"}, f"目标配置已恢复：{result['target_root']}")


class MMY_OT_OpenMigrationReport(bpy.types.Operator):
    bl_idname = "mmy.open_migration_report"
    bl_label = "打开迁移报告"
    bl_description = "打开最近一次迁移报告所在目录"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        preferences = _get_preferences(context)
        report = Path(getattr(preferences, "last_migration_report", "")) if preferences else Path()
        if not report.is_file():
            self.report({"WARNING"}, "暂无可用的迁移报告")
            return {"CANCELLED"}
        try:
            bpy.ops.wm.path_open(filepath=str(report.parent))
        except Exception as exc:
            self.report({"ERROR"}, f"无法打开报告目录：{exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


classes = (
    MMY_OT_MigrateToBlender,
    MMY_OT_ExportMigrationProfile,
    MMY_OT_RestoreMigrationBackup,
    MMY_OT_OpenMigrationReport,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
