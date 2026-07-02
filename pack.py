#!/usr/bin/env python3
"""
MMY Blender Configure — 打包工具（双重功能）

支持两种打包模式：
1. 无参数 → 打包插件本身（mmy_pack_config/ → releases/）
2. 有参数 → 打包 Blender portable/ 文件夹

用法：
  python pack.py                          # 打包插件本身
  python pack.py <portable_path>         # 打包 portable/ 文件夹（交互模式）
  python pack.py <portable_path> [output] # 命令行模式
"""

import json
import re
import sys
import zipfile
from pathlib import Path
from datetime import datetime


# ============================================================
# 通用配置
# ============================================================

CONFIG_FILE = Path(__file__).parent / ".pack_config.json"
ADDON_DIR = Path(__file__).parent / "mmy_pack_config"
RELEASES_DIR = Path(__file__).parent / "releases"


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
# 功能1：打包插件本身（无参数时）
# ============================================================

def get_addon_version():
    """从 mmy_pack_config/__init__.py 读取版本号"""
    init_file = ADDON_DIR / "__init__.py"
    if not init_file.exists():
        return "1.0.0"
    for line in init_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith('"version"'):
            # 提取 (1, 0, 0) 这样的元组
            m = re.search(r"\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", line)
            if m:
                return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return "1.0.0"


def pack_addon():
    """
    打包插件本身（mmy_pack_config/ → releases/）

    符合 AGENTS.md 的自动打包机制：
    - 读取版本号：mmy_pack_config/__init__.py 的 bl_info["version"]
    - 时间戳：%Y%m%d_%H%M
    - 输出：releases/MMY_Blender_Toolkit_v{版本}_{时间戳}.zip
    - 排除：__pycache__/*.pyc
    """
    version = get_addon_version()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    zip_name = f"MMY_Blender_Toolkit_v{version}_{timestamp}.zip"
    
    RELEASES_DIR.mkdir(exist_ok=True)
    output_path = RELEASES_DIR / zip_name
    
    # 自动重命名（避免覆盖）
    counter = 1
    while output_path.exists():
        new_name = f"MMY_Blender_Toolkit_v{version}_{timestamp}_({counter}).zip"
        output_path = RELEASES_DIR / new_name
        counter += 1
    
    print("=" * 60)
    print("MMY Blender Configure — 打包插件本身")
    print("=" * 60)
    print(f"[1/3] 版本号：{version}")
    print(f"[2/3] 输出文件：{output_path.name}")
    
    # 统计文件数
    all_files = []
    for f in ADDON_DIR.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts and not f.name.endswith(".pyc"):
            all_files.append(f)
    
    print(f"[3/3] 打包 {len(all_files)} 个文件...")
    print()
    
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in all_files:
            arcname = Path("mmy_pack_config") / file_path.relative_to(ADDON_DIR)
            arcname_str = str(arcname).replace("\\", "/")
            zf.write(file_path, arcname_str)
    
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"[MMY] [OK] 打包完成！")
    print(f"[MMY]   文件：{output_path}")
    print(f"[MMY]   大小：{size_mb:.1f} MB")
    print(f"[MMY]   插件包可拖入 Blender 偏好设置 → 插件 → 安装...")
    return output_path


# ============================================================
# 功能2：打包 portable/ 文件夹（有参数时）
# ============================================================

EXCLUDE_PATTERNS = [
    "__pycache__",      # Python 缓存目录
    ".cache",           # 通用缓存目录
    ".pytest_cache",    # pytest 缓存
    ".mypy_cache",      # mypy 缓存
    ".ruff_cache",      # ruff 缓存
    "cache",            # 通用缓存目录
    "caches",           # 通用缓存目录
    "cacheddata",       # Chromium/Electron 缓存
    "code cache",       # Chromium/Electron 缓存
    "gpucache",         # GPU 缓存
    "crashpad",         # 崩溃报告缓存
    "logs",             # 日志目录
    "tmp",              # 临时目录
    "temp",             # 临时目录
    "*.pyc",           # Python 编译文件
    "*.pyo",           # Python 优化编译文件
    ".git",             # Git 仓库元数据
    ".hg",              # Mercurial 元数据
    ".svn",             # SVN 元数据
    ".idea",            # JetBrains 配置
    ".vscode",          # VS Code 配置
    "*.log",           # 日志文件
    "*.tmp",           # 临时文件
    "*.temp",          # 临时文件
    "*.bak",           # 备份文件
    "*.backup",        # 备份文件
    "*.old",           # 旧版本文件
    "*.blend1",        # Blender 自动备份
    "*.blend2",        # Blender 自动备份
    "*.blend3",        # Blender 自动备份
    "*.part",          # 未完成下载
    "*.crdownload",    # Chrome 未完成下载
    "thumbs.db",       # Windows 缩略图缓存
    "._*",             # macOS 资源派生文件
    ".ds_store",       # macOS Finder 元数据
]

