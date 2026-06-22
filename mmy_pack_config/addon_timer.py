"""
插件加载耗时监控（monkey-patch bpy.utils._addon_utils.enable）

修复说明：
1. 修复 dataclasses 拼写错误
2. 修复 record() 调用缺少逗号的语法错误
3. 修复错误判断：只在有异常时才记 ERROR（mod 为 None 可能是正常行为）
4. 增强健壮性：处理 _addon_utils 不存在或签名变化的情况
"""

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
        """Monkey-patch bpy.utils._addon_utils.enable 以监控加载耗时"""
        if self._patched:
            return

        try:
            from bpy.utils import _addon_utils as addon_utils
            original_enable = addon_utils.enable

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

                # 只在有异常时才记为错误
                # mod 为 None 可能是正常行为（很多插件不返回 module）
                if err:
                    manager.record(module_name, elapsed, err)
                else:
                    manager.record(module_name, elapsed)

                return mod

            addon_utils.enable = _timed_enable
            self._patched = True
            print("[MMY] AddonTimerManager: monkey-patch 注入成功")

        except ImportError:
            print("[MMY] AddonTimerManager: 无法导入 _addon_utils（可能 Blender 版本不兼容）")
        except AttributeError:
            print("[MMY] AddonTimerManager: _addon_utils.enable 不存在（可能 Blender 版本不兼容）")
        except Exception as e:
            print(f"[MMY] AddonTimerManager: monkey-patch 失败: {e}")

    def register_fallback(self):
        """注册兜底 timer，延迟扫描未被 monkey-patch 捕获的 addon"""
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
                        # 早期加载的插件，耗时记为 0（无法精确测量）
                        manager.record(mod_name, 0.0, "")
            except Exception as e:
                print(f"[MMY] 扫描早期插件失败: {e}")
            return None  # 只执行一次

        try:
            bpy.app.timers.register(lambda: _scan_early_addons(), first_interval=2.0)
        except Exception as e:
            print(f"[MMY] 注册兜底 timer 失败: {e}")

    def unpatch(self):
        """恢复原始的 _addon_utils.enable"""
        if not self._patched:
            return
        try:
            from bpy.utils import _addon_utils as addon_utils
            import importlib
            importlib.reload(addon_utils)
            self._patched = False
            print("[MMY] AddonTimerManager: monkey-patch 已恢复")
        except Exception as e:
            print(f"[MMY] AddonTimerManager: 恢复失败: {e}")


manager = AddonTimerManager()
