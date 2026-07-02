"""
插件加载耗时监控（monkey-patch bpy.utils._addon_utils.enable）

工作原理：
1. 在 register() 时 monkey-patch _addon_utils.enable，拦截后续插件加载
2. 2 秒后执行 fallback 扫描，捕获在注入前已加载的插件
3. 数据持久化到本地 JSON 文件，重启后仍可查看上一次的数据

局限性：
- 我们的插件本身也是通过 enable() 加载的，所以注入时机晚于大部分插件
- 初始启动时只能捕获「排在我们之后加载」的插件
- 已加载的插件只能记录名称，无法测量耗时（记为 0.0s）
- 重启/重新启用插件时可获得完整数据
"""

import sys
import time
import traceback
import json
import bpy
from pathlib import Path
from dataclasses import dataclass, asdict, fields


# ============================================================
# 持久化存储路径（与 pack.py 的 .pack_config.json 同级）
# ============================================================
DATA_FILE = Path(__file__).parent.parent / ".mmy_timer_data.json"
PROBE_DATA_FILE = Path(__file__).parent.parent / ".mmy_timer_probe_data.json"
PROBE_SCRIPT_NAME = "mmy_addon_timer_probe.py"
SELF_MODULE_NAME = __package__.split(".")[0]


@dataclass
class AddonLoadRecord:
    name: str
    elapsed: float
    error: str = ""
    source: str = ""
    kind: str = "session"


