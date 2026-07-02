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
from dataclasses import dataclass, asdict


# ============================================================
# 持久化存储路径（与 pack.py 的 .pack_config.json 同级）
# ============================================================
DATA_FILE = Path(__file__).parent.parent / ".mmy_timer_data.json"


@dataclass
class AddonLoadRecord:
    name: str
    elapsed: float
    error: str = ""
    source: str = ""


class AddonTimerManager:
    def __init__(self):
        self.records: list[AddonLoadRecord] = []
        self._patched = False
        self._fallback_registered = False

    # ---- 基础操作 ----

    def record(self, name: str, elapsed: float, error: str = "", source: str = ""):
        """记录一条插件加载信息"""
        # 避免重复记录同名插件（保留耗时最长的那条）
        existing = next((r for r in self.records if r.name == name), None)
        if existing:
            if elapsed > existing.elapsed:
                existing.elapsed = elapsed
            if error and not existing.error:
                existing.error = error
            if source and not existing.source:
                existing.source = source
        else:
            self.records.append(AddonLoadRecord(name, elapsed, error, source))

    def get_records(self):
        return self.records

    # ---- 持久化 ----

    def save_data(self):
        """将当前记录保存到 JSON 文件"""
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
            self.records = [AddonLoadRecord(**d) for d in data]
            print(f"[MMY] 已加载历史耗时数据 ({len(self.records)} 条)")
            return True
        except Exception as e:
            print(f"[MMY] 加载历史耗时数据失败: {e}")
            return False

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

        def _timed_enable(module_name, *args, **kwargs):
            t0 = time.perf_counter()
            err = ""
            mod = None
            try:
                mod = original_enable(module_name, *args, **kwargs)
            except Exception:
                err = traceback.format_exc()

            elapsed = time.perf_counter() - t0
            manager.record(module_name, elapsed, err, _detect_addon_source(module_name, mod))

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
                            manager.record(mod_name, 0.0, "", _detect_addon_source(mod_name))
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
