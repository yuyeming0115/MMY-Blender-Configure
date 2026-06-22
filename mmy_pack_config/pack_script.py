#!/usr/bin/env python3
"""
MMY Blender Configure — 打包脚本（库模式）

支持两种运行方式：
  1. 命令行带参数（推荐，从无 tkinter 环境调用）：
       python pack_script.py <portable_path> [output_path]
  2. 命令行无参数（tkinter 交互模式，需要 tkinter）：
       python pack_script.py

作为库调用：
   from .pack_script import pack_portable
   pack_portable(portable_path, output_path)
"""

import json
import re
import sys
import zipfile
from pathlib import Path
from datetime import datetime


CONFIG_FILE = Path(__file__).parent.parent / ".pack_config.json"


# ============================================================
# 配置文件
# ============================================================

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[MMY] 警告：无法读取配置文件：{e}")
    return {
        "last_portable_path": "",
        "last_output_dir": str(Path(__file__).parent.parent / "releases"),
    }


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[MMY] 警告：无法保存配置文件：{e}")


# ============================================================
# 版本检测
# ============================================================

def get_blender_version(portable_path, version_override=None):
    if version_override:
        v = version_override
        if v.count(".") == 1:
            v += ".0"
        return v

    portable_path = Path(portable_path)
    parent_name = portable_path.parent.name
    m = re.search(r"(\d+\.\d+(\.\d+)?)", parent_name)
    if m:
        v = m.group(1)
        if v.count(".") == 1:
            v += ".0"
        return v

    try:
        for item in portable_path.iterdir():
            if item.is_dir() and re.match(r"\d+\.\d+", item.name):
                v = item.name
                if v.count(".") == 1:
                    v += ".0"
                return v
    except Exception:
        pass

    return None


# ============================================================
# 文件排除
# ============================================================

EXCLUDE_PATTERNS = [
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".git",
    "*.log",
    "*.tmp",
    "thumbs.db",
    "._.ds_store",
    ".ds_store",
]


def should_exclude(file_path):
    file_path_str = str(file_path).replace("\\", "/")
    file_name = file_path.name
    for pattern in EXCLUDE_PATTERNS:
        if pattern in file_path.parts:
            return True
        if pattern.startswith("*"):
            if file_name.endswith(pattern[1:]):
                return True
        if file_name == pattern or pattern in file_path_str:
            return True
    return False


# ============================================================
# 打包核心
# ============================================================

def pack_portable(portable_path, output_path):
    """
    打包整个 Blender portable/ 文件夹。

    Args:
        portable_path: portable 文件夹路径（str 或 Path）
        output_path: 输出的 zip 文件路径（str 或 Path）

    Returns:
        output_path 的 Path 对象

    Raises:
        FileNotFoundError: portable 文件夹不存在
    """
    portable_path = Path(portable_path)
    output_path = Path(output_path)

    if not portable_path.exists():
        raise FileNotFoundError(f"portable 文件夹不存在：{portable_path}")

    if not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    total_files = sum(1 for _ in portable_path.rglob("*") if _.is_file())
    packed_files = 0

    print(f"[MMY] 开始打包 {portable_path}")
    print(f"[MMY] 总文件数：{total_files}")
    print(f"[MMY] 输出到：{output_path}")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in portable_path.rglob("*"):
            if not file_path.is_file():
                continue
            if should_exclude(file_path):
                continue

            arcname = file_path.relative_to(portable_path.parent)
            arcname_str = str(arcname).replace("\\", "/")
            zf.write(file_path, arcname_str)
            packed_files += 1

            if packed_files % 500 == 0:
                print(f"[MMY]   已打包 {packed_files}/{total_files}...")

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"[MMY] ✅ 打包完成！")
    print(f"[MMY]   文件：{output_path}")
    print(f"[MMY]   大小：{size_mb:.2f} MB")
    print(f"[MMY]   数量：{packed_files}/{total_files}")
    return output_path


