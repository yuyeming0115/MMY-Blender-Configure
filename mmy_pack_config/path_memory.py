"""
打包路径记忆：保存到 Blender scripts/presets，避免更新插件后丢失。
"""

import json
from pathlib import Path

import bpy


PRESET_DIR_NAME = "mmy_blender_configure"
PRESET_FILE_NAME = "path_memory.json"
_KEY_PORTABLE = "last_portable_path"
_KEY_OUTPUT = "pack_output_path"


def get_path_memory_file():
    """返回路径记忆预设文件。Portable 模式下位于 portable/scripts/presets/。"""
    preset_dir = Path(
        bpy.utils.user_resource(
            "SCRIPTS",
            path=f"presets/{PRESET_DIR_NAME}",
            create=True,
        )
    )
    return preset_dir / PRESET_FILE_NAME


def load_path_memory():
    """读取路径记忆。文件不存在或损坏时返回空配置。"""
    preset_file = get_path_memory_file()
    if not preset_file.exists():
        return {}

    try:
        with open(preset_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[MMY] 读取路径预设失败: {e}")

    return {}


def save_path_memory(last_portable_path="", pack_output_path=""):
    """保存路径记忆到 presets 目录。"""
    preset_file = get_path_memory_file()
    preset_file.parent.mkdir(parents=True, exist_ok=True)

    data = load_path_memory()
    if last_portable_path is not None:
        data[_KEY_PORTABLE] = str(last_portable_path)
    if pack_output_path is not None:
        data[_KEY_OUTPUT] = str(pack_output_path)

    try:
        with open(preset_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[MMY] 保存路径预设失败: {e}")


def apply_path_memory(preferences):
    """把预设中的路径填回 AddonPreferences。"""
    data = load_path_memory()
    if not data:
        return

    portable_path = data.get(_KEY_PORTABLE, "")
    output_path = data.get(_KEY_OUTPUT, "")

    if portable_path and not preferences.last_portable_path:
        preferences.last_portable_path = portable_path
    if output_path and not preferences.pack_output_path:
        preferences.pack_output_path = output_path
