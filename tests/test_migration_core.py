import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mmy_migration_core",
    ROOT / "mmy_pack_config" / "migration_core.py",
)
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


class MigrationCoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.user_root = self.root / "source" / "5.1"
        self.config = self.user_root / "config"
        self.scripts = self.user_root / "scripts"
        self.datafiles = self.user_root / "datafiles"
        self.extensions = self.user_root / "extensions"
        self.output = self.root / "output"

        (self.scripts / "addons" / "sample_addon").mkdir(parents=True)
        (self.scripts / "presets" / "render").mkdir(parents=True)
        self.config.mkdir(parents=True)
        self.datafiles.mkdir(parents=True)
        self.extensions.mkdir(parents=True)
        (self.config / "userpref.blend").write_bytes(b"preferences")
        (self.config / "startup.blend").write_bytes(b"startup")
        (self.config / "recent-files.txt").write_text("secret.blend", encoding="utf-8")
        (self.scripts / "addons" / "sample_addon" / "__init__.py").write_text(
            "bl_info = {}", encoding="utf-8"
        )
        (self.scripts / "addons" / "sample_addon" / "cache.pyc").write_bytes(b"cache")
        (self.scripts / "presets" / "render" / "quality.py").write_text(
            "quality = 1", encoding="utf-8"
        )
        (self.datafiles / "large.dat").write_bytes(b"data")
        (self.extensions / "repo.json").write_text("{}", encoding="utf-8")
        self.keymap_export = self.root / "keymap.py"
        self.keymap_fingerprint = self.root / "keymap_fingerprint.json"
        self.keymap_export.write_text("keyconfig = []", encoding="utf-8")
        self.keymap_fingerprint.write_text(
            json.dumps({"schema_version": 1, "items": ["shortcut"]}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def snapshot(self, **overrides):
        values = {
            "version": (5, 1, 0),
            "platform": "win32",
            "install_mode": "normal",
            "binary_path": self.root / "Blender 5.1" / "blender.exe",
            "user_root": self.user_root,
            "config_dir": self.config,
            "scripts_dir": self.scripts,
            "datafiles_dir": self.datafiles,
            "extensions_dir": self.extensions,
            "keymap_export_path": self.keymap_export,
            "keymap_fingerprint_path": self.keymap_fingerprint,
            "keymap_item_count": 1,
            "addons": [
                {
                    "module": "sample_addon",
                    "kind": "legacy",
                    "enabled": True,
                    "version": [1, 0, 0],
                }
            ],
        }
        values.update(overrides)
        return core.SourceSnapshot(**values)

    def test_forward_version_accepts_same_major_higher_minor(self):
        self.assertEqual(core.validate_forward_version((5, 1, 0), (5, 3, 0)), (5, 3, 0))

    def test_forward_version_rejects_downgrade_and_other_major(self):
        with self.assertRaises(core.MigrationError):
            core.validate_forward_version((5, 2, 0), (5, 1, 9))
        with self.assertRaises(core.MigrationError):
            core.validate_forward_version((5, 2, 0), (6, 0, 0))

    def test_output_must_be_outside_blender_user_root(self):
        with self.assertRaises(core.MigrationError):
            core.ensure_external_output(self.user_root / "profiles", [self.user_root])

    def test_profile_contains_default_components_and_exclusions(self):
        result = core.create_profile(self.snapshot(), self.output)
        self.assertTrue(result.path.is_file())
        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())
            self.assertIn("payload/config/userpref.blend", names)
            self.assertIn("payload/config/startup.blend", names)
            self.assertIn("payload/scripts/addons/sample_addon/__init__.py", names)
            self.assertIn("payload/scripts/presets/render/quality.py", names)
            self.assertIn("payload/extensions/repo.json", names)
            self.assertIn("fallback/keymap.py", names)
            self.assertNotIn("payload/config/recent-files.txt", names)
            self.assertNotIn("payload/datafiles/large.dat", names)
            self.assertNotIn("payload/scripts/addons/sample_addon/cache.pyc", names)
        self.assertEqual(result.manifest["schema_version"], 2)
        self.assertEqual(result.manifest["source"]["blender_version"], [5, 1, 0])

    def test_profile_round_trip_validates_hashes(self):
        result = core.create_profile(self.snapshot(include_datafiles=True), self.output)
        staging = self.root / "stage"
        fallback = self.root / "fallback"
        manifest = core.extract_profile(result.path, staging, fallback)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual((staging / "config" / "userpref.blend").read_bytes(), b"preferences")
        self.assertEqual((staging / "datafiles" / "large.dat").read_bytes(), b"data")
        self.assertTrue((fallback / "keymap.py").is_file())

    def test_profile_supports_chinese_and_space_paths(self):
        output = self.root / "中文 profile output"
        result = core.create_profile(self.snapshot(), output)
        staging = self.root / "中文 target stage"
        fallback = self.root / "中文 fallback"
        core.extract_profile(result.path, staging, fallback)
        self.assertTrue((staging / "config" / "userpref.blend").is_file())

    def test_profile_rejects_tampered_file_content(self):
        result = core.create_profile(self.snapshot(), self.output)
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(result.path, "r") as source, zipfile.ZipFile(tampered, "w") as target:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == "payload/config/userpref.blend":
                    data = b"tampered preferences"
                target.writestr(info, data)
        with self.assertRaises(core.MigrationError):
            core.extract_profile(tampered, self.root / "tampered_stage", self.root / "tampered_fallback")

    def test_profile_rejects_path_traversal_and_cleans_staging(self):
        profile = self.root / "malicious.zip"
        payload = b"bad"
        manifest = {
            "schema_version": 2,
            "files": [
                {
                    "path": "payload/../../escape.txt",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }
        with zipfile.ZipFile(profile, "w") as archive:
            archive.writestr("payload/../../escape.txt", payload)
            archive.writestr("manifest.json", json.dumps(manifest))
        staging = self.root / "malicious_stage"
        fallback = self.root / "malicious_fallback"
        with self.assertRaises(core.MigrationError):
            core.extract_profile(profile, staging, fallback)
        self.assertFalse(staging.exists())
        self.assertFalse(fallback.exists())
        self.assertFalse((self.root / "escape.txt").exists())

    def test_profile_rejects_duplicate_archive_entries(self):
        result = core.create_profile(self.snapshot(), self.output)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(result.path, "a") as archive:
                archive.writestr("fallback/keymap.py", b"tampered")
        with self.assertRaises(core.MigrationError):
            core.extract_profile(result.path, self.root / "duplicate_stage", self.root / "duplicate_fallback")

    def test_existing_profile_can_migrate_directly_to_later_minor(self):
        profile = core.create_profile(self.snapshot(), self.output)
        target_root = self.root / "target" / "5.3"
        target_root.mkdir(parents=True)
        (target_root / "old.txt").write_text("target old state", encoding="utf-8")
        recovery_output = self.root / "migration_output"

        def fake_audit(*args, **kwargs):
            report_path = args[4]
            report = {"status": "success", "disabled_addons": [], "warnings": []}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            return report

        probe = {
            "version": [5, 3, 0],
            "user_root": str(target_root),
            "config_dir": str(target_root / "config"),
            "scripts_dir": str(target_root / "scripts"),
            "datafiles_dir": str(target_root / "datafiles"),
            "extensions_dir": str(target_root / "extensions"),
        }
        with mock.patch.object(core, "run_target_probe", return_value=probe), mock.patch.object(
            core, "run_target_audit", side_effect=fake_audit
        ):
            result = core.execute_existing_profile_migration(
                profile.path,
                self.root / "Blender 5.3" / "blender.exe",
                recovery_output,
                self.user_root,
                self.root / "migration_worker.py",
                current_pid=123,
            )
        self.assertEqual(result.target_version, (5, 3, 0))
        self.assertEqual(
            (target_root / "config" / "userpref.blend").read_bytes(),
            b"preferences",
        )
        self.assertTrue((result.recovery_dir / "target_before.zip").is_file())

    def test_audit_failure_rolls_back_and_exposes_recovery_directory(self):
        profile = core.create_profile(self.snapshot(), self.output)
        target_root = self.root / "target" / "5.2"
        target_root.mkdir(parents=True)
        (target_root / "state.txt").write_text("original", encoding="utf-8")
        probe = {
            "version": [5, 2, 0],
            "user_root": str(target_root),
            "config_dir": str(target_root / "config"),
            "scripts_dir": str(target_root / "scripts"),
            "datafiles_dir": str(target_root / "datafiles"),
            "extensions_dir": str(target_root / "extensions"),
        }
        with mock.patch.object(core, "run_target_probe", return_value=probe), mock.patch.object(
            core,
            "run_target_audit",
            side_effect=core.MigrationError("目标审计失败"),
        ):
            with self.assertRaises(core.MigrationError) as caught:
                core.execute_existing_profile_migration(
                    profile.path,
                    self.root / "Blender 5.2" / "blender.exe",
                    self.root / "migration_output",
                    self.user_root,
                    self.root / "migration_worker.py",
                    current_pid=123,
                )
        self.assertEqual((target_root / "state.txt").read_text(encoding="utf-8"), "original")
        self.assertTrue(caught.exception.recovery_dir.is_dir())
        recovery = json.loads(
            (caught.exception.recovery_dir / "recovery.json").read_text(encoding="utf-8")
        )
        self.assertEqual(recovery["status"], "rolled_back")

    def test_atomic_install_can_roll_back(self):
        target = self.root / "target"
        staging = self.root / "staging"
        target.mkdir()
        staging.mkdir()
        (target / "state.txt").write_text("old", encoding="utf-8")
        (staging / "state.txt").write_text("new", encoding="utf-8")
        old_root = core.atomic_install(staging, target, "run")
        self.assertEqual((target / "state.txt").read_text(encoding="utf-8"), "new")
        failed = core.rollback_atomic_install(target, old_root, "run")
        self.assertEqual((target / "state.txt").read_text(encoding="utf-8"), "old")
        self.assertEqual((failed / "state.txt").read_text(encoding="utf-8"), "new")

    def test_directory_backup_and_manual_restore(self):
        target = self.root / "target_profile"
        target.mkdir()
        (target / "state.txt").write_text("before", encoding="utf-8")
        recovery_dir = self.root / "recovery"
        backup = core.create_directory_backup(target, recovery_dir / "target_before.zip")
        recovery = {
            "schema_version": 1,
            "target_executable": str(self.root / "Blender 5.2" / "blender.exe"),
            "target_root": str(target),
            "target_existed": True,
            "backup": backup,
        }
        recovery_file = recovery_dir / "recovery.json"
        recovery_file.write_text(json.dumps(recovery), encoding="utf-8")
        (target / "state.txt").write_text("after", encoding="utf-8")
        result = core.restore_recovery(recovery_file, current_pid=123)
        self.assertEqual(result["status"], "success")
        self.assertEqual((target / "state.txt").read_text(encoding="utf-8"), "before")

    def test_probe_output_uses_structured_marker(self):
        payload = {
            "version": [5, 2, 0],
            "user_root": "C:/Blender/5.2",
            "config_dir": "C:/Blender/5.2/config",
            "scripts_dir": "C:/Blender/5.2/scripts",
            "datafiles_dir": "C:/Blender/5.2/datafiles",
            "extensions_dir": "C:/Blender/5.2/extensions",
        }
        output = "noise\n" + core.PROBE_MARKER + json.dumps(payload) + "\nmore noise"
        self.assertEqual(core.parse_probe_output(output), payload)

    def test_custom_target_resource_override_is_rejected(self):
        probe = {
            "config_dir": "C:/Blender/5.2/config",
            "scripts_dir": "D:/Shared/scripts",
            "datafiles_dir": "C:/Blender/5.2/datafiles",
            "extensions_dir": "C:/Blender/5.2/extensions",
        }
        with self.assertRaises(core.MigrationError):
            core.validate_target_resource_layout(probe, Path("C:/Blender/5.2"))

    def test_free_space_check_rejects_insufficient_disk(self):
        disk_usage = shutil.disk_usage(self.root)
        fake_usage = disk_usage.__class__(disk_usage.total, disk_usage.total - 1024, 1024)
        with mock.patch.object(core.shutil, "disk_usage", return_value=fake_usage):
            with self.assertRaises(core.MigrationError):
                core.ensure_free_space(self.root, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
