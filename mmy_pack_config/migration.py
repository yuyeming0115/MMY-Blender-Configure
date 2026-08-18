"""Blender 跨版本配置迁移的 Operator、备份记录与当前会话采集逻辑。

v1.3.0 变更：
  - 迁移改为两阶段：预检（只读）→ 确认页 → 执行（M1）
  - 迁移组件按风险分级展示，启动脚本需二次确认（M2）
  - 配置包信息前置展示 + 新旧提示（M4）
  - 支持多目标批量迁移（M5）
  - 新增备份记录面板，恢复入口改为列表选择（B2/B3）
  - 异步任务状态栏显示已用时长
"""

import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import addon_utils
import bpy

from . import utils
from .migration_core import (
    RECOVERY_DIR_NAME,
    MigrationError,
    SourceSnapshot,
    cleanup_stale_migration_artifacts,
    create_profile,
    directory_size,
    ensure_external_output,
    execute_existing_profile_migration_multi,
    execute_migration_multi,
    extract_portable_backup,
    find_blender_executables,
    list_backup_entries,
    predict_addon_compatibility,
    read_profile_manifest,
    restore_recovery,
    suggest_blender_executable,
    version_string,
)


_WORKER_SCRIPT = Path(__file__).parent / "migration_worker.py"
_JOB_LOCK = threading.Lock()

# 两阶段迁移在 Operator 之间传递的待执行数据
_PENDING_MIGRATION: dict = {}

# 备份记录面板的缓存条目
_BACKUP_ENTRIES: list = []


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


def _discard_pending_migration():
    """清理两阶段迁移的待执行数据与临时目录。"""
    pending = _PENDING_MIGRATION
    _PENDING_MIGRATION.clear()
    work_dir = pending.get("work_dir")
    if work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)


class _AsyncOperatorMixin:
    _timer = None
    _thread = None
    _result_queue = None
    _job_reserved = False
    _status_base = ""
    _start_time = 0.0

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
        self._status_base = status_text
        self._start_time = time.monotonic()
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
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if self._thread.is_alive():
            elapsed = int(time.monotonic() - self._start_time)
            _set_status_text(context, f"{self._status_base}（已用 {elapsed} 秒）")
            return {"RUNNING_MODAL"}

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
    """迁移组件选项：按风险分级展示（M2）。"""

    def _load_option_defaults(self, preferences):
        self.output_dir = _default_output_dir(preferences)
        if preferences:
            self.include_presets = preferences.migration_include_presets
            self.include_datafiles = preferences.migration_include_datafiles
            self.include_startup_scripts = preferences.migration_include_startup_scripts
            self.include_history = preferences.migration_include_history
        self.confirm_startup_risk = False

    def _draw_options(self, layout):
        layout.prop(self, "output_dir")
        layout.label(text="执行前将自动保存当前 Blender 偏好设置", icon='INFO')

        safe_box = layout.box()
        safe_box.label(text="🟢 安全（默认迁移）", icon='CHECKMARK')
        safe_box.label(text="偏好设置 / 快捷键 / 启动文件 / 插件与扩展：始终包含", icon='BLANK1')
        safe_box.prop(self, "include_presets")

        caution_box = layout.box()
        caution_box.label(text="🟡 谨慎（按需勾选）", icon='SORTTIME')
        caution_box.prop(self, "include_datafiles")
        caution_box.prop(self, "include_history")

        risk_box = layout.box()
        risk_box.label(text="🔴 高风险", icon='ERROR')
        risk_box.prop(self, "include_startup_scripts")
        if self.include_startup_scripts:
            alert = risk_box.column()
            alert.alert = True
            alert.label(text="启动脚本会在目标 Blender 启动时自动执行代码")
            alert.prop(self, "confirm_startup_risk")

    def _validate_options(self) -> str:
        """返回错误信息；None 表示校验通过。"""
        if self.include_startup_scripts and not self.confirm_startup_risk:
            return "已勾选启动脚本：请同时勾选「我已了解风险」确认项"
        return ""

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