# ============================================================
# 自动重命名
# ============================================================

def get_unique_output_path(output_path):
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


# ============================================================
# tkinter 交互模式（仅当无命令行参数时调用）
# ============================================================

def _run_tkinter_ui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog

    config = load_config()

    # 1. 选择 portable 文件夹
    root = tk.Tk()
    root.withdraw()

    initial_dir = config.get("last_portable_path", str(Path.home()))
    portable_path = filedialog.askdirectory(
        title="选择 Blender portable 文件夹",
        initialdir=initial_dir,
    )
    if not portable_path:
        print("已取消。")
        sys.exit(0)

    config["last_portable_path"] = portable_path
    save_config(config)

    # 2. 检测版本号
    version = get_blender_version(portable_path)
    if not version:
        root.deiconify()
        version = simpledialog.askstring(
            title="输入 Blender 版本",
            prompt="无法自动检测版本号，请手动输入（如 4.5.0）：",
        )
        root.withdraw()
        if version:
            if version.count(".") == 1:
                version += ".0"
        else:
            version = "unknown"

    # 3. 生成输出文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    default_name = f"Blender_Portable_v{version}_{timestamp}.zip"

    initial_dir = config.get("last_output_dir", str(Path.home()))
    output_path = filedialog.asksaveasfilename(
        title="保存 ZIP 文件",
        initialdir=initial_dir,
        initialfile=default_name,
        defaultextension=".zip",
        filetypes=[("ZIP 文件", "*.zip")],
    )
    if not output_path:
        print("已取消。")
        sys.exit(0)

    config["last_output_dir"] = str(Path(output_path).parent)
    save_config(config)

    # 4. 自动重命名
    output_path = get_unique_output_path(output_path)

    # 5. 执行打包
    try:
        result = pack_portable(portable_path, output_path)
        messagebox.showinfo("打包完成", f"输出文件：\n{result}")
        print(f"[MMY] 结果：{result}")
    except Exception as e:
        messagebox.showerror("打包失败", str(e))
        print(f"[MMY] 错误：{e}")
        sys.exit(1)


# ============================================================
# 命令行入口
# ============================================================

def main():
    print("=" * 60)
    print("MMY Blender Configure — Portable 打包工具")
    print("=" * 60)

    if len(sys.argv) >= 2:
        # ---- 命令行参数模式（无 tkinter 依赖）----
        portable_path = sys.argv[1]
        output_path = sys.argv[2] if len(sys.argv) >= 3 else None
        version_override = None
        for i, arg in enumerate(sys.argv):
            if arg == "--version" and i + 1 < len(sys.argv):
                version_override = sys.argv[i + 1]

        if not Path(portable_path).exists():
            print(f"[MMY] ❌ portable 文件夹不存在：{portable_path}")
            sys.exit(1)

        # 检测/使用版本号
        version = get_blender_version(portable_path, version_override)
        if not version:
            print("[MMY] ⚠️  无法检测版本号，使用 'unknown'")
            version = "unknown"

        # 生成输出路径
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            default_name = f"Blender_Portable_v{version}_{timestamp}.zip"
            config = load_config()
            out_dir = config.get("last_output_dir", ".")
            output_path = str(Path(out_dir) / default_name)

        output_path = get_unique_output_path(output_path)

        # 执行打包
        try:
            result = pack_portable(portable_path, output_path)
            print(f"[MMY] ✅ 结果：{result}")
        except Exception as e:
            print(f"[MMY] ❌ 错误：{e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        # ---- 无参数：tkinter 交互模式 ----
        try:
            _run_tkinter_ui()
        except Exception as e:
            print(f"[MMY] ❌ tkinter 模式失败（可能未安装 tkinter）：{e}")
            print("[MMY] 请使用命令行参数模式：")
            print("  python pack_script.py <portable路径> [输出路径]")
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[MMY] 已取消。")
        sys.exit(0)
    except Exception as e:
        print(f"\n[MMY] ❌ 发生错误：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
