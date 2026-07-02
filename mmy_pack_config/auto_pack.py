"""
每周自动打包 Blender Portable 配置。

使用 bpy.app.timers 做轻量轮询，真正打包交给 pack_script.py 子进程执行，
避免阻塞 Blender UI。
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import bpy

from .path_memory import get_path_memory_file
from .ui import _PACK_SCRIPT, _build_default_filename


STATE_FILE_NAME = "auto_pack_state.json"
CHECK_INTERVAL_SECONDS = 60 * 60

_timer_registered = False


def _get_state_file():
    return get_path_memory_file().parent / STATE_FILE_NAME


def _load_state():
    state_file = _get_state_file()
    if not state_file.exists():
        return {}

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[MMY] 读取自动打包状态失败: {e}")
    return {}


def _save_state(state):
    state_file = _get_state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[MMY] 保存自动打包状态失败: {e}")


def _get_preferences():
    addon = bpy.context.preferences.addons.get(__package__)
    if not addon:
        return None
    return addon.preferences


def _is_enabled(prefs):
    return bool(getattr(prefs, "enable_weekly_auto_pack", False))


def _week_key(now):
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _build_output_path(portable_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / _build_default_filename(str(portable_path))


def _start_pack_process(portable_path, output_path):
    args = [
        sys.executable,
        str(_PACK_SCRIPT),
        str(portable_path),
        str(output_path),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    subprocess.Popen(
        args,
        cwd=str(_PACK_SCRIPT.parent),
        creationflags=creationflags,
    )


def run_weekly_check(force=False):
    """
    检查是否需要自动打包。

    force=True 供 UI 测试按钮使用，会忽略“必须周一”和“本周已执行”限制。
    """
    prefs = _get_preferences()
    if not prefs:
        return False, "无法读取偏好设置"

    if not force and not _is_enabled(prefs):
        return False, "每周自动打包未启用"

    now = datetime.now()
    if not force and now.weekday() != 0:
        return False, "今天不是周一"

    portable_raw = getattr(prefs, "last_portable_path", "") or ""
    output_raw = getattr(prefs, "pack_output_path", "") or ""
    portable_path = Path(portable_raw)
    output_dir = Path(output_raw)
    if not portable_path.exists():
        return False, "上次 Portable 路径无效"
    if not output_raw.strip():
        return False, "打包输出目录未设置"

    state = _load_state()
    current_week = _week_key(now)
    if not force and state.get("last_auto_pack_week") == current_week:
        return False, "本周已自动打包"

    try:
        output_path = _build_output_path(portable_path, output_dir)
        _start_pack_process(portable_path, output_path)
    except Exception as e:
        return False, f"启动自动打包失败: {e}"

    state["last_auto_pack_week"] = current_week
    state["last_auto_pack_at"] = now.isoformat(timespec="seconds")
    state["last_portable_path"] = str(portable_path)
    state["last_output_path"] = str(output_path)
    _save_state(state)

    print(f"[MMY] 每周自动打包已启动: {output_path}")
    return True, f"已启动自动打包: {output_path.name}"


def _timer_callback():
    if not _timer_registered:
        return None

    try:
        run_weekly_check(force=False)
    except Exception as e:
        print(f"[MMY] 每周自动打包检查异常: {e}")
    return CHECK_INTERVAL_SECONDS


def register():
    global _timer_registered
    if _timer_registered:
        return

    try:
        bpy.app.timers.register(_timer_callback, first_interval=30.0)
        _timer_registered = True
        print("[MMY] 每周自动打包检查已注册")
    except Exception as e:
        print(f"[MMY] 注册每周自动打包检查失败: {e}")


def unregister():
    global _timer_registered
    try:
        if bpy.app.timers.is_registered(_timer_callback):
            bpy.app.timers.unregister(_timer_callback)
    except Exception:
        pass
    _timer_registered = False