class MMY_TargetCandidate(bpy.types.PropertyGroup):
    """多目标批量迁移的候选目标（M5）。"""

    name: bpy.props.StringProperty(name="目标 blender.exe")
    use: bpy.props.BoolProperty(name="迁入此目标", default=False)


def _exe_dir_version_label(exe_path: str) -> str:
    """从 blender.exe 所在目录名解析版本号（启发式，用于展示）。"""
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", str(Path(exe_path).parent))
    return match.group(1) if match else "未知版本"


class MMY_OT_MigrateToBlender(
    _AsyncOperatorMixin,
    _SnapshotOptionsMixin,
    bpy.types.Operator,
):
    """阶段一：收集输入并执行只读预检，随后弹出确认页。"""

    bl_idname = "mmy.migrate_to_blender"
    bl_label = "迁移到新版 Blender"
    bl_description = "先预检并展示风险，确认后备份目标并迁移到更高的同主版本 Blender"
    bl_options = {"REGISTER"}

    output_dir: bpy.props.StringProperty(name="输出目录", subtype="DIR_PATH")
    include_presets: bpy.props.BoolProperty(name="用户预设", default=True)
    include_datafiles: bpy.props.BoolProperty(
        name="数据文件与 Studio Lights（体积大）",
        default=False,
    )
    include_startup_scripts: bpy.props.BoolProperty(
        name="启动脚本（自动执行代码）",
        default=False,
    )
    include_history: bpy.props.BoolProperty(
        name="书签与最近文件（含本机路径）",
        default=False,
    )
    confirm_startup_risk: bpy.props.BoolProperty(
        name="我已了解风险，仍要迁移启动脚本",
        default=False,
    )
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
    extra_targets: bpy.props.CollectionProperty(type=MMY_TargetCandidate)
    audit_timeout: bpy.props.IntProperty(
        name="验证超时（秒）",
        default=300,
        min=60,
        max=1800,
    )
    profile_summary: bpy.props.StringProperty(default="", options={"HIDDEN"})
    profile_hint: bpy.props.StringProperty(default="", options={"HIDDEN"})

    def invoke(self, context, event):
        preferences = _get_preferences(context)
        self._load_option_defaults(preferences)
        remembered = getattr(preferences, "last_target_blender", "") if preferences else ""
        self.source_profile = (
            getattr(preferences, "last_migration_profile", "") if preferences else ""
        )
        current = Path(getattr(bpy.app, "binary_path", "") or sys.executable)
        self.target_executable = suggest_blender_executable(current, remembered)

        # M5：列出本机其他可迁入目标（排除当前与主目标）
        self.extra_targets.clear()
        primary = self.target_executable
        for candidate in find_blender_executables(current):
            candidate_str = str(candidate)
            if candidate_str == primary:
                continue
            item = self.extra_targets.add()
            item.name = candidate_str
            item.use = False

        self._refresh_profile_summary()
        return context.window_manager.invoke_props_dialog(self, width=560)

    def _refresh_profile_summary(self):
        """M4：读取配置包 manifest 生成摘要与新旧提示。"""
        self.profile_summary = ""
        self.profile_hint = ""
        profile_path = Path(self.source_profile) if self.source_profile else None
        if not profile_path or not profile_path.is_file():
            return
        try:
            manifest = read_profile_manifest(profile_path)
        except Exception:
            self.profile_summary = "无法读取配置包（文件无效或已损坏）"
            return
        source = manifest.get("source", {})
        source_version = version_string(source.get("blender_version", []))
        created = manifest.get("created_at", "?")
        components = ", ".join(manifest.get("components", [])) or "?"
        self.profile_summary = (
            f"配置包：来源 Blender {source_version} ｜ 创建于 {created} ｜ 组件：{components}"
        )
        # 新旧提示：本机存在比配置包更新的同主版本安装时给出建议
        current = Path(getattr(bpy.app, "binary_path", "") or sys.executable)
        source_parts = _version_list(source_version)
        for candidate in find_blender_executables(current):
            candidate_version = _version_list(_exe_dir_version_label(str(candidate)))
            if (
                len(source_parts) >= 2
                and len(candidate_version) >= 2
                and candidate_version[0] == source_parts[0]
                and tuple(candidate_version) > tuple(source_parts)
            ):
                self.profile_hint = (
                    f"本机检测到更高版本 Blender {'.'.join(map(str, candidate_version))}："
                    "若你已在该版本工作过，建议改为从它重新导出配置包，"
                    "否则会丢失该版本期间的新设置"
                )
                break

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "source_mode", expand=True)
        if self.source_mode == "PROFILE":
            layout.prop(self, "source_profile")
            if self.profile_summary:
                layout.label(text=self.profile_summary, icon='INFO')
            if self.profile_hint:
                hint_box = layout.box()
                hint_box.alert = True
                hint_box.label(text=self.profile_hint, icon='ERROR')
        layout.prop(self, "target_executable")
        if len(self.extra_targets):
            multi_box = layout.box()
            multi_box.label(text="附加目标（可选：同一份配置顺次迁入）", icon='LINKED')
            for candidate in self.extra_targets:
                row = multi_box.row(align=True)
                row.prop(candidate, "use", text="")
                row.label(
                    text=f"{_exe_dir_version_label(candidate.name)} ｜ {candidate.name}",
                    icon='BLENDER',
                )
        if self.source_mode == "CURRENT":
            self._draw_options(layout)
        else:
            layout.prop(self, "output_dir")
        advanced = layout.box()
        advanced.label(text="高级", icon="SETTINGS")
        advanced.prop(self, "audit_timeout")
        layout.label(text="点击「确定」后先进行只读预检，确认无误才会真正迁移", icon='INFO')

    def _selected_targets(self) -> list[str]:
        targets = [self.target_executable]
        for candidate in self.extra_targets:
            if candidate.use:
                targets.append(candidate.name)
        return targets

    def execute(self, context):
        if sys.platform != "win32":
            self.report({"ERROR"}, "跨版本一键迁移第一版仅支持 Windows")
            return {"CANCELLED"}
        option_error = self._validate_options()
        if option_error:
            self.report({"ERROR"}, option_error)
            return {"CANCELLED"}
        targets = self._selected_targets()
        for target_str in targets:
            target = Path(target_str)
            if not target.is_file() or target.name.casefold() != "blender.exe":
                self.report({"ERROR"}, f"请选择有效的目标 blender.exe：{target_str}")
                return {"CANCELLED"}
        if self.source_mode == "PROFILE" and not Path(self.source_profile).is_file():
            self.report({"ERROR"}, "请选择有效的跨版本配置包")
            return {"CANCELLED"}
        if not self._reserve_job():
            self.report({"WARNING"}, "已有配置任务正在执行")
            return {"CANCELLED"}

        _discard_pending_migration()
        output_dir = Path(self.output_dir)
        audit_timeout = int(self.audit_timeout)

        snapshot = None
        work_dir = None
        if self.source_mode == "CURRENT":
            try:
                snapshot, work_dir = self._snapshot(context)
            except Exception as exc:
                self._release_job()
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}

        source_mode = self.source_mode
        source_profile = self.source_profile
        current_user_root = Path(bpy.utils.resource_path("USER"))

        def precheck_job():
            return _run_precheck(
                source_mode=source_mode,
                snapshot=snapshot,
                profile_path=Path(source_profile) if source_profile else None,
                targets=[Path(t) for t in targets],
                output_dir=output_dir,
                current_pid=os.getpid(),
                current_user_root=current_user_root,
            )

        self._pending_snapshot = snapshot
        self._pending_work_dir = work_dir
        self._pending_targets = targets
        self._pending_output_dir = str(output_dir)
        self._pending_audit_timeout = audit_timeout
        self._pending_source_mode = source_mode
        self._pending_source_profile = source_profile
        return self._start_job(context, precheck_job, "正在预检目标环境（只读，不修改任何文件）...")

    def _handle_success(self, context, precheck):
        _PENDING_MIGRATION.clear()
        _PENDING_MIGRATION.update(
            {
                "snapshot": self._pending_snapshot,
                "work_dir": self._pending_work_dir,
                "targets": self._pending_targets,
                "output_dir": self._pending_output_dir,
                "audit_timeout": self._pending_audit_timeout,
                "source_mode": self._pending_source_mode,
                "source_profile": self._pending_source_profile,
                "precheck": precheck,
            }
        )
        try:
            bpy.ops.mmy.migration_confirm("INVOKE_DEFAULT")
        except Exception as exc:
            _discard_pending_migration()
            self.report({"ERROR"}, f"无法打开预检确认页：{exc}")

    def _handle_failure(self, context, error):
        work_dir = getattr(self, "_pending_work_dir", None)
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
            self._pending_work_dir = None


