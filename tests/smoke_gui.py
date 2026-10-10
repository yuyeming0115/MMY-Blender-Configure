"""GUI 冒烟测试（需要 tkinter，请用带 tkinter 的解释器运行）。

运行方式::

    "%LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe" tests/smoke_gui.py

覆盖：
1. gui_kit / 合并后的双标签页入口能否导入并在隐藏窗口中实例化（含真实 tkdnd 注册）
2. 拖拽事件解析走真实 splitlist 形态（回归 v1.5.1「拖拽无反应」）
3. 核心层 progress 回调契约：阶段顺序、比例边界、回调异常不影响结果

只读/临时目录操作，不会碰用户的真实 Blender 配置。
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))


def check_gui_modules() -> None:
    import gui_kit as gk

    record("gui_kit 导入", True, f"拖拽可用={gk.DND_AVAILABLE}")
    import importlib

    for label, module_name, class_name in (
        ("配置迁移页", "mmy_tool_gui", "MigratePage"),
        ("导出迁移包页", "mmy_tool_gui", "ExportPage"),
        ("配置打包页", "mmy_tool_gui", "PackPage"),
    ):
        try:
            module = importlib.import_module(module_name)
            root = gk.make_root(f"smoke {label}")
            scroll = gk.ScrollPane(root)
            body = scroll.body
            if class_name in {"MigratePage", "ExportPage"}:
                getattr(module, class_name)(body, root, scroll)
            else:
                getattr(module, class_name)(body, root)
            root.withdraw()
            root.update()
            root.destroy()
            record(f"{label} 实例化", True, "窗口与组件创建成功")
        except Exception as exc:  # noqa: BLE001
            record(f"{label} 实例化", False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()


def check_drop_zone_real_dnd() -> None:
    """用真实的 Tk 解析链路驱动 DropZone，确保拖拽真的能落路径。

    这是 v1.5.1「拖进去完全没反应」的回归测试：
    - splitlist 返回的是 list，旧版 parse_drop_paths 只接受 str → TypeError
    - tkdnd 的 ``%D`` 无大括号包裹，反斜杠可能被 Tcl 吃掉 → 必须靠
      「存在性校验」在多候选里挑出真实路径
    """
    import gui_kit as gk

    with tempfile.TemporaryDirectory() as tmp:
        # 刻意用带空格 + 中文的名字，这是最容易触发 Tcl 转义问题的形态
        zip_path = Path(tmp) / "我的 配置包 v1.zip"
        zip_path.write_bytes(b"x")
        got: list[str] = []

        root = gk.make_root("smoke drop")
        zone = gk.DropZone(root, title="T", hint="H", on_path=got.append)
        zone.pack(fill="x")
        root.withdraw()
        root.update()

        class _Event:
            def __init__(self, data):
                self.data = data

        # 形态 A：event.data 本身就是 list —— tkinterdnd2 在部分平台/绑定
        # 下会直接给列表。旧版 parse_drop_paths 只接受 str，这里必崩。
        got.clear()
        zone._handle_drop(_Event([str(zip_path)]))
        record("拖拽：data 已是 list 的形态", got == [str(zip_path)], f"收到={got}")

        # 形态 B：Tcl list 字符串，花括号保护空格（tkdnd 的标准输出）
        got.clear()
        braced = "{" + str(zip_path) + "}"
        zone._handle_drop(_Event(braced))
        record("拖拽：Tcl list 花括号形态", got == [str(zip_path)], f"收到={got}")

        # 形态 C：裸路径无空格（splitlist 不会破坏，最常见）
        plain = Path(tmp) / "plain.zip"
        plain.write_bytes(b"x")
        got.clear()
        zone._handle_drop(_Event(str(plain)))
        record("拖拽：裸路径形态", got == [str(plain)], f"收到={got}")

        # 形态 D：多个文件一起拖入 → 取第一个类型匹配的
        got.clear()
        zone._handle_drop(
            _Event("{C:/nope.txt} {" + str(zip_path) + "}")
        )
        record("拖拽：多文件取首个匹配", got == [str(zip_path)], f"收到={got}")

        # 子控件也要能接住 drop：tkdnd 只投递给光标下最深的已注册控件
        if gk.DND_AVAILABLE:
            count = len(list(zone._walk(zone)))
            record(
                "拖拽：子树控件注册",
                count >= 4,
                f"注册控件数={count}（Frame+内层+标题+路径+按钮）",
            )
        else:
            record("拖拽：子树控件注册", True, "无拖拽库，跳过（点击选择可用）")

        root.destroy()


def check_result_card_visible() -> None:
    """结果卡片必须能被滚动到视野内，且会弹出模态提示。"""
    import gui_kit as gk

    root = gk.make_root("smoke result")
    scroll = gk.ScrollPane(root)
    scroll.pack(fill="both", expand=True)
    filler = gk.ttk.Frame(scroll.body, height=1400)
    filler.pack(fill="x")
    card = gk.ResultCard(scroll.body)
    card.pack(fill="x")
    root.withdraw()
    root.update()
    card.show(ok=True, title="导入成功", details=[("配置写入", "C:\\x\\portable")])
    root.update_idletasks()
    record(
        "结果卡片展开并可滚动定位",
        card.visible,
        f"卡片可见={card.visible}",
    )
    root.destroy()


def check_progress_contract() -> None:
    import pack as core

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "portable"
        (src / "config").mkdir(parents=True)
        (src / "config" / "userpref.blend").write_bytes(b"x" * 512)
        for index in range(20):
            (src / f"f{index}.txt").write_text(f"data{index}", encoding="utf-8")

        seen: list[str] = []
        out = Path(tmp) / "out.zip"
        core.pack_portable(
            src, out, compress=True,
            progress=lambda key, message="", ratio=None: seen.append(key),
        )
        record(
            "pack_portable 阶段上报",
            "scan" in seen and "compress" in seen and out.is_file(),
            f"阶段={sorted(set(seen))}，共 {len(seen)} 次",
        )

        def boom(*args, **kwargs):
            raise RuntimeError("GUI 挂了")

        out2 = Path(tmp) / "out2.zip"
        core.pack_portable(src, out2, progress=boom)
        record("回调异常不影响打包结果", out2.is_file(), "zip 照常生成")


def check_export_page_contract() -> None:
    """导出页的输入校验必须拦得住「拖错文件夹」这类小白误操作。

    真实事故场景：桌面上有多个Blender，用户拖了 5.2 的 portable 但工具
    自动找到 5.1 的 exe（或反过来），产出一份来源不明的包。
    """
    import migrate as cli

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "BlenderTest"
        portable = base / "portable"
        (portable / "config").mkdir(parents=True)
        (portable / "config" / "userpref.blend").write_bytes(b"x" * 16)
        exe = base / "blender.exe"
        exe.write_bytes(b"MZ")

        ok_exe = cli.source_executable_from_portable(portable)
        record(
            "导出：从 portable 推导 blender.exe",
            Path(ok_exe) == exe.resolve(strict=False) or Path(ok_exe) == exe,
            f"得到={ok_exe}",
        )

        info = cli.validate_source_blender(str(portable), str(exe))
        record(
            "导出：源目录校验通过",
            info["has_userpref"] and "config" in info["components_on_disk"],
            f"磁盘组件={info['components_on_disk']}",
        )

        # 拖了非 portable 目录 → 必须报错而不是猜
        try:
            cli.source_executable_from_portable(str(base))
            record("导出：拒绝非 portable 目录", False, "未拦截")
        except cli.MigrateInputError:
            record("导出：拒绝非 portable 目录", True, "已拦截")

        # portable 存在但没有 userpref.blend → 必须报错
        empty = base / "portable2"
        (empty / "config").mkdir(parents=True)
        try:
            cli.validate_source_blender(str(empty), str(exe))
            record("导出：拒绝缺 userpref 的目录", False, "未拦截")
        except cli.MigrateInputError:
            record("导出：拒绝缺 userpref 的目录", True, "已拦截")

        # 非便携版 Blender（exe 同级没有 portable/）
        lonely = Path(tmp) / "Solo" / "blender.exe"
        lonely.parent.mkdir(parents=True)
        lonely.write_bytes(b"MZ")
        try:
            cli.validate_source_blender(str(portable), str(lonely))
            record("导出：拒绝非便携版 Blender", False, "未拦截")
        except cli.MigrateInputError:
            record("导出：拒绝非便携版 Blender", True, "已拦截")

        # 报告渲染：警告必须出现在展示文本里（不能静默降级）
        warning_text = "后台模式无法物化默认键位"
        text = cli.describe_export_report(
            {
                "components": ["preferences", "datafiles"],
                "source_version": (5, 1, 0),
                "file_count": 12,
                "profile_path": r"E:\x\MMY_Backup_Profile_v5.1.0.zip",
                "notes": ["工作区 12 个"],
                "warnings": [warning_text],
            }
        )
        # 逐字匹配中文措辞太脆（改个词就红），断言语义要素：
        # 组件中文名在、警告原文逐字在、版本归一化后带 Blender 前缀
        checks = {
            "组件中文名": "数据文件" in text,
            "警告逐字透传": warning_text in text,
            "版本前缀": "Blender 5.1" in text,
            "可迁移指引": "配置迁移" in text,
        }
        record(
            "导出：报告含组件与警告",
            all(checks.values()),
            "、".join(f"{k}={'OK' if v else 'FAIL'}" for k, v in checks.items()),
        )


def main() -> int:
    try:
        check_gui_modules()
        check_drop_zone_real_dnd()
        check_result_card_visible()
        check_progress_contract()
        check_export_page_contract()
    except Exception as exc:  # noqa: BLE001
        record("冒烟框架", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    print()
    print("=" * 62)
    for name, ok, note in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {note}")
    print("=" * 62)
    failed = [name for name, ok, _ in RESULTS if not ok]
    if failed:
        print(f"[SMOKE] 存在 {len(failed)} 项失败：{failed}")
        return 1
    print(f"[SMOKE] 全部通过（{len(RESULTS)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