class AddonTimerManager:
    def __init__(self):
        self.records: list[AddonLoadRecord] = []
        self.previous_records: list[AddonLoadRecord] = []
        self._patched = False
        self._fallback_registered = False
        self._original_enable = None

    # ---- 基础操作 ----

    def begin_session(self):
        """开始一次新的 Blender 插件加载监控会话。"""
        self.records = []
        self._fallback_registered = False

    def record(self, name: str, elapsed: float, error: str = "", source: str = "", kind: str = "session"):
        """记录一条插件加载信息"""
        # 同名插件保留本次会话中的最新结果，避免旧的慢记录长期压住新数据。
        existing = next((r for r in self.records if r.name == name), None)
        if existing:
            existing.elapsed = elapsed
            existing.error = error
            if source:
                existing.source = source
            if kind:
                existing.kind = kind
            return existing
        else:
            rec = AddonLoadRecord(name, elapsed, error, source, kind)
            self.records.append(rec)
            return rec

    def get_records(self):
        return self.records

    def get_display_records(self):
        """返回面板应展示的数据，以及是否为上次保存的历史数据。"""
        if self.records:
            return self.records, False
        if self.previous_records:
            return self.previous_records, True
        return [], False

    # ---- 持久化 ----

    def save_data(self):
        """将当前记录保存到 JSON 文件"""
        if not self.records:
            print("[MMY] 当前会话暂无耗时数据，跳过保存")
            return

        try:
            data = [asdict(r) for r in self.records]
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"[MMY] 耗时数据已保存 ({len(self.records)} 条)")
        except Exception as e:
            print(f"[MMY] 保存耗时数据失败: {e}")

    def load_data(self):
        """从 JSON 文件加载历史数据"""
        if not DATA_FILE.exists():
            return False
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.previous_records = [_record_from_dict(d) for d in data]
            print(f"[MMY] 已加载历史耗时数据 ({len(self.previous_records)} 条)")
            return True
        except Exception as e:
            print(f"[MMY] 加载历史耗时数据失败: {e}")
            return False

    def load_probe_data(self):
        """读取启动探针在本次 Blender 启动早期写出的数据。"""
        if not PROBE_DATA_FILE.exists():
            return False

        try:
            with open(PROBE_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = 0
            for item in data:
                rec = _record_from_dict(item)
                rec.kind = rec.kind or "startup_probe"
                self.record(rec.name, rec.elapsed, rec.error, rec.source, rec.kind)
                loaded += 1
            print(f"[MMY] 已加载启动探针耗时数据 ({loaded} 条)")
            return True
        except Exception as e:
            print(f"[MMY] 加载启动探针耗时数据失败: {e}")
            return False

    # ---- 启动探针 ----

    def get_probe_script_path(self) -> Path:
        startup_dir = Path(bpy.utils.user_resource("SCRIPTS", path="startup", create=True))
        return startup_dir / PROBE_SCRIPT_NAME

    def is_probe_installed(self) -> bool:
        try:
            return self.get_probe_script_path().exists()
        except Exception:
            return False

    def install_probe(self) -> Path:
        """安装启动探针脚本，下次启动 Blender 时生效。"""
        script_path = self.get_probe_script_path()
        script_path.write_text(_build_probe_script(PROBE_DATA_FILE), encoding="utf-8")
        print(f"[MMY] 启动探针已安装: {script_path}")
        return script_path

    def uninstall_probe(self) -> bool:
        """卸载启动探针脚本。"""
        script_path = self.get_probe_script_path()
        if script_path.exists():
            script_path.unlink()
            print(f"[MMY] 启动探针已卸载: {script_path}")
            return True
        return False

    # ---- 手动重测 ----

    def retest_addon(self, module_name: str):
        """禁用并重新启用指定插件，记录当前会话中的重测耗时。"""
        module_name = module_name.strip()
        if not module_name:
            raise ValueError("未选择插件")
        if module_name == SELF_MODULE_NAME:
            raise ValueError("不能重测当前监控插件本身")

        try:
            from bpy.utils import _addon_utils as addon_utils
        except ImportError:
            import addon_utils

        try:
            addon_utils.disable(module_name, default_set=False)
        except Exception:
            return self._record_retest_error(module_name, "禁用插件失败")

        _purge_addon_modules(module_name)

        err = ""
        mod = None
        t0 = time.perf_counter()
        enable_func = self._original_enable or getattr(addon_utils, "enable")
        try:
            mod = enable_func(module_name, default_set=False)
        except Exception:
            err = traceback.format_exc()

        elapsed = time.perf_counter() - t0
        rec = self.record(module_name, elapsed, err, _detect_addon_source(module_name, mod), "manual_retest")
        self.save_data()
        return rec

    def _record_retest_error(self, module_name: str, message: str):
        err = f"{message}\n{traceback.format_exc()}"
        rec = self.record(module_name, 0.0, err, _detect_addon_source(module_name), "manual_retest")
        self.save_data()
        return rec

    # ---- Monkey-patch ----

    def patch(self):
        """Monkey-patch bpy.utils._addon_utils.enable 以监控后续加载的插件"""
        if self._patched:
            return

        # 先尝试导入目标模块
        try:
            from bpy.utils import _addon_utils as addon_utils
        except ImportError:
            print("[MMY] AddonTimerManager: 无法导入 bpy.utils._addon_utils（Blender 版本可能不兼容）")
            return

        # 检查 enable 是否存在
        original_enable = getattr(addon_utils, "enable", None)
        if not original_enable:
            print("[MMY] AddonTimerManager: _addon_utils.enable 不存在（Blender 版本可能不兼容）")
            return

        manager = self
        self._original_enable = original_enable

        def _timed_enable(module_name, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                mod = original_enable(module_name, *args, **kwargs)
            except Exception:
                err = traceback.format_exc()
                elapsed = time.perf_counter() - t0
                manager.record(module_name, elapsed, err, _detect_addon_source(module_name), "session")
                raise

            elapsed = time.perf_counter() - t0
            manager.record(module_name, elapsed, "", _detect_addon_source(module_name, mod), "session")
            return mod

        try:
            addon_utils.enable = _timed_enable
            self._patched = True
            print("[MMY] AddonTimerManager: monkey-patch 注入成功")
        except Exception as e:
            print(f"[MMY] AddonTimerManager: monkey-patch 安装失败: {e}")

    # ---- Fallback 扫描（捕获早期加载的插件）----

    def register_fallback(self):
        """2 秒后扫描已加载但未被捕获的插件"""
        if self._fallback_registered:
            return
        self._fallback_registered = True

        manager = self

        def _scan_early_addons():
            print("[MMY] AddonTimerManager: 执行 fallback 扫描...")
            scanned = 0
            recorded_names = {r.name for r in manager.records}

            try:
                prefs = bpy.context.preferences.addons
                for mod_name in prefs.keys():
                    if mod_name not in recorded_names:
                        if mod_name in sys.modules:
                            manager.record(mod_name, 0.0, "", _detect_addon_source(mod_name), "early")
                            scanned += 1
                print(f"[MMY] Fallback 扫描完成：发现 {scanned} 个早期插件")
            except Exception as e:
                print(f"[MMY] Fallback 扫描异常: {e}")

            # 扫描完成后保存数据
            manager.save_data()
            return None  # 只执行一次

        try:
            bpy.app.timers.register(
                lambda: _scan_early_addons(),
                first_interval=2.0,
            )
            print("[MMY] AddonTimerManager: fallback timer 已注册（2秒后扫描）")
        except Exception as e:
            print(f"[MMY] 注册 fallback timer 失败: {e}")

    # ---- 恢复原始函数 ----

    def unpatch(self):
        if not self._patched:
            return
        try:
            from bpy.utils import _addon_utils as addon_utils
            import importlib
            importlib.reload(addon_utils)
            self._patched = False
            self._original_enable = None
            print("[MMY] AddonTimerManager: monkey-patch 已恢复")

            # 注销前保存数据
            self.save_data()
        except Exception as e:
            print(f"[MMY] AddonTimerManager: 恢复失败: {e}")


# 全局单例
manager = AddonTimerManager()


def _detect_addon_source(module_name: str, module=None) -> str:
    """尽量识别插件来源，用于 UI 默认屏蔽 Blender 内置/官方项。"""
    if module_name.startswith("bl_ext.blender_org."):
        return "official"

    module = module or sys.modules.get(module_name)
    module_file = getattr(module, "__file__", "") if module else ""
    if not module_file:
        if module_name in _KNOWN_BLENDER_ADDONS:
            return "official"
        return ""

    try:
        module_path = Path(module_file).resolve()
    except Exception:
        module_path = Path(module_file)

    if _is_under_blender_system_scripts(module_path):
        return "official"

    path_text = module_path.as_posix().lower()
    if "/extensions/blender_org/" in path_text or "\\extensions\\blender_org\\" in path_text:
        return "official"

    return "user"


def is_blender_official_addon(record: AddonLoadRecord) -> bool:
    """判断记录是否属于 Blender 内置或官方扩展。"""
    if record.source == "official":
        return True

    name = _clean_record_name(record.name)
    if name.startswith("bl_ext.blender_org."):
        return True

    if name in _KNOWN_BLENDER_ADDONS:
        return True

    module = sys.modules.get(name)
    if module:
        return _detect_addon_source(name, module) == "official"

    return False


def _clean_record_name(name: str) -> str:
    return name.replace("addon_utils: ", "")


def _is_under_blender_system_scripts(module_path: Path) -> bool:
    try:
        system_scripts = Path(bpy.utils.system_resource("SCRIPTS")).resolve()
    except Exception:
        return False

    try:
        module_path.relative_to(system_scripts)
        return True
    except ValueError:
        return False


def _purge_addon_modules(module_name: str):
    """清理目标插件主模块与子模块，便于重测导入成本。"""
    prefix = module_name + "."
    for name in list(sys.modules.keys()):
        if name == module_name or name.startswith(prefix):
            sys.modules.pop(name, None)


def _build_probe_script(data_file: Path) -> str:
    """生成可放入 Blender startup 目录的早期计时脚本。"""
    return f'''# Auto-generated by MMY Blender Configure. Do not edit manually.
import json
import sys
import time
import traceback
from pathlib import Path

DATA_FILE = Path({str(data_file)!r})
RECORDS = []


def _detect_source(module_name, module=None):
    if module_name.startswith("bl_ext.blender_org."):
        return "official"

    module = module or sys.modules.get(module_name)
    module_file = getattr(module, "__file__", "") if module else ""
    if not module_file:
        return ""

    path_text = str(module_file).replace("\\\\", "/").lower()
    if "/extensions/blender_org/" in path_text:
        return "official"
    return "user"


def _record(module_name, elapsed, error="", source=""):
    item = {{
        "name": module_name,
        "elapsed": elapsed,
        "error": error,
        "source": source,
        "kind": "startup_probe",
    }}
    for index, existing in enumerate(RECORDS):
        if existing.get("name") == module_name:
            RECORDS[index] = item
            break
    else:
        RECORDS.append(item)
    _save()


def _save():
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(RECORDS, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"[MMY Probe] save failed: {{exc}}")


def _install():
    try:
        from bpy.utils import _addon_utils as addon_utils
    except Exception:
        try:
            import addon_utils
        except Exception as exc:
            print(f"[MMY Probe] addon_utils unavailable: {{exc}}")
            return

    original_enable = getattr(addon_utils, "enable", None)
    if not original_enable or getattr(original_enable, "_mmy_startup_probe", False):
        return

    RECORDS.clear()
    _save()

    def _timed_enable(module_name, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            mod = original_enable(module_name, *args, **kwargs)
        except Exception:
            err = traceback.format_exc()
            _record(module_name, time.perf_counter() - t0, err, _detect_source(module_name))
            raise

        _record(module_name, time.perf_counter() - t0, "", _detect_source(module_name, mod))
        return mod

    _timed_enable._mmy_startup_probe = True
    addon_utils.enable = _timed_enable
    print("[MMY Probe] startup addon timer installed")


_install()
'''


_KNOWN_BLENDER_ADDONS = {
    "add_curve_extra_objects",
    "add_mesh_extra_objects",
    "amaranth",
    "animation_animall",
    "archimesh",
    "bl_pkg",
    "btrace",
    "camera_turnaround",
    "copy_global_transform",
    "curve_assign_shapekey",
    "curve_simplify",
    "cycles",
    "development_edit_operator",
    "extra_curve_objectes",
    "extra_mesh_objects",
    "io_anim_bvh",
    "io_curve_svg",
    "io_import_images_as_planes",
    "io_mesh_ply",
    "io_mesh_stl",
    "io_mesh_uv_layout",
    "io_scene_3ds",
    "io_scene_fbx",
    "io_scene_gltf2",
    "io_scene_obj",
    "io_scene_x3d",
    "lighting_dynamic_sky",
    "magic_uv",
    "materials_library_vx",
    "measureit",
    "mesh_auto_mirror",
    "mesh_f2",
    "mesh_looptools",
    "mesh_snap_utilities_line",
    "node_arrange",
    "node_wrangler",
    "object_boolean_tools",
    "object_carver",
    "object_collection_manager",
    "object_color_rules",
    "object_edit_linked",
    "object_fracture_cell",
    "object_print3d_utils",
    "object_scatter",
    "paint_palette",
    "pose_library",
    "power_sequencer",
    "render_copy_settings",
    "rigify",
    "space_clip_editor_refine_solution",
    "space_view3d_3d_navigation",
    "space_view3d_align_tools",
    "space_view3d_brush_menus",
    "space_view3d_copy_attributes",
    "space_view3d_math_vis",
    "space_view3d_pie_menus",
    "stored_views",
    "storypencil",
    "sun_position",
    "system_blend_info",
    "ui_translate",
    "viewport_vr_preview",
}


def _record_from_dict(data: dict) -> AddonLoadRecord:
    """兼容旧版 JSON，并忽略未来版本中可能新增的字段。"""
    valid_keys = {field.name for field in fields(AddonLoadRecord)}
    values = {key: value for key, value in data.items() if key in valid_keys}
    return AddonLoadRecord(**values)
