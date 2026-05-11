import time
import traceback
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
                try:
                    result = original(module_name, *args, **kwargs)
                except Exception:
                    err = traceback.format_exc()
                    result = None
                manager.record(module_name, time.perf_counter() - t0, err)
                return result

            addon_utils.enable = _timed_enable
            self._patched = True
        except Exception:
            pass

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
