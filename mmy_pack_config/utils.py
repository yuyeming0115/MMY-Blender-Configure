"""
工具函数：只保留 Portable 模式检测
（配置打包/解包逻辑已迁移到 pack.py）
"""

import sys
import bpy
from pathlib import Path


def get_portable_base_dir():
    """
    获取 Blender 的 portable 基础目录（portable/ 所在位置）
    
    返回：
        Path 对象，如果不在 portable 模式则返回 None
    """
    # Blender 可执行文件所在目录
    blender_exe = Path(sys.executable).resolve()
    blender_dir = blender_exe.parent

    portable_dir = blender_dir / "portable"
    if portable_dir.exists() and portable_dir.is_dir():
        return blender_dir
    
    return None


def is_portable_mode():
    """
    检测当前 Blender 是否运行在 Portable 模式
    
    返回：
        bool：True = Portable 模式，False = 普通模式
    """
    return get_portable_base_dir() is not None


def get_blender_version():
    """
    获取当前 Blender 版本号字符串
    
    返回：
        版本字符串（如 "4.5.0"）
    """
    version = bpy.app.version
    return f"{version[0]}.{version[1]}.{version[2]}"


def get_addons_dir():
    """
    获取当前 Blender 的插件目录路径（兼容 Portable / 普通模式）
    
    返回：
        Path 对象
    """
    if is_portable_mode():
        base = get_portable_base_dir()
        return base / "portable" / "scripts" / "addons"
    else:
        # 普通模式：%APPDATA%/Blender Foundation/Blender/X.X/scripts/addons/
        version = bpy.app.version_string.split()[0]  # "4.5.0"
        return Path(bpy.utils.user_resource('SCRIPTS', path='addons'))


# ============================================================
# 以下是 pack.py 可能用到的辅助函数（在 Blender 外运行时不可用）
# ============================================================

def format_file_size(size_bytes):
    """
    格式化文件大小（人类可读）
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"
