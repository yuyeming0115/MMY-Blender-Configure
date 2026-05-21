#!/usr/bin/env python3
"""
MMY Blender Configure 打包脚本
自动打包插件为分发版本
"""

import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path


def get_version():
    """从 __init__.py 读取 bl_info 版本"""
    init_path = Path(__file__).parent / "mmy_pack_config" / "__init__.py"

    with open(init_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析 bl_info["version"]
    import re
    match = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', content)
    if match:
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"

    return "0.0.0"


def pack():
    """打包插件"""
    root = Path(__file__).parent
    source_dir = root / "mmy_pack_config"
    releases_dir = root / "releases"

    if not source_dir.exists():
        print(f"错误：源目录不存在 {source_dir}")
        sys.exit(1)

    # 创建 releases 目录
    releases_dir.mkdir(exist_ok=True)

    # 生成文件名
    version = get_version()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"MMY_Blender_Toolkit_v{version}_{timestamp}.zip"
    output_path = releases_dir / filename

    # 打包
    exclude_patterns = ["__pycache__", ".pyc", "*.pyc", ".git"]

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in source_dir.rglob("*"):
            if file_path.is_file():
                # 排除 __pycache__ 和 .pyc
                rel_path = file_path.relative_to(source_dir)
                if any(part == "__pycache__" for part in rel_path.parts):
                    continue
                if file_path.suffix == ".pyc":
                    continue

                # 写入 zip，保留目录结构
                arcname = Path("mmy_pack_config") / rel_path
                zf.write(file_path, arcname)
                print(f"  添加: {arcname}")

    print(f"\n打包完成：{output_path}")
    print(f"文件大小：{output_path.stat().st_size / 1024:.1f} KB")

    return output_path


if __name__ == "__main__":
    pack()