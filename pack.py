#!/usr/bin/env python3
"""
MMY Blender Configure 打包脚本 - 重构版
打包 Blender portable/ 配置文件夹，支持动态路径选择、自动版本检测、时间戳命名
"""

import json
import os
import re
import sys
import zipfile
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog


CONFIG_FILE = Path(__file__).parent / ".pack_config.json"


# ============ 配置文件管理 ============

def load_config():
    """加载配置文件，如果不存在则返回默认值"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"警告：无法读取配置文件，使用默认配置。错误：{e}")
    
    return {
        "last_portable_path": "",
        "last_output_dir": str(Path(__file__).parent / "releases")
    }


def save_config(config):
    """保存配置到文件"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"警告：无法保存配置文件。错误：{e}")


# ============ 版本检测 ============

def get_blender_version(portable_path):
    """
    自动检测 Blender 版本号（三层方案）
    
    返回：版本字符串（如 "4.5.0"），如果检测失败则返回 None
    """
    portable_path = Path(portable_path)
    
    # 方法1：从父目录名称提取版本（如 "Blender 4.5/portable/"）
    parent_name = portable_path.parent.name
    version_match = re.search(r'(\d+\.\d+(\.\d+)?)', parent_name)
    if version_match:
        version = version_match.group(1)
        # 确保是 3 段版本号（如 4.5 → 4.5.0）
        if version.count('.') == 1:
            version += ".0"
        return version
    
    # 方法2：检查 portable/ 下的版本命名子目录（如 portable/4.5/）
    try:
        for item in portable_path.iterdir():
            if item.is_dir() and re.match(r'\d+\.\d+', item.name):
                version = item.name
                if version.count('.') == 1:
                    version += ".0"
                return version
    except Exception:
        pass
    
    # 方法3：无法检测，返回 None（让调用者处理）
    return None


def ask_user_for_version():
    """弹出对话框让用户输入版本号"""
    root = tk.Tk()
    root.withdraw()
    
    version = simpledialog.askstring(
        title="输入 Blender 版本",
        prompt="无法自动检测 Blender 版本号。\n请手动输入（如 4.5.0）："
    )
    
    root.destroy()
    
    if version:
        # 验证格式
        if re.match(r'\d+\.\d+(\.\d+)?', version):
            if version.count('.') == 1:
                version += ".0"
            return version
    
    return "unknown"


# ============ 路径选择 ============

def select_portable_folder(config):
    """
    弹出文件夹选择对话框，让用户选择 Blender portable/ 文件夹
    
    返回：选择的文件夹路径，如果取消则返回 None
    """
    root = tk.Tk()
    root.withdraw()
    
    initial_dir = config.get("last_portable_path", "")
    if not initial_dir or not Path(initial_dir).exists():
        initial_dir = str(Path.home())
    
    folder_path = filedialog.askdirectory(
        title="选择 Blender portable 文件夹",
        initialdir=initial_dir
    )
    
    root.destroy()
    
    if folder_path:
        # 保存到配置
        config["last_portable_path"] = folder_path
        save_config(config)
        return folder_path
    
    return None


def select_output_path(config, default_name):
    """
    弹出保存文件对话框，让用户选择 zip 输出路径
    
    返回：选择的文件路径，如果取消则返回 None
    """
    root = tk.Tk()
    root.withdraw()
    
    initial_dir = config.get("last_output_dir", str(Path(__file__).parent / "releases"))
    if not Path(initial_dir).exists():
        initial_dir = str(Path.home())
    
    output_path = filedialog.asksaveasfilename(
        title="保存 ZIP 文件",
        initialdir=initial_dir,
        initialfile=default_name,
        defaultextension=".zip",
        filetypes=[("ZIP 文件", "*.zip"), ("所有文件", "*.*")]
    )
    
    root.destroy()
    
    if output_path:
        # 保存到配置
        config["last_output_dir"] = str(Path(output_path).parent)
        save_config(config)
        return output_path
    
    return None


# ============ 文件排除逻辑 ============

def should_exclude(file_path, exclude_patterns):
    """
    检查文件是否应该被排除
    
    Args:
        file_path: Path 对象
        exclude_patterns: 排除模式列表（如 ["__pycache__", "*.pyc"]）
    
    返回：如果应该排除则返回 True，否则返回 False
    """
    file_path_str = str(file_path).replace("\\", "/")
    file_name = file_path.name
    
    for pattern in exclude_patterns:
        # 如果是目录名模式（如 "__pycache__"）
        if pattern in file_path.parts:
            return True
        
        # 如果是通配符模式（如 "*.pyc"）
        if pattern.startswith("*"):
            if file_name.endswith(pattern[1:]):
                return True
        
        # 如果是精确匹配（如 ".git"）
        if file_name == pattern or pattern in file_path_str:
            return True
    
    return False


