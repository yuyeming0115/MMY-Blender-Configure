"""MMY GUI 纯逻辑层单测（不需要 tkinter，任意解释器可跑）。"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


logic = _load("mmy_gui_logic", "gui_logic.py")


class ParseDropPathsTests(unittest.TestCase):
    def test_plain_path(self):
        self.assertEqual(logic.parse_drop_paths(r"C:\a\b.zip"), [r"C:\a\b.zip"])

    def test_braced_paths_with_spaces(self):
        raw = r"{C:\my configs\a.zip} {D:\b\c.exe}"
        self.assertEqual(
            logic.parse_drop_paths(raw),
            [r"C:\my configs\a.zip", r"D:\b\c.exe"],
        )

    def test_mixed_braced_and_plain(self):
        raw = r"{C:\a b\x.zip} D:\y\z.exe"
        self.assertEqual(
            logic.parse_drop_paths(raw), [r"C:\a b\x.zip", r"D:\y\z.exe"]
        )

    def test_empty_and_none_like(self):
        self.assertEqual(logic.parse_drop_paths(""), [])
        self.assertEqual(logic.parse_drop_paths("   "), [])

    def test_unbalanced_brace_does_not_crash(self):
        # 拖拽数据异常时不能抛异常，否则用户看到崩溃
        result = logic.parse_drop_paths(r"{C:\a b\x.zip")
        self.assertTrue(all(not p.startswith("{") for p in result))

    def test_single_braced_path(self):
        self.assertEqual(
            logic.parse_drop_paths(r"{C:\my configs\a.zip}"), [r"C:\my configs\a.zip"]
        )

    def test_splitlist_result_is_accepted(self):
        """回归：Tk.tk.splitlist() 返回的是 list。

        v1.5.1 之前只接受 str，实际拖拽时抛
        ``TypeError: expected string or bytes-like object, got 'list'``，
        而异常发生在 tkinter 事件回调里被静默吞掉（pythonw 无控制台），
        表现为「拖拽完全没反应」。
        """
        result = logic.parse_drop_paths(["E:/packs/profile.zip"])
        self.assertEqual(result, ["E:/packs/profile.zip"])

    def test_splitlist_result_with_spaces(self):
        raw = [r"C:\my configs\a.zip", r"D:\b\c.exe"]
        self.assertEqual(
            logic.parse_drop_paths(raw), [r"C:\my configs\a.zip", r"D:\b\c.exe"]
        )

    def test_splitlist_result_strips_braces_and_blanks(self):
        self.assertEqual(
            logic.parse_drop_paths(["{C:\\a b\\x.zip}", "  ", ""]),
            [r"C:\a b\x.zip"],
        )

    def test_tuple_input_also_accepted(self):
        self.assertEqual(
            logic.parse_drop_paths(("C:/a.zip",)), ["C:/a.zip"]
        )

    def test_none_is_safe(self):
        self.assertEqual(logic.parse_drop_paths(None), [])


class FirstMatchingPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.tmp.name) / "portable"
        self.dir_path.mkdir()
        self.file_path = Path(self.tmp.name) / "a.zip"
        self.file_path.write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_pick_directory(self):
        found = logic.first_matching_path(
            [str(self.file_path), str(self.dir_path)], want_directory=True
        )
        self.assertEqual(found, str(self.dir_path))

    def test_pick_file(self):
        found = logic.first_matching_path(
            [str(self.dir_path), str(self.file_path)], want_directory=False
        )
        self.assertEqual(found, str(self.file_path))

    def test_type_mismatch_returns_none(self):
        self.assertIsNone(
            logic.first_matching_path([str(self.file_path)], want_directory=True)
        )
        self.assertIsNone(
            logic.first_matching_path([str(self.dir_path)], want_directory=False)
        )

    def test_nonexistent_path_is_skipped(self):
        found = logic.first_matching_path(
            [str(self.dir_path / "nope"), str(self.file_path)], want_directory=False
        )
        self.assertEqual(found, str(self.file_path))


class ExplainErrorTests(unittest.TestCase):
    def test_known_hints_include_action(self):
        cases = {
            "目标版本不满足同主版本正向迁移要求": "同主版本",
            "目标 Blender 就是配置包的来源，不能自己导入自己": "不能自己导入自己",
            "目标 Blender 正在运行，请关闭后重试": "正在运行",
            "x.zip 不是有效的 ZIP 文件。": "不是有效的 ZIP",
            "portable 文件夹不存在：C:\\x": "文件夹不存在",
            "目标磁盘空间不足": "空间不足",
        }
        for raw, keyword in cases.items():
            with self.subTest(raw=raw):
                text = logic.explain_error(raw)
                self.assertIn("怎么办", text)
                self.assertIn(keyword, text)

    def test_specific_hint_wins_over_generic(self):
        # "不是迁移配置包" 比 "portable" 更具体，必须优先命中
        raw = "MMY_Backup_Portable.zip 不是迁移配置包（portable 整目录备份）"
        text = logic.explain_error(raw)
        self.assertIn("整目录备份", text)

    def test_unknown_error_returned_as_is(self):
        self.assertEqual(logic.explain_error("某个未知错误"), "某个未知错误")

    def test_accepts_exception_instance(self):
        text = logic.explain_error(ValueError("目标 Blender 正在运行"))
        self.assertIn("系统托盘", text)

    def test_hint_never_loses_original_message(self):
        raw = "目标 Blender 正在运行，请关闭后重试：C:\\BlenderAPP\\blender.exe"
        self.assertTrue(logic.explain_error(raw).startswith(raw))


class PackProgressContractTests(unittest.TestCase):
    """pack_portable 的阶段回调契约。"""

    @classmethod
    def setUpClass(cls):
        cls.pack = _load("mmy_gui_pack", "pack.py")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "portable"
        (self.src / "config").mkdir(parents=True)
        (self.src / "config" / "userpref.blend").write_bytes(b"x" * 512)
        for index in range(10):
            (self.src / f"f{index}.txt").write_text(f"data{index}", encoding="utf-8")
        self.out = Path(self.tmp.name) / "out.zip"

    def tearDown(self):
        self.tmp.cleanup()

    def test_stages_reported_in_order(self):
        seen = []
        self.pack.pack_portable(
            self.src, self.out, compress=True,
            progress=lambda key, message, ratio=None: seen.append(key),
        )
        self.assertIn("scan", seen)
        self.assertIn("compress", seen)
        self.assertLess(seen.index("scan"), seen.index("compress"))
        self.assertTrue(self.out.is_file())

    def test_ratio_is_within_bounds(self):
        ratios = []
        self.pack.pack_portable(
            self.src, self.out,
            progress=lambda key, message, ratio=None: ratios.append(ratio),
        )
        for ratio in ratios:
            if ratio is not None:
                self.assertGreaterEqual(ratio, 0.0)
                self.assertLessEqual(ratio, 1.0)

    def test_progress_exception_does_not_break_packing(self):
        """GUI 崩溃绝不能影响用户真正要的结果（zip 必须照样生成）。"""
        def boom(*args, **kwargs):
            raise RuntimeError("GUI 挂了")

        self.pack.pack_portable(self.src, self.out, progress=boom)
        self.assertTrue(self.out.is_file())
        self.assertGreater(self.out.stat().st_size, 0)

    def test_no_prescan_when_progress_is_none(self):
        """命令行/插件场景不传 progress 时不应付出预扫描代价。"""
        calls = []
        original_rglob = Path.rglob

        def counting_rglob(self, pattern):
            calls.append(pattern)
            return original_rglob(self, pattern)

        Path.rglob = counting_rglob
        try:
            self.pack.pack_portable(self.src, self.out)
        finally:
            Path.rglob = original_rglob
        # 无 progress：只有打包本身的一次遍历
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