def _run_precheck(
    source_mode: str,
    snapshot,
    profile_path,
    targets: list[Path],
    output_dir: Path,
    current_pid: int,
    current_user_root: Path,
) -> dict:
    """只读预检：探测每个目标的版本/目录/占用/兼容性，不修改任何文件。"""
    from .migration_core import (
        run_target_probe,
        validate_forward_version,
        validate_target_resource_layout,
        windows_executable_is_running,
        _resolved,
    )

    if source_mode == "PROFILE":
        manifest = read_profile_manifest(profile_path)
        if manifest.get("source", {}).get("platform") != "win32":
            raise MigrationError("跨版本一键迁移第一版只接受 Windows 配置快照")
        source_version = manifest.get("source", {}).get("blender_version", [])
        addons = manifest.get("addons", [])
        components = manifest.get("components", [])
    else:
        source_version = list(snapshot.version)
        addons = snapshot.addons
        components = []
        if snapshot.include_presets:
            components.append("presets")
        if snapshot.include_datafiles:
            components.append("datafiles")
        if snapshot.include_startup_scripts:
            components.append("startup_scripts")
        if snapshot.include_history:
            components.append("history")

    current_root = _resolved(
        snapshot.user_root if snapshot else current_user_root
    )
    entries = []
    for target in targets:
        entry = {"target": str(target), "ok": False, "error": ""}
        try:
            if os.name == "nt" and windows_executable_is_running(target, [current_pid]):
                raise MigrationError("目标 Blender 正在运行，请关闭后重试")
            probe = run_target_probe(target)
            target_version = validate_forward_version(source_version, probe["version"])
            target_root = _resolved(probe["user_root"])
            validate_target_resource_layout(probe, target_root)
            if target_root == current_root:
                raise MigrationError("来源与目标使用同一用户目录")
            entry.update(
                {
                    "ok": True,
                    "target_version": version_string(target_version),
                    "target_root": str(target_root),
                    "existing_mb": round(directory_size(target_root) / 1024 / 1024, 1),
                    "incompatible": predict_addon_compatibility(addons, target_version),
                }
            )
        except Exception as exc:
            entry["error"] = str(exc)
        entries.append(entry)

    try:
        free_mb = round(shutil.disk_usage(str(output_dir)).free / 1024 / 1024, 0)
    except OSError:
        free_mb = -1

    return {
        "source_version": version_string(source_version),
        "components": components,
        "targets": entries,
        "output_dir": str(output_dir),
        "free_mb": free_mb,
    }


