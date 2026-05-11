import sys
import time
import traceback
import bpy
from dataclasses import dataclass, field


@dataclass
class AddonLoadRecord:
    name: str
    elapsed: float
    error: str = ""


class AddonTimerManager:
    def __init__(self):
        self.records: list[AddonLoadRecord] = []
        self._patched = False
        self._fallback_registered = False

    def record(self, name: str, elapsed: float, error: str = ""):
        self.records.append(AddonLoadRecord(name, elapsed, error))

    def get_records(self):
        return self.records

    def patch(self):
        if self._patched:
            return
        try:
            from bpy.utils import _addon_utils as addon_utils
            original = addon_utils.enable

            manager = self

            def _timed_enable(module_name, *args, **kwargs):
                t0 = time.perf_counter()
                err = ""
                mod = None
                try:
                    mod = original(module_name, *args, **kwargs)
                except Exception:
                    err = traceback.format_exc()
                elapsed = time.perf_counter() - t0
                # mod 为 None 或加载失败才记为错误
                if not mod or err:
                    manager.record(module_name, elapsed, err or "模块加载返回 None")
                else:
                    manager.record(module_name, elapsed)
                return mod

            addon_utils.enable = _timed_enable
            self._patched = True
        except Exception:
            pass

    def register_fallback(self):
        """注册兜底 timer，延迟扫描未被 monkey-patch 捕获的 addon。"""
        if self._fallback_registered:
            return
        self._fallback_registered = True

        manager = self

        def _scan_early_addons():
            try:
                prefs = bpy.context.preferences.addons
                recorded = {r.name for r in manager.records}
                for mod_name in prefs.keys():
                    if mod_name not in recorded and mod_name in sys.modules:
                        manager.record(mod_name, 0.0, "早期加载（未在监控注入前完成）")
            except Exception:
                pass
            return None  # 只执行一次

        bpy.app.timers.register(lambda: _scan_early_addons(), first_interval=2.0)

    def unpatch(self):
        if not self._patched:
            return
        try:
            from bpy.utils import _addon_utils as addon_utils
            import importlib
            importlib.reload(addon_utils)
            self._patched = False
        except Exception:
            pass


manager = AddonTimerManager()
