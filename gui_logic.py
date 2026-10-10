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


def parse_drop_paths(data: str | Iterable[str] | None) -> list[str]:
    """解析拖拽事件的 data 为路径列表。

    入参有两种形态，都必须支持：

    - ``str``：原始 Tcl list 字符串。tkdnd 的 ``%D`` **没有**用大括号包裹，
      路径里的空格会成为词分隔符，所以带空格的路径必须靠花括号保护，
      形如 ``"{C:\\\\a b\\\\x.zip} {D:\\\\y\\\\z.exe}"``；花括号本身要剥掉。
    - ``list[str]``：``Tk.tk.splitlist()`` 的返回值。**这是最常见的形态**，
      若按 str 处理会抛 ``TypeError: expected string or bytes-like object``。

    两种形态都只做「去壳 + 去空白」，不做存在性判断——那是
    :func:`first_matching_path` 的职责。

    >>> parse_drop_paths("{C:\\\\my configs\\\\a.zip} {D:\\\\b\\\\c.exe}")
    ['C:\\\\my configs\\\\a.zip', 'D:\\\\b\\\\c.exe']
    >>> parse_drop_paths(["C:/x/a.zip", "D:/b/c.exe"])
    ['C:/x/a.zip', 'D:/b/c.exe']
    >>> parse_drop_paths("")
    []
    >>> parse_drop_paths(None)
    []
    """
    if not data:
        return []
    # splitlist() 的结果已是列表，逐项清洗即可，不必再过正则
    if isinstance(data, (list, tuple)):
        return _clean_paths(data)
    return _clean_paths(_TOKEN_RE.findall(str(data)))


def _clean_paths(items: Iterable[str]) -> list[str]:
    """逐项剥掉 Tcl 花括号、去首尾空白，丢弃空项。"""
    cleaned: list[str] = []
    for item in items:
        text = str(item).strip()
        if text.startswith("{") and text.endswith("}"):
            text = text[1:-1].strip()
        if text:
            cleaned.append(text)
    return cleaned


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