# ============ 自动重命名 ============

def get_unique_output_path(output_path):
    """
    如果文件已存在，自动重命名（如 file (1).zip, file (2).zip）
    
    返回：唯一的文件路径
    """
    path = Path(output_path)
    
    if not path.exists():
        return output_path
    
    counter = 1
    while True:
        new_name = f"{path.stem} ({counter}){path.suffix}"
        new_path = path.parent / new_name
        
        if not new_path.exists():
            return str(new_path)
        
        counter += 1


# ============ 打包逻辑 ============

def pack_portable(portable_path, output_path):
    """
    打包整个 Blender portable/ 文件夹
    
    Args:
        portable_path: portable 文件夹路径
        output_path: 输出的 zip 文件路径
    
    返回：输出的 zip 文件路径
    """
    portable_path = Path(portable_path)
    
    if not portable_path.exists():
        raise FileNotFoundError(f"portable 文件夹不存在：{portable_path}")
    
    # 排除规则
    exclude_patterns = [
        "__pycache__",    # Python 缓存
        "*.pyc",          # Python 编译文件
        "*.pyo",
        ".git",           # Git 仓库
        "*.log",          # 日志文件
        "*.tmp",          # 临时文件
        "thumbs.db",      # Windows 缩略图缓存
        ".ds_store"       # macOS 元数据
    ]
    
    # 统计信息
    total_files = 0
    packed_files = 0
    
    # 先统计总文件数（用于进度显示）
    for file_path in portable_path.rglob("*"):
        if file_path.is_file():
            total_files += 1
    
    print(f"开始打包 {portable_path}")
    print(f"总文件数：{total_files}")
    print(f"输出到：{output_path}\n")
    
    # 打包
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in portable_path.rglob("*"):
            if file_path.is_file():
                # 检查是否应该排除
                if should_exclude(file_path, exclude_patterns):
                    continue
                
                # 计算相对路径（保留目录结构）
                arcname = file_path.relative_to(portable_path.parent)
                arcname_str = str(arcname).replace("\\", "/")
                
                # 写入 zip
                zf.write(file_path, arcname_str)
                packed_files += 1
                
                # 进度显示（每 100 个文件显示一次）
                if packed_files % 100 == 0:
                    print(f"  已打包 {packed_files}/{total_files} 文件...")
    
    # 完成信息
    output_size = Path(output_path).stat().st_size / 1024 / 1024  # MB
    print(f"\n✅ 打包完成！")
    print(f"  输出文件：{output_path}")
    print(f"  文件大小：{output_size:.2f} MB")
    print(f"  打包文件数：{packed_files}/{total_files}")
    
    return output_path


# ============ 主函数 ============

def main():
    """主函数"""
    print("=" * 60)
    print("MMY Blender Configure - Portable 打包工具")
    print("=" * 60 + "\n")
    
    # 加载配置
    config = load_config()
    
    # 1. 选择 portable 文件夹
    print("[1/4] 选择 Blender portable 文件夹...")
    portable_path = select_portable_folder(config)
    
    if not portable_path:
        print("❌ 已取消选择。")
        sys.exit(0)
    
    print(f"  已选择：{portable_path}\n")
    
    # 2. 检测 Blender 版本号
    print("[2/4] 检测 Blender 版本号...")
    blender_version = get_blender_version(portable_path)
    
    if not blender_version:
        print("  ⚠️  无法自动检测版本号，需要手动输入...")
        blender_version = ask_user_for_version()
    
    print(f"  版本号：{blender_version}\n")
    
    # 3. 生成输出文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    default_name = f"Blender_Portable_v{blender_version}_{timestamp}.zip"
    print(f"[3/4] 生成输出文件名：{default_name}\n")
    
    # 4. 选择输出路径
    print("[4/4] 选择输出路径...")
    output_path = select_output_path(config, default_name)
    
    if not output_path:
        print("❌ 已取消选择。")
        sys.exit(0)
    
    print(f"  输出到：{output_path}\n")
    
    # 5. 检查文件是否存在，自动重命名
    output_path = get_unique_output_path(output_path)
    if output_path != Path(output_path).parent / default_name:
        print(f"⚠️  文件已存在，自动重命名为：{Path(output_path).name}\n")
    
    # 6. 执行打包
    try:
        pack_portable(portable_path, output_path)
    except Exception as e:
        print(f"\n❌ 打包失败：{e}")
        messagebox.showerror("打包失败", str(e))
        sys.exit(1)
    
    # 7. 完成
    messagebox.showinfo("打包完成", f"打包成功！\n\n输出文件：{output_path}")
    
    print("\n" + "=" * 60)
    print("打包任务完成！")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  已取消打包。")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ 发生错误：{e}")
        import traceback
        traceback.print_exc()
        messagebox.showerror("错误", str(e))
        sys.exit(1)