class MMY_OT_MigrationConfirm(
    _AsyncOperatorMixin,
    bpy.types.Operator,
):
    """阶段二：展示预检报告，用户确认后执行真正迁移。"""

    bl_idname = "mmy.migration_confirm"
    bl_label = "迁移预检确认"
    bl_description = "确认预检结果后执行迁移"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        if not _PENDING_MIGRATION:
            self.report({"ERROR"}, "没有待确认的迁移任务")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(
            self, width=640, confirm_text="确认迁移"
        )

    def draw(self, context):
        layout = self.layout
        precheck = _PENDING_MIGRATION.get("precheck", {})
        source_version = precheck.get("source_version", "?")

        header = layout.box()
        header.label(text=f"配置来源：Blender {source_version}", icon='BLENDER')
        components = precheck.get("components") or []
        optional = (" + " + ", ".join(components)) if components else ""
        header.label(
            text=f"迁移组件：偏好 / 快捷键 / 启动文件 / 插件与扩展（固定包含）{optional}",
            icon='PREFERENCES',
        )
        header.label(
            text=f"输出目录：{precheck.get('output_dir', '?')}（可用 {precheck.get('free_mb', '?')} MB）",
            icon='FILE_FOLDER',
        )

        for entry in precheck.get("targets", []):
            box = layout.box()
            if entry.get("ok"):
                box.label(
                    text=f"目标：Blender {entry.get('target_version', '?')} ✅",
                    icon='CHECKMARK',
                )
                box.label(text=entry.get("target", ""), icon='BLANK1')
                box.label(
                    text=f"目标用户目录：{entry.get('target_root', '')}",
                    icon='FILE_FOLDER',
                )
                box.label(
                    text=f"目标已有配置 {entry.get('existing_mb', 0)} MB（迁移前自动备份，可随时恢复）",
                    icon='RECOVER_LAST',
                )
                incompatible = entry.get("incompatible") or []
                if incompatible:
                    warn = box.column()
                    warn.alert = True
                    warn.label(
                        text=f"预计 {len(incompatible)} 个插件不兼容（迁移后自动禁用）：",
                        icon='ERROR',
                    )
                    for item in incompatible[:8]:
                        warn.label(text=f"{item['module']}：{item['reason']}", icon='DOT')
            else:
                box.alert = True
                box.label(text=f"目标不可用：{entry.get('target', '?')}", icon='CANCEL')
                box.label(text=entry.get("error", ""), icon='ERROR')

        layout.label(text="迁移期间请勿打开任何目标 Blender；失败会自动回滚", icon='INFO')

    def execute(self, context):
        pending = dict(_PENDING_MIGRATION)
        if not pending:
            self.report({"ERROR"}, "没有待确认的迁移任务")
            return {"CANCELLED"}
        precheck = pending.get("precheck", {})
        ok_targets = [
            entry["target"] for entry in precheck.get("targets", []) if entry.get("ok")
        ]
        if not ok_targets:
            self.report({"ERROR"}, "预检未通过：没有可迁移的目标")
            return {"CANCELLED"}
        if not self._reserve_job():
            self.report({"WARNING"}, "已有配置任务正在执行")
            return {"CANCELLED"}

        snapshot = pending.get("snapshot")
        work_dir = pending.get("work_dir")
        output_dir = Path(pending["output_dir"])
        audit_timeout = int(pending["audit_timeout"])
        source_mode = pending["source_mode"]
        source_profile = pending.get("source_profile") or ""
        self._confirmed_output_dir = pending["output_dir"]
        current_user_root = Path(bpy.utils.resource_path("USER"))

        def job():
            try:
                if source_mode == "PROFILE":
                    return execute_existing_profile_migration_multi(
                        Path(source_profile),
                        [Path(t) for t in ok_targets],
                        output_dir,
                        current_user_root,
                        _WORKER_SCRIPT,
                        os.getpid(),
                        audit_timeout=audit_timeout,
                    )
                return execute_migration_multi(
                    snapshot,
                    [Path(t) for t in ok_targets],
                    output_dir,
                    _WORKER_SCRIPT,
                    os.getpid(),
                    audit_timeout=audit_timeout,
                )
            finally:
                if work_dir:
                    shutil.rmtree(work_dir, ignore_errors=True)
                _PENDING_MIGRATION.clear()

        return self._start_job(context, job, "正在迁移配置（自动备份目标，失败自动回滚）...")

    def cancel(self, context):
        _discard_pending_migration()

    def _handle_success(self, context, result):
        profile, outcomes = result
        preferences = _get_preferences(context)
        self._save_options_if_possible(preferences)
        status_parts = []
        for outcome in outcomes:
            target_label = _exe_dir_version_label(str(outcome.target_executable))
            if outcome.result is not None:
                migration = outcome.result
                if preferences:
                    preferences.last_target_blender = str(outcome.target_executable)
                    preferences.last_migration_profile = str(migration.profile_path)
                    preferences.last_migration_recovery = str(
                        migration.recovery_dir / "recovery.json"
                    )
                    preferences.last_migration_report = str(
                        migration.report_html_path or migration.report_path
                    )
                if migration.status == "degraded":
                    status_parts.append(
                        f"{target_label}：降级（禁用 {len(migration.disabled_addons)} 个插件）"
                    )
                else:
                    status_parts.append(
                        f"{target_label}：成功（{version_string(migration.target_version)}）"
                    )
            else:
                status_parts.append(f"{target_label}：失败（{outcome.error}）")
        if preferences:
            preferences.last_migration_status = (
                f"{datetime.now().strftime('%m-%d %H:%M')} " + "；".join(status_parts)
            )
        bpy.ops.wm.save_userpref()
        has_failure = any(outcome.result is None for outcome in outcomes)
        has_degraded = any(
            outcome.result is not None and outcome.result.status == "degraded"
            for outcome in outcomes
        )
        level = {"ERROR"} if has_failure else ({"WARNING"} if has_degraded else {"INFO"})
        self.report(level, "迁移结果：" + "；".join(status_parts))

    def _save_options_if_possible(self, preferences):
        output_dir = getattr(self, "_confirmed_output_dir", "")
        if preferences and output_dir:
            preferences.pack_output_path = output_dir

    def _handle_failure(self, context, error):
        recovery_dir = getattr(error, "recovery_dir", None)
        preferences = _get_preferences(context)
        if preferences:
            if recovery_dir:
                recovery_dir = Path(recovery_dir)
                preferences.last_migration_recovery = str(recovery_dir / "recovery.json")
                report_path = recovery_dir / "migration_report.json"
                html_path = recovery_dir / "migration_report.html"
                if html_path.is_file():
                    preferences.last_migration_report = str(html_path)
                elif report_path.is_file():
                    preferences.last_migration_report = str(report_path)
            preferences.last_migration_status = (
                f"{datetime.now().strftime('%m-%d %H:%M')} 失败：{error}"
            )
            bpy.ops.wm.save_userpref()
        _discard_pending_migration()


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
        name="数据文件与 Studio Lights（体积大）",
        default=False,
    )
    include_startup_scripts: bpy.props.BoolProperty(
        name="启动脚本（自动执行代码）",
        default=False,
    )
    include_history: bpy.props.BoolProperty(
        name="书签与最近文件（含本机路径）",
        default=False,
    )
    confirm_startup_risk: bpy.props.BoolProperty(
        name="我已了解风险，仍要包含启动脚本",
        default=False,
    )

    def invoke(self, context, event):
        self._load_option_defaults(_get_preferences(context))
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        self._draw_options(self.layout)

    def execute(self, context):
        option_error = self._validate_options()
        if option_error:
            self.report({"ERROR"}, option_error)
            return {"CANCELLED"}
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
    summary: bpy.props.StringProperty(default="", options={"HIDDEN"})

    def invoke(self, context, event):
        preferences = _get_preferences(context)
        if not self.filepath:
            remembered = getattr(preferences, "last_migration_recovery", "") if preferences else ""
            if remembered:
                self.filepath = remembered
        self.summary = self._build_summary(self.filepath)
        return context.window_manager.invoke_props_dialog(
            self, width=560, confirm_text="确认恢复"
        )

    @staticmethod
    def _build_summary(filepath: str) -> str:
        recovery_file = Path(filepath) if filepath else None
        if not recovery_file or not recovery_file.is_file():
            return ""
        try:
            metadata = json.loads(recovery_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "恢复记录无法解析"
        source_v = version_string(metadata.get("source_version", []))
        target_v = version_string(metadata.get("target_version", []))
        created = metadata.get("created_at", "?")
        status = metadata.get("status", "?")
        return f"{source_v} → {target_v} ｜ 迁移于 {created} ｜ 状态 {status}"

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "filepath")
        if self.summary:
            layout.label(text=self.summary, icon='INFO')
        layout.label(text="恢复前会先备份目标当前配置，可再次回退", icon='RECOVER_LAST')

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
    bl_description = "直接打开最近一次迁移报告（优先 HTML）"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        preferences = _get_preferences(context)
        report = Path(getattr(preferences, "last_migration_report", "")) if preferences else Path()
        if report.suffix.lower() == ".json":
            html_sibling = report.with_suffix(".html")
            if html_sibling.is_file():
                report = html_sibling
        if not report.is_file():
            self.report({"WARNING"}, "暂无可用的迁移报告")
            return {"CANCELLED"}
        try:
            bpy.ops.wm.path_open(filepath=str(report))
        except Exception as exc:
            self.report({"ERROR"}, f"无法打开报告：{exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# ============================================================
# 备份记录面板（B2/B3）
# ============================================================

class MMY_OT_BackupHistory(bpy.types.Operator):
    bl_idname = "mmy.backup_history"
    bl_label = "备份记录"
    bl_description = "列出全部备份与迁移恢复点；回到旧版本 = 恢复该版本的备份"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        preferences = _get_preferences(context)
        output_dir = _default_output_dir(preferences)
        global _BACKUP_ENTRIES
        try:
            _BACKUP_ENTRIES = list_backup_entries(Path(output_dir))
        except Exception as exc:
            _BACKUP_ENTRIES = []
            print(f"[MMY] 扫描备份记录失败: {exc}")
        return context.window_manager.invoke_props_dialog(
            self, width=720, confirm_text="关闭"
        )

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text="回到旧版本 = 恢复该版本的备份（迁移从不修改源版本）", icon='INFO')
        if not _BACKUP_ENTRIES:
            layout.label(text="暂无备份记录：先执行一次「立即备份」或迁移", icon='BLANK1')
            return
        for entry in _BACKUP_ENTRIES[:30]:
            box = layout.box()
            head = box.row(align=True)
            icon = {
                "portable": "PACKAGE",
                "profile": "EXPORT",
                "recovery": "RECOVER_LAST",
            }.get(entry["type"], "FILE")
            type_label = {
                "portable": "全量备份",
                "profile": "配置包",
                "recovery": "迁移前备份",
            }.get(entry["type"], entry["type"])
            head.label(text=f"[{type_label}] {entry['version_label']}", icon=icon)
            created = datetime.fromtimestamp(entry["created"]).strftime("%Y-%m-%d %H:%M")
            size_mb = entry["size"] / 1024 / 1024
            status = f" ｜ {entry['status']}" if entry.get("status") else ""
            box.label(text=f"{created} ｜ {size_mb:.1f} MB{status}", icon='TIME')
            if entry.get("detail"):
                box.label(text=entry["detail"], icon='BLANK1')

            actions = box.row(align=True)
            if entry["type"] == "recovery":
                restore = actions.operator(
                    "mmy.restore_migration_backup",
                    text="恢复",
                    icon='RECOVER_LAST',
                )
                restore.filepath = entry["path"]
            elif entry["type"] == "portable":
                unpack = actions.operator(
                    "mmy.restore_portable_backup",
                    text="解压恢复",
                    icon='IMPORT',
                )
                unpack.filepath = entry["path"]
            locate = actions.operator(
                "mmy.open_path_location",
                text="打开位置",
                icon='FILE_FOLDER',
            )
            locate.filepath = entry["path"]