_EXCLUDE_DESCRIPTION = {
    "__pycache__":  "Python 缓存目录",
    ".cache":       "通用缓存目录",
    ".pytest_cache": "pytest 缓存目录",
    ".mypy_cache":  "mypy 缓存目录",
    ".ruff_cache":  "ruff 缓存目录",
    "cache":        "通用缓存目录",
    "caches":       "通用缓存目录",
    "cacheddata":   "Chromium/Electron 缓存",
    "code cache":   "Chromium/Electron 缓存",
    "gpucache":     "GPU 缓存",
    "crashpad":     "崩溃报告缓存",
    "logs":         "日志目录",
    "tmp":          "临时目录",
    "temp":         "临时目录",
    "*.pyc":        "Python 编译文件 (.pyc)",
    "*.pyo":        "Python 优化编译文件 (.pyo)",
    ".git":          "Git 仓库元数据",
    ".hg":           "Mercurial 元数据",
    ".svn":          "SVN 元数据",
    ".idea":         "JetBrains 配置",
    ".vscode":       "VS Code 配置",
    "*.log":         "日志文件 (.log)",
    "*.tmp":         "临时文件 (.tmp)",
    "*.temp":        "临时文件 (.temp)",
    "*.bak":         "备份文件 (.bak)",
    "*.backup":      "备份文件 (.backup)",
    "*.old":         "旧版本文件 (.old)",
    "*.blend1":      "Blender 自动备份 (.blend1)",
    "*.blend2":      "Blender 自动备份 (.blend2)",
    "*.blend3":      "Blender 自动备份 (.blend3)",
    "*.part":        "未完成下载文件 (.part)",
    "*.crdownload":  "Chrome 未完成下载文件",
    "thumbs.db":     "Windows 缩略图缓存",
    "._*":           "macOS 资源派生文件",
    ".ds_store":     "macOS Finder 元数据",
}


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


def should_exclude(file_path, exclude_patterns=None):
    """检查文件是否应被排除。返回：(是否排除, 排除原因描述)"""
    if exclude_patterns is None:
        exclude_patterns = EXCLUDE_PATTERNS
    
    file_name = file_path.name.lower()
    path_parts = {part.lower() for part in file_path.parts}
    
    import fnmatch
    
    for pattern in exclude_patterns:
        pattern_key = pattern.lower()

        # 1) 目录名精确匹配（如 __pycache__ 出现在路径任一层）
        if pattern_key in path_parts:
            return True, _EXCLUDE_DESCRIPTION.get(pattern, pattern)
        
        # 2) 通配符文件模式（如 *.pyc、._*）
        if "*" in pattern_key:
            if fnmatch.fnmatch(file_name, pattern_key):
                return True, _EXCLUDE_DESCRIPTION.get(pattern, pattern)
        
        # 3) 精确文件名匹配（如 .ds_store）
        if file_name == pattern_key:
            return True, _EXCLUDE_DESCRIPTION.get(pattern, pattern)
    
    return False, None


def normalize_output_path(output_path, portable_path=None, version=None):
    """将任意输入规范化为合法的 .zip 文件路径"""
    if not output_path or not output_path.strip():
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


def get_unique_output_path(output_path):
    """如果文件已存在，自动重命名"""
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


