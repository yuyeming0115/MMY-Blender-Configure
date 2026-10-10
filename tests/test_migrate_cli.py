"""migrate.py（独立迁移 CLI）的纯函数单测——不依赖 bpy / Blender。"""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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


class SourceUserRootTests(unittest.TestCase):
    def test_reads_source_user_root(self):
        self.assertEqual(
            migrate.source_user_root({"source": {"user_root": "C:/Blender/portable"}}),
            "C:/Blender/portable",
        )

    def test_missing_field_returns_empty(self):
        self.assertEqual(migrate.source_user_root({"source": {}}), "")
        self.assertEqual(migrate.source_user_root({}), "")


class _FakeCore:
    """preflight_target 的最小替身：不启动真实 Blender。"""

    class MigrationError(Exception):
        pass

    def __init__(self, user_root):
        self._user_root = user_root

    def windows_executable_is_running(self, target, pids):
        return False

    def run_target_probe(self, target, timeout=60):
        return {"version": [5, 2, 0], "user_root": self._user_root}

    def normalize_version(self, value):
        return tuple(value)

    def validate_forward_version(self, source, target):
        return tuple(target)

    def predict_addon_compatibility(self, addons, version):
        return []


class PreflightTargetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.exe = self.tmp / "blender.exe"
        self.exe.write_bytes(b"stub")

    def tearDown(self):
        self._tmp.cleanup()

    def _preflight(self, user_root, source_user_root=""):
        manifest = {
            "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            "addons": [],
        }
        if source_user_root:
            manifest["source"]["user_root"] = source_user_root
        fake = _FakeCore(user_root)
        with mock.patch.object(migrate, "load_migration_core", return_value=fake):
            return migrate.preflight_target(manifest, self.exe)

    def test_self_migration_rejected_with_explicit_message(self):
        root = str(self.tmp / "portable")
        with self.assertRaises(migrate.MigrateInputError) as ctx:
            self._preflight(root, source_user_root=root)
        message = str(ctx.exception)
        self.assertIn("来源", message)
        self.assertIn("不能自己导入自己", message)

    def test_other_source_passes_and_reports_paths(self):
        info = self._preflight(
            str(self.tmp / "portable"),
            source_user_root=r"C:\BlenderAPP\Blender5.1\portable",
        )
        self.assertEqual(info["version"], "5.2")
        self.assertTrue(info["portable"])
        self.assertEqual(info["source_user_root"], r"C:\BlenderAPP\Blender5.1\portable")

    def test_legacy_profile_without_source_root_is_allowed(self):
        info = self._preflight(str(self.tmp / "portable"))
        self.assertEqual(info["source_user_root"], "")


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

    def test_preflight_shows_source_root_when_known(self):
        manifest = {
            "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            "addons": [],
        }
        preflight = {
            "version": "5.2",
            "user_root": r"C:\BlenderAPP\Blender5.2\portable",
            "portable": True,
            "source_user_root": r"C:\BlenderAPP\Blender5.1\portable",
            "incompatible": [],
        }
        text = migrate.describe_profile(
            manifest, r"C:\BlenderAPP\Blender5.2\blender.exe", None, preflight
        )
        self.assertIn("已自动排除自迁移", text)
        self.assertNotIn("未记录来源目录", text)

    def test_summary_warns_about_missing_folder_components(self):
        """v1.5.1 之前导出的包不含 datafiles / scripts/startup。

        迁移是整目录替换，不提示的话用户迁移完才发现目标目录少一截文件。
        """
        manifest = {
            "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            "addons": [],
            "components": ["preferences", "startup_file", "addons", "extensions"],
        }
        text = migrate.describe_profile(manifest, r"C:\B\blender.exe", None, None)
        self.assertIn("不含以下内容", text)
        self.assertIn("datafiles", text)
        self.assertIn("scripts/startup", text)
        self.assertIn("重新导出", text)

    def test_summary_quiet_when_all_folders_present(self):
        manifest = {
            "source": {"blender_version": [5, 1, 0], "platform": "win32"},
            "addons": [],
            "components": [
                "preferences",
                "startup_file",
                "addons",
                "extensions",
                "presets",
                "datafiles",
                "startup_scripts",
            ],
        }
        text = migrate.describe_profile(manifest, r"C:\B\blender.exe", None, None)
        self.assertNotIn("不含以下内容", text)


class MissingFolderComponentTests(unittest.TestCase):
    def test_all_reported_when_components_absent(self):
        result = migrate.missing_folder_components({})
        self.assertEqual(len(result), 3)
        self.assertTrue(any("datafiles" in item for item in result))
        self.assertTrue(any("scripts/startup" in item for item in result))

    def test_datafiles_only_missing(self):
        result = migrate.missing_folder_components(
            {"components": ["presets", "startup_scripts"]}
        )
        self.assertEqual(result, ["数据文件 datafiles"])

    def test_nothing_missing(self):
        result = migrate.missing_folder_components(
            {"components": ["presets", "datafiles", "startup_scripts"]}
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
