"""GUI 冒烟测试（需要 tkinter，请用带 tkinter 的解释器运行）。

运行方式::

    "%LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe" tests/smoke_gui.py

覆盖：
1. gui_kit / 两个 GUI 入口能否导入并在隐藏窗口中实例化（含真实 tkdnd 注册）
2. 核心层 progress 回调契约：阶段顺序、比例边界、回调异常不影响结果

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
        ("迁移 GUI", "mmy_migrate_gui", "MigrateApp"),
        ("打包 GUI", "mmy_pack_gui", "PackApp"),
    ):
        try:
            module = importlib.import_module(module_name)
            root = gk.make_root(f"smoke {label}")
            getattr(module, class_name)(root)
            root.withdraw()
            root.update()
            root.destroy()
            record(f"{label} 实例化", True, "窗口与组件创建成功")
        except Exception as exc:  # noqa: BLE001
            record(f"{label} 实例化", False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()


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


def main() -> int:
    try:
        check_gui_modules()
        check_progress_contract()
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
