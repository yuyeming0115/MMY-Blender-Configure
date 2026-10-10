"""MMY GUI 的纯逻辑部分（零依赖，可被无 tkinter 的解释器测试）。

只放不依赖图形界面的函数：拖拽数据解析、路径筛选、错误文案翻译。
UI 组件在 gui_kit.py，两者分离让逻辑层可独立测试。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

# Tk 拖拽事件的 data 是 Tcl list 风格：带空格/花括号的路径会被包起来
# 例："{C:\\my configs\\a.zip} {D:\\b\\c.exe}"
_TOKEN_RE = re.compile(r"\{[^}]*\}|[^\s{}]+")


def parse_drop_paths(data: str) -> list[str]:
    """解析拖拽事件的 data 字符串为路径列表。

    >>> parse_drop_paths("{C:\\\\my configs\\\\a.zip} {D:\\\\b\\\\c.exe}")
    ['C:\\\\my configs\\\\a.zip', 'D:\\\\b\\\\c.exe']
    >>> parse_drop_paths("")
    []
    """
    if not data:
        return []
    paths: list[str] = []
    for token in _TOKEN_RE.findall(data):
        if token.startswith("{") and token.endswith("}"):
            token = token[1:-1]
        token = token.strip()
        if token:
            paths.append(token)
    return paths


def first_matching_path(
    paths: Iterable[str], want_directory: bool
) -> str | None:
    """从拖入的多个路径里挑出第一个符合类型的（目录/文件）。

    用户常把文件夹和文件一起拖进来，或拖错类型；这里只做筛选，
    找不到返回 None，由调用方给出友好提示。
    """
    for path in paths:
        try:
            target = Path(path)
            # 先判存在性：is_dir() 对不存在的路径返回 False，
            # 若不先过滤，会被误判成「文件」而放行
            if not target.exists():
                continue
            if target.is_dir() == want_directory:
                return path
        except OSError:  # pragma: no cover —— 非法路径
            continue
    return None


# 常见技术错误 → 小白能懂的说法。关键词命中即给「怎么办」。
# 顺序有意义：更具体的关键词必须排在更宽泛的之前。
_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    (
        "不能自己导入自己",
        "你选的配置包就是这台机器上的 Blender 导出的，不能导回它自己。\n"
        "请改选另一台机器/另一个版本的 Blender 作为目标。",
    ),
    (
        "同主版本",
        "本工具只支持同主版本向前迁移（例如 5.1 → 5.2）。\n"
        "跨主版本请先在源 Blender 里升级到目标主版本，再重新导出配置包。",
    ),
    (
        "不是有效的 ZIP",
        "这个文件已损坏或不是配置包。请重新从源 Blender 用插件「一键导出」生成。",
    ),
    (
        "不是迁移配置包",
        "这是 pack.py 做的整目录备份，只能用于同版本覆盖解压。\n"
        "跨版本请在源 Blender 中用插件「一键导出」生成迁移配置包。",
    ),
    (
        "正在运行",
        "请先关闭目标 Blender（系统托盘里也要退出），然后重新点「开始导入」。",
    ),
    (
        "不是便携版",
        "目标不是便携版 Blender，配置会写进系统目录而不是便携目录。\n"
        "请在 blender.exe 同级新建一个名为 portable 的空文件夹，重启一次 Blender 后再试。",
    ),
    (
        "空间不足",
        "目标磁盘剩余空间不够。请清理磁盘后重试。",
    ),
    (
        "文件夹不存在",
        "路径不存在，可能被移动或删除。请重新选择。",
    ),
)


def explain_error(exc: BaseException | str) -> str:
    """把技术错误翻译成「原因 + 怎么办」。未命中则原样返回。

    原则：永远给出下一步动作，而不是只报错。
    """
    text = str(exc).strip()
    for keyword, hint in _ERROR_HINTS:
        if keyword in text:
            return f"{text}\n\n→ 怎么办：{hint}"
    return text
