"""migrate.py（独立迁移 CLI）的纯函数单测——不依赖 bpy / Blender。"""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import migrate  # noqa: E402


def _write_profile(path: Path, manifest: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("payload/config/userpref.blend", b"fake")
    return path


class ValidateProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_profile_passes(self):
        profile = _write_profile(
            self.tmp / "profile.zip",
            {
                "schema_version": 2,
                "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            },
        )
        manifest = migrate.validate_profile(profile)
        self.assertEqual(manifest["schema_version"], 2)

    def test_pack_py_portable_backup_rejected_with_hint(self):
        pack_backup = self.tmp / "backup.zip"
        with zipfile.ZipFile(pack_backup, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps({"schema_version": 1, "type": "portable"}),
            )
            zf.writestr("portable/config/userpref.blend", b"fake")
        with self.assertRaises(migrate.MigrateInputError) as ctx:
            migrate.validate_profile(pack_backup)
        self.assertIn("整目录备份", str(ctx.exception))

    def test_garbage_file_rejected(self):
        bad = self.tmp / "bad.zip"
        bad.write_bytes(b"not a zip")
        with self.assertRaises(migrate.MigrateInputError):
            migrate.validate_profile(bad)

    def test_missing_file_rejected(self):
        with self.assertRaises(migrate.MigrateInputError):
            migrate.validate_profile(self.tmp / "nope.zip")

    def test_wrong_platform_rejected(self):
        profile = _write_profile(
            self.tmp / "p.zip",
            {
                "schema_version": 2,
                "source": {"blender_version": [5, 1, 0], "platform": "darwin"},
            },
        )
        with self.assertRaises(migrate.MigrateInputError):
            migrate.validate_profile(profile)

    def test_missing_worker_script_raises_on_load(self):
        self.assertTrue(migrate.WORKER_SCRIPT.is_file())


class VersionStringTests(unittest.TestCase):
    def test_patches_zero_omitted(self):
        self.assertEqual(migrate.version_string([5, 1, 0]), "5.1")

    def test_full_triplet_kept(self):
        self.assertEqual(migrate.version_string([5, 1, 3]), "5.1.3")

    def test_short_input_padded(self):
        self.assertEqual(migrate.version_string([4, 2]), "4.2")


class DetectTargetVersionTests(unittest.TestCase):
    def test_portable_style_path(self):
        self.assertEqual(
            migrate.detect_target_version(r"C:\BlenderAPP\Blender5.1\blender.exe"),
            "5.1",
        )

    def test_standard_install_path(self):
        self.assertEqual(
            migrate.detect_target_version(
                r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
            ),
            "4.5",
        )

    def test_unknown_path_returns_none(self):
        self.assertIsNone(migrate.detect_target_version(r"C:\tools\blender.exe"))


class PortableDetectionTests(unittest.TestCase):
    def test_exe_sibling_portable_is_portable(self):
        self.assertTrue(
            migrate.is_portable_user_root(
                r"C:\BlenderAPP\Blender5.2\portable",
                r"C:\BlenderAPP\Blender5.2\blender.exe",
            )
        )

    def test_appdata_user_root_is_not_portable(self):
        self.assertFalse(
            migrate.is_portable_user_root(
                r"C:\Users\EDY\AppData\Roaming\Blender Foundation\Blender\5.2",
                r"C:\BlenderAPP\Blender5.2\blender.exe",
            )
        )

    def test_empty_user_root_is_not_portable(self):
        self.assertFalse(migrate.is_portable_user_root("", r"C:\x\blender.exe"))


class DescribeProfileTests(unittest.TestCase):
    def test_summary_contains_key_info(self):
        manifest = {
            "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            "addons": [{"name": "a", "enabled": True}, {"name": "b", "enabled": False}],
            "workspaces": {"count": 5, "custom_count": 2},
        }
        text = migrate.describe_profile(manifest, r"C:\BlenderAPP\Blender5.2\blender.exe", "5.2")
        self.assertIn("5.1", text)
        self.assertIn("5.2", text)
        self.assertIn("1 个", text)  # 只有 1 个已启用插件
        self.assertIn("2 个", text)  # 2 个自定义工作区
        self.assertIn("来源 Blender", text)  # 自我迁移提示

    def test_preflight_summary_shows_real_paths(self):
        manifest = {
            "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            "addons": [{"name": "a", "enabled": True}],
            "workspaces": {"count": 3, "custom_count": 2},
        }
        preflight = {
            "version": "5.2",
            "user_root": r"C:\BlenderAPP\Blender5.2\portable",
            "portable": True,
            "incompatible": [],
        }
        text = migrate.describe_profile(
            manifest, r"C:\BlenderAPP\Blender5.2\blender.exe", None, preflight
        )
        self.assertIn("实测", text)
        self.assertIn(r"C:\BlenderAPP\Blender5.2\portable", text)
        self.assertNotIn("未启用便携模式", text)

    def test_preflight_warns_when_not_portable(self):
        manifest = {
            "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            "addons": [],
        }
        preflight = {
            "version": "5.2",
            "user_root": r"C:\Users\EDY\AppData\Roaming\Blender Foundation\Blender\5.2",
            "portable": False,
            "incompatible": [],
        }
        text = migrate.describe_profile(
            manifest, r"C:\BlenderAPP\Blender5.2\blender.exe", None, preflight
        )
        self.assertIn("未启用便携模式", text)
        self.assertIn("新建名为 portable 的文件夹", text)
        self.assertIn("AppData", text)

    def test_preflight_lists_incompatible_addons(self):
        manifest = {
            "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            "addons": [],
        }
        preflight = {
            "version": "5.2",
            "user_root": r"C:\BlenderAPP\Blender5.2\portable",
            "portable": True,
            "incompatible": [{"name": "BoxCutter", "module": "boxcutter"}],
        }
        text = migrate.describe_profile(
            manifest, r"C:\BlenderAPP\Blender5.2\blender.exe", None, preflight
        )
        self.assertIn("不兼容插件", text)
        self.assertIn("BoxCutter", text)


if __name__ == "__main__":
    unittest.main()
