#!/usr/bin/env python3
"""
MMY Blender Configure — 打包脚本（库模式）

支持两种运行方式：
  1. 命令行带参数（推荐，从无 tkinter 环境调用）：
       python pack_script.py <portable_path> [output_path]
  2. 无参数 fallback 到 tkinter（仅限有 tkinter 的环境）

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
        "last_output_dir": str(Path.home() / "Desktop"),
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
    if version_override and version_override.strip():
        v = version_override.strip()
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
    "__pycache__",      # Python 缓存目录
    "*.pyc",           # Python 编译文件
    "*.pyo",           # Python 优化编译文件
    ".git",             # Git 仓库元数据
    "*.log",            # 日志文件
    "*.tmp",            # 临时文件
    "thumbs.db",        # Windows 缩略图缓存
    "._*",              # macOS 资源派生文件
    ".ds_store",        # macOS Finder 元数据
]

# 用于统计排除原因
_EXCLUDE_DESCRIPTION = {
    "__pycache__":  "Python 缓存目录",
    "*.pyc":        "Python 编译文件 (.pyc)",
    "*.pyo":        "Python 优化编译文件 (.pyo)",
    ".git":          "Git 仓库元数据",
    "*.log":         "日志文件 (.log)",
    "*.tmp":         "临时文件 (.tmp)",
    "thumbs.db":     "Windows 缩略图缓存",
    "._*":           "macOS 资源派生文件",
    ".ds_store":     "macOS Finder 元数据",
}


def should_exclude(file_path, exclude_patterns=None):
    """
    检查文件是否应被排除。

    返回：(是否排除, 排除原因描述) 的元组。
    不排除时返回 (False, None)。
    """
    if exclude_patterns is None:
        exclude_patterns = EXCLUDE_PATTERNS

    file_path_str = str(file_path).replace("\\", "/")
    file_name = file_path.name

    import fnmatch

    for pattern in exclude_patterns:
        # 1) 目录名精确匹配（如 __pycache__ 出现在路径任一层）
        if pattern in file_path.parts:
            return True, _EXCLUDE_DESCRIPTION.get(pattern, pattern)

        # 2) 通配符文件模式（如 *.pyc、._*）
        if "*" in pattern:
            if fnmatch.fnmatch(file_name, pattern):
                return True, _EXCLUDE_DESCRIPTION.get(pattern, pattern)

        # 3) 精确文件名匹配（如 .ds_store、.git）
        if file_name == pattern or pattern in file_path_str:
            return True, _EXCLUDE_DESCRIPTION.get(pattern, pattern)

    return False, None


# ============================================================
# 输出路径规范化：确保是 .zip 文件路径
# ============================================================

def normalize_output_path(output_path, portable_path=None, version=None):
    """
    将任意输入规范化为合法的 .zip 文件路径。

    支持的输入类型：
      - 空/None          → 自动生成到桌面
      - 目录路径         → 在该目录下生成文件名
      - 完整文件路径     → 直接使用（补齐 .zip 后缀）
      - 无后缀的文件名   → 补上 .zip 后缀
    """
    if not output_path or not output_path.strip():
        # 自动生成
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        ver = version or "unknown"
        default_name = f"Blender_Portable_v{ver}_{timestamp}.zip"
        config = load_config()
        out_dir = config.get("last_output_dir", str(Path.home() / "Desktop"))
        return str(Path(out_dir) / default_name)

    p = Path(output_path)

    # 如果看起来是目录（以 \ 或 / 结尾，或已存在且是目录）
    if p.is_dir() or (not p.suffix and not p.name.endswith(".zip")):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        ver = version or "unknown"
        base_dir = p if p.is_dir() else p.parent
        default_name = f"Blender_Portable_v{ver}_{timestamp}.zip"
        return str(base_dir / default_name)

    # 有后缀但不是 .zip
    if p.suffix.lower() != ".zip":
        return str(p.with_suffix(".zip"))

    # 正常的 .zip 文件路径
    return output_path


# ============================================================
# 自动重命名（避免覆盖）
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
# 打包核心（快速模式：STORED 不压缩）
# ============================================================

def pack_portable(portable_path, output_path, compress=False, exclude_patterns=None):
    """
    打包整个 Blender portable/ 文件夹。

    Args:
        portable_path:     portable 文件夹路径
        output_path:    输出的 zip 文件路径
        compress:         是否启用 ZIP_DEFLATED 压缩（默认关闭，速度快 5-10x）
        exclude_patterns: 排除模式列表。None 时使用默认 EXCLUDE_PATTERNS；
                           传 [] 或 --all 则不排除任何文件
    Returns:
        output_path 的 Path 对象
    Raises:
        FileNotFoundError: portable 文件夹不存在
    """
    portable_path = Path(portable_path)
    output_path = Path(output_path)

    if not portable_path.exists():
        raise FileNotFoundError(f"portable 文件夹不存在：{portable_path}")

    # 确定排除规则
    if exclude_patterns is None:
        exclude_patterns = EXCLUDE_PATTERNS
    use_exclude = len(exclude_patterns) > 0

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 统计
    total_files = sum(1 for _ in portable_path.rglob("*") if _.is_file())
    packed_files = 0
    skipped_files = 0
    exclude_reasons = {}   # {原因描述: 计数}

    compression = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    mode_label = "压缩" if compress else "存储（快速）"

    print(f"[MMY] 开始打包 {portable_path}")
    print(f"[MMY] 总文件数：{total_files}")
    print(f"[MMY] 输出到：{output_path}")
    print(f"[MMY] 模式：{mode_label}")
    if use_exclude:
        print(f"[MMY] 排除规则：{len(exclude_patterns)} 类")
    else:
        print(f"[MMY] 排除规则：无（--all 模式，包含所有文件）")

    with zipfile.ZipFile(output_path, "w", compression) as zf:
        for file_path in portable_path.rglob("*"):
            if not file_path.is_file():
                continue
            if use_exclude:
                excluded, reason = should_exclude(file_path, exclude_patterns)
                if excluded:
                    skipped_files += 1
                    exclude_reasons[reason] = exclude_reasons.get(reason, 0) + 1
                    continue

            arcname = file_path.relative_to(portable_path.parent)
            arcname_str = str(arcname).replace("\\", "/")
            zf.write(file_path, arcname_str)
            packed_files += 1

            if packed_files % 1000 == 0:
                pct = packed_files * 100 // max(total_files, 1)
                print(f"[MMY]   进度：{packed_files}/{total_files} ({pct}%)...")

    size_mb = output_path.stat().st_size / 1024 / 1024

    # ---- 打印排除摘要 ----
    print()
    if exclude_reasons:
        print(f"[MMY] 排除摘要（共 {skipped_files} 个文件）：")
        for reason, count in sorted(exclude_reasons.items(), key=lambda x: -x[1]):
            print(f"[MMY]   - {reason}：{count} 个")
    else:
        print(f"[MMY] （无排除文件）")

    print(f"[MMY] ✅ 打包完成！")
    print(f"[MMY]   文件：{output_path.name}")
    print(f"[MMY]   大小：{size_mb:.1f} MB")
    print(f"[MMY]   已打包：{packed_files}  已跳过：{skipped_files}  总计：{total_files}")
    return output_path


# ============================================================
# tkinter 交互模式
# ============================================================

def _run_tkinter_ui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog

    config = load_config()

    root = tk.Tk()
    root.withdraw()

    portable_path = filedialog.askdirectory(
        title="选择 Blender portable 文件夹",
        initialdir=config.get("last_portable_path", str(Path.home())),
    )
    if not portable_path:
        sys.exit(0)

    config["last_portable_path"] = portable_path
    save_config(config)

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
        sys.exit(0)

    config["last_output_dir"] = str(Path(output_path).parent)
    save_config(config)

    output_path = get_unique_output_path(output_path)

    try:
        result = pack_portable(portable_path, output_path)
        messagebox.showinfo("打包完成", f"输出文件：\n{result}")
    except Exception as e:
        messagebox.showerror("打包失败", str(e))
        sys.exit(1)


# ============================================================
# 命令行入口
# ============================================================

def main():
    print("=" * 60)
    print("MMY Blender Configure — Portable 打包工具")
    print("=" * 60)

    if len(sys.argv) >= 2:
        portable_path = sys.argv[1]
        raw_output = sys.argv[2] if len(sys.argv) >= 3 else None
        version_override = None
        use_compress = "--compress" in sys.argv
        use_all = "--all" in sys.argv       # 不排除任何文件

        for i, arg in enumerate(sys.argv):
            if arg == "--version" and i + 1 < len(sys.argv):
                version_override = sys.argv[i + 1]

        if not Path(portable_path).exists():
            print(f"[MMY] ❌ portable 文件夹不存在：{portable_path}")
            sys.exit(1)

        version = get_blender_version(portable_path, version_override)
        if not version:
            print("[MMY] ⚠️  无法检测版本号，使用 'unknown'")
            version = "unknown"

        # 规范化输出路径
        output_path = normalize_output_path(raw_output, portable_path, version)
        output_path = get_unique_output_path(output_path)

        # 确定排除规则
        exclude_patterns = [] if use_all else None

        try:
            result = pack_portable(portable_path, output_path,
                                 compress=use_compress,
                                 exclude_patterns=exclude_patterns)
            print(f"\n[MMY] ✅ 结果：{result}")
        except Exception as e:
            print(f"[MMY] ❌ 错误：{e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        try:
            _run_tkinter_ui()
        except Exception as e:
            print(f"[MMY] ❌ tkinter 失败：{e}")
            print('[MMY] 用法：python pack_script.py <portable> [output] [--compress]')
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[MMY] 已取消。")
        sys.exit(0)