def pack_portable(portable_path, output_path, compress=False, exclude_patterns=None):
    """
    打包整个 Blender portable/ 文件夹。
    
    Args:
        portable_path:     portable 文件夹路径
        output_path:       输出的 zip 文件路径
        compress:          是否启用 ZIP_DEFLATED 压缩（默认关闭，速度快 5-10x）
        exclude_patterns:  排除模式列表。None 时使用默认 EXCLUDE_PATTERNS；
                          传 [] 或 --all 则不排除任何文件
    """
    portable_path = Path(portable_path)
    output_path = Path(output_path)
    
    if not portable_path.exists():
        raise FileNotFoundError(f"portable 文件夹不存在：{portable_path}")
    
    if exclude_patterns is None:
        exclude_patterns = EXCLUDE_PATTERNS
    use_exclude = len(exclude_patterns) > 0
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    packed_files = 0
    skipped_files = 0
    exclude_reasons = {}   # {原因描述: 计数}
    output_resolved = output_path.resolve(strict=False)
    
    compression = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    mode_label = "压缩" if compress else "存储（快速）"
    
    print(f"[MMY] 开始打包 {portable_path}")
    print("[MMY] 总文件数：边打包边统计（跳过预扫描）")
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
            if file_path.resolve(strict=False) == output_resolved:
                skipped_files += 1
                exclude_reasons["输出文件自身"] = exclude_reasons.get("输出文件自身", 0) + 1
                continue
            if use_exclude:
                excluded, reason = should_exclude(file_path.relative_to(portable_path), exclude_patterns)
                if excluded:
                    skipped_files += 1
                    exclude_reasons[reason] = exclude_reasons.get(reason, 0) + 1
                    continue
            
            arcname = file_path.relative_to(portable_path.parent)
            arcname_str = str(arcname).replace("\\", "/")
            zf.write(file_path, arcname_str)
            packed_files += 1
            
            if packed_files % 1000 == 0:
                print(f"[MMY]   进度：已打包 {packed_files}，已跳过 {skipped_files}...")
    
    size_mb = output_path.stat().st_size / 1024 / 1024
    
    print()
    if exclude_reasons:
        print(f"[MMY] 排除摘要（共 {skipped_files} 个文件）：")
        for reason, count in sorted(exclude_reasons.items(), key=lambda x: -x[1]):
            print(f"[MMY]   - {reason}：{count} 个")
    else:
        print(f"[MMY] （无排除文件）")
    
    print(f"[MMY] [OK] 打包完成！")
    print(f"[MMY]   文件：{output_path.name}")
    print(f"[MMY]   大小：{size_mb:.1f} MB")
    total_files = packed_files + skipped_files
    print(f"[MMY]   已打包：{packed_files}  已跳过：{skipped_files}  总计：{total_files}")
    return output_path


def _run_tkinter_ui():
    """tkinter 交互模式（仅限有 tkinter 的环境）"""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog
    except ImportError:
        print("[MMY] [ERROR] 当前环境无 tkinter，请使用命令行模式：")
        print("       python pack.py <portable_path> [output_path]")
        sys.exit(1)
    
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
# 主函数
# ============================================================

def main():
    print("=" * 60)
    print("MMY Blender Configure — 打包工具")
    print("=" * 60)
    print()
    
    if len(sys.argv) == 1:
        # 无参数 → 打包插件本身
        pack_addon()
    else:
        # 有参数 → 打包 portable/ 文件夹
        print("模式：打包 Blender portable/ 文件夹")
        print()
        
        portable_path = sys.argv[1]
        raw_output = sys.argv[2] if len(sys.argv) >= 3 else None
        version_override = None
        use_compress = "--compress" in sys.argv
        use_all = "--all" in sys.argv
        
        for i, arg in enumerate(sys.argv):
            if arg == "--version" and i + 1 < len(sys.argv):
                version_override = sys.argv[i + 1]
        
        if not Path(portable_path).exists():
            print(f"[MMY] [ERROR] portable 文件夹不存在：{portable_path}")
            sys.exit(1)
        
        version = get_blender_version(portable_path, version_override)
        if not version:
            print("[MMY] [WARN] 无法检测版本号，使用 'unknown'")
            version = "unknown"
        
        output_path = normalize_output_path(raw_output, portable_path, version)
        output_path = get_unique_output_path(output_path)
        
        exclude_patterns = [] if use_all else None
        
        try:
            result = pack_portable(portable_path, output_path,
                                 compress=use_compress,
                                 exclude_patterns=exclude_patterns)
            print(f"\n[MMY] [OK] 结果：{result}")
        except Exception as e:
            print(f"\n[MMY] [ERROR] 错误：{e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[MMY] 已取消。")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[MMY] [ERROR] 发生错误：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