class MMY_OT_OpenPathLocation(bpy.types.Operator):
    bl_idname = "mmy.open_path_location"
    bl_label = "打开所在位置"
    bl_options = {"INTERNAL"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        path = Path(self.filepath)
        target = path if path.is_dir() else path.parent
        if not target.exists():
            self.report({"WARNING"}, f"路径不存在：{target}")
            return {"CANCELLED"}
        try:
            bpy.ops.wm.path_open(filepath=str(target))
        except Exception as exc:
            self.report({"ERROR"}, f"无法打开位置：{exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class MMY_OT_RestorePortableBackup(_AsyncOperatorMixin, bpy.types.Operator):
    """把全量 Portable 备份解压到指定目录（降级恢复的主路径）。"""

    bl_idname = "mmy.restore_portable_backup"
    bl_label = "解压恢复 Portable 备份"
    bl_description = "将全量备份解压到指定目录；建议选择旧版本 Blender 的 portable 上级目录"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(name="备份文件", subtype="FILE_PATH")
    target_dir: bpy.props.StringProperty(
        name="解压到目录",
        subtype="DIR_PATH",
        description="备份内容将解压到该目录下（内含 portable/ 层级）",
    )

    def invoke(self, context, event):
        if not self.target_dir:
            self.target_dir = str(Path(self.filepath).parent / "restored")
        return context.window_manager.invoke_props_dialog(
            self, width=560, confirm_text="确认解压"
        )

    def draw(self, context):
        layout = self.layout
        layout.label(text=Path(self.filepath).name, icon='PACKAGE')
        layout.prop(self, "target_dir")
        warn = layout.column()
        warn.alert = True
        warn.label(text="解压会覆盖目标目录中的同名文件")
        layout.label(
            text="降级用法：解压到旧版本 Blender 目录，替换其中的 portable/ 文件夹",
            icon='INFO',
        )

    def execute(self, context):
        zip_path = Path(self.filepath)
        if not zip_path.is_file():
            self.report({"ERROR"}, "备份文件不存在")
            return {"CANCELLED"}
        target_dir = Path(self.target_dir)
        if not self._reserve_job():
            self.report({"WARNING"}, "已有配置任务正在执行")
            return {"CANCELLED"}
        return self._start_job(
            context,
            lambda: extract_portable_backup(zip_path, target_dir),
            "正在解压恢复备份...",
        )

    def _handle_success(self, context, count):
        self.report({"INFO"}, f"解压完成：{count} 个文件 → {self.target_dir}")


class MMY_OT_CleanupMigrationArtifacts(bpy.types.Operator):
    bl_idname = "mmy.cleanup_migration_artifacts"
    bl_label = "清理迁移残留"
    bl_description = "删除迁移中断留下的 .mmy_old_/.mmy_stage_ 等临时目录"
    bl_options = {"REGISTER"}

    include_recent: bpy.props.BoolProperty(
        name="同时清理 24 小时内的残留",
        description="默认只清理超过 24 小时的残留；勾选后全部清理（确保没有迁移正在执行）",
        default=False,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=480, confirm_text="立即清理"
        )

    def draw(self, context):
        layout = self.layout
        layout.label(text="清理迁移中断残留的 .mmy_old_/.mmy_stage_ 等目录", icon='TRASH')
        layout.label(text="这些目录可能被误打包进全量备份，建议定期清理", icon='INFO')
        layout.prop(self, "include_recent")

    def execute(self, context):
        preferences = _get_preferences(context)
        parents = set()
        try:
            parents.add(Path(bpy.utils.resource_path("USER")).parent)
        except Exception:
            pass
        output_dir = Path(_default_output_dir(preferences))
        parents.add(output_dir)
        max_age = 0.0 if self.include_recent else 24.0
        removed = cleanup_stale_migration_artifacts(sorted(parents), max_age)
        # 顺带清理预检/快照遗留的临时目录
        now = time.time()
        for temp_dir in output_dir.glob("mmy_migration_*"):
            if not temp_dir.is_dir():
                continue
            try:
                if self.include_recent or (now - temp_dir.stat().st_mtime) > 24 * 3600:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    removed.append(str(temp_dir))
            except OSError:
                continue
        if removed:
            self.report({"INFO"}, f"已清理 {len(removed)} 个残留目录")
        else:
            self.report({"INFO"}, "没有需要清理的迁移残留")
        return {"FINISHED"}


classes = (
    MMY_TargetCandidate,
    MMY_OT_MigrateToBlender,
    MMY_OT_MigrationConfirm,
    MMY_OT_ExportMigrationProfile,
    MMY_OT_RestoreMigrationBackup,
    MMY_OT_OpenMigrationReport,
    MMY_OT_BackupHistory,
    MMY_OT_OpenPathLocation,
    MMY_OT_RestorePortableBackup,
    MMY_OT_CleanupMigrationArtifacts,
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
