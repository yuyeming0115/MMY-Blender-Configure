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
            json.dumps(
                {
                    "schema_version": 2,
                    "items": [
                        {
                            "sig": '{"keymap":"Mesh","idname":"mesh.custom"}',
                            "kind": "added",
                            "idname": "mesh.custom",
                            "keymap": "Mesh",
                        }
                    ],
                }
            ),
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

    def _create_app_template_files(self):
        template = self.scripts / "startup" / "bl_app_templates_user" / "MyTemplate"
        template.mkdir(parents=True)
        (template / "__init__.py").write_text("# template", encoding="utf-8")
        (template / "startup.blend").write_bytes(b"template-startup")
        (self.scripts / "startup" / "custom_module.py").write_text("x = 1", encoding="utf-8")
        return template

    def test_app_templates_packed_as_independent_component(self):
        self._create_app_template_files()

        result = core.create_profile(
            self.snapshot(include_app_templates=True), self.output
        )
        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())
            self.assertIn(
                "payload/scripts/startup/bl_app_templates_user/MyTemplate/__init__.py",
                names,
            )
            # 模板内 startup.blend（.blend 后缀）不被快照排除规则误杀
            self.assertIn(
                "payload/scripts/startup/bl_app_templates_user/MyTemplate/startup.blend",
                names,
            )
            # 未勾选启动脚本时，startup 下其余脚本不进入快照
            self.assertNotIn("payload/scripts/startup/custom_module.py", names)
        self.assertIn("app_templates", result.manifest["components"])
        self.assertNotIn("startup_scripts", result.manifest["components"])

        # 恢复侧按归档路径落位，与 Blender 标准模板目录一致
        staging = self.root / "stage"
        fallback = self.root / "fallback"
        core.extract_profile(result.path, staging, fallback)
        self.assertTrue(
            (
                staging
                / "scripts"
                / "startup"
                / "bl_app_templates_user"
                / "MyTemplate"
                / "startup.blend"
            ).is_file()
        )

    def test_app_templates_and_startup_scripts_no_duplicate_paths(self):
        self._create_app_template_files()

        # 两开关同时开启：模板仅通过独立组件进入。若 startup_scripts 组件
        # 未排除该子目录，add_file 的路径查重会先抛「配置快照内路径重复」
        result = core.create_profile(
            self.snapshot(include_startup_scripts=True, include_app_templates=True),
            self.output,
        )
        with zipfile.ZipFile(result.path) as archive:
            names = archive.namelist()
            template_entries = [
                name for name in names if "bl_app_templates_user" in name
            ]
            self.assertEqual(
                sorted(template_entries),
                [
                    "payload/scripts/startup/bl_app_templates_user/MyTemplate/__init__.py",
                    "payload/scripts/startup/bl_app_templates_user/MyTemplate/startup.blend",
                ],
            )
            self.assertIn("payload/scripts/startup/custom_module.py", names)
        self.assertIn("startup_scripts", result.manifest["components"])
        self.assertIn("app_templates", result.manifest["components"])

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
        probe = {
            "version": [5, 2, 0],
            "user_root": str(target),
            "config_dir": str(target / "config"),
            "scripts_dir": str(target / "scripts"),
            "datafiles_dir": str(target / "datafiles"),
            "extensions_dir": str(target / "extensions"),
        }
        with mock.patch.object(core, "run_target_probe", return_value=probe), mock.patch.object(
            core, "windows_executable_is_running", return_value=False
        ):
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

    def test_probe_output_skips_traceback_echo_lines(self):
        """目标启动失败时，错误回显中的 marker 行不能当作探针结果。"""
        payload = {
            "version": [5, 3, 0],
            "user_root": "C:/Blender/5.3",
            "config_dir": "C:/Blender/5.3/config",
            "scripts_dir": "C:/Blender/5.3/scripts",
            "datafiles_dir": "C:/Blender/5.3/datafiles",
            "extensions_dir": "C:/Blender/5.3/extensions",
        }
        valid_line = core.PROBE_MARKER + json.dumps(payload)
        echo_line = "  print('" + core.PROBE_MARKER + "'+json.dumps({...}))"
        output = valid_line + "\nTraceback ...\n" + echo_line + "\nSyntaxError"
        self.assertEqual(core.parse_probe_output(output), payload)

    def test_probe_output_only_echo_lines_gives_clear_error(self):
        echo_line = "  print('" + core.PROBE_MARKER + "'+json.dumps({...}))"
        with self.assertRaises(core.MigrationError) as caught:
            core.parse_probe_output("Traceback\n" + echo_line)
        self.assertIn("执行失败", str(caught.exception))

    def test_blender_subprocess_env_strips_python_vars(self):
        with mock.patch.dict(
            core.os.environ,
            {"PYTHONHOME": "C:/bad", "PYTHONPATH": "D:/bad", "PATH": core.os.environ.get("PATH", "")},
        ):
            env = core._blender_subprocess_env()
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertIn("PATH", env)

    def test_probe_expression_is_valid_python(self):
        """回归：v1.2.0 曾因 f-string {{ 与普通字符串 }} 混用导致表达式括号不平衡。"""
        expression = core._build_probe_expression()
        compile(expression, "<probe>", "exec")  # 语法错误会直接抛 SyntaxError
        self.assertEqual(expression.count("{"), expression.count("}"))

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

    # ---------------- v1.3.0 新增 ----------------

    def test_profile_uses_new_backup_prefix(self):
        result = core.create_profile(self.snapshot(), self.output)
        self.assertTrue(result.path.name.startswith("MMY_Backup_Profile_v5.1.0_"))

    def test_predict_addon_compatibility(self):
        addons = [
            {"module": "ok_addon", "kind": "legacy", "enabled": True,
             "blender_version_min": [4, 0, 0]},
            {"module": "too_new", "kind": "extension", "enabled": True,
             "blender_version_min": "5.3.0"},
            {"module": "capped", "kind": "extension", "enabled": True,
             "blender_version_max": "5.1.0"},
            {"module": "disabled_addon", "kind": "legacy", "enabled": False,
             "blender_version_min": [9, 0, 0]},
        ]
        result = core.predict_addon_compatibility(addons, (5, 2, 0))
        modules = {item["module"] for item in result}
        self.assertEqual(modules, {"too_new", "capped"})

    def test_list_backup_entries_mixed_naming_and_recovery(self):
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "MMY_Backup_Portable_v5.1.0_20260818_1200.zip").write_bytes(b"pk")
        (self.output / "Blender_Portable_v5.0.0_20260101_0000.zip").write_bytes(b"pk")
        (self.output / "MMY_Backup_Profile_v5.1.0_20260818_120100.zip").write_bytes(b"pk")
        (self.output / "unrelated.zip").write_bytes(b"pk")
        run_dir = self.output / core.RECOVERY_DIR_NAME / "5.1.0_to_5.2.0_run1"
        run_dir.mkdir(parents=True)
        (run_dir / "target_before.zip").write_bytes(b"pk")
        (run_dir / "recovery.json").write_text(json.dumps({
            "schema_version": 1,
            "source_version": [5, 1, 0],
            "target_version": [5, 2, 0],
            "status": "success",
            "backup": {"path": str(run_dir / "target_before.zip")},
        }), encoding="utf-8")
        entries = core.list_backup_entries(self.output)
        types = sorted(entry["type"] for entry in entries)
        self.assertEqual(types, ["portable", "portable", "profile", "recovery"])
        recovery = [e for e in entries if e["type"] == "recovery"][0]
        self.assertEqual(recovery["version_label"], "5.1.0 → 5.2.0")
        self.assertEqual(recovery["status"], "success")

    def test_cleanup_stale_migration_artifacts(self):
        parent = self.root / "blender_dir"
        parent.mkdir()
        stale = parent / ".5.2.mmy_old_20260801_000000_abcd1234"
        stale.mkdir()
        (stale / "junk.txt").write_text("x", encoding="utf-8")
        keep = parent / "5.2"
        keep.mkdir()
        # 默认 24 小时：刚创建的残留不清理
        self.assertEqual(core.cleanup_stale_migration_artifacts([parent]), [])
        # max_age=0：全部清理，但不误伤正常目录
        removed = core.cleanup_stale_migration_artifacts([parent], max_age_hours=0)
        self.assertEqual(len(removed), 1)
        self.assertFalse(stale.exists())
        self.assertTrue(keep.exists())

    def test_write_migration_report_html(self):
        report = {
            "status": "degraded",
            "source_version": [5, 1, 0],
            "target_version": [5, 2, 0],
            "target_user_root": "C:/Blender/5.2",
            "disabled_addons": [
                {"module": "bad_addon", "kind": "legacy",
                 "reason": "插件未能在目标版本加载", "disabled": True}
            ],
            "keymap": {"source_count": 10, "matched_count": 9, "lost_count": 1,
                       "lost": [{"keymap": "Mesh", "idname": "mesh.custom_op"}],
                       "orphan_operators": ["uv.gone_op"], "unverifiable_count": 2},
            "missing_paths": [],
            "warnings": ["示例警告"],
        }
        metadata = {"created_at": "2026-08-18T12:00:00", "status": "degraded"}
        html_path = self.output / "migration_report.html"
        self.output.mkdir(exist_ok=True)
        core.write_migration_report_html(report, metadata, html_path)
        text = html_path.read_text(encoding="utf-8")
        self.assertIn("5.1.0", text)
        self.assertIn("5.2.0", text)
        self.assertIn("bad_addon", text)
        self.assertIn("降级", text)
        self.assertIn("mesh.custom_op", text)
        self.assertIn("uv.gone_op", text)

    def test_classify_keymap_audit_categories(self):
        expected = [
            # 用户新增 + 操作符存在 + 目标缺失 → 真丢失
            {"sig": "s1", "kind": "added", "idname": "mmy.custom", "keymap": "Mesh"},
            # 用户新增但操作符不存在（插件后台未注册/版本移除）→ orphan
            {"sig": "s2", "kind": "added", "idname": "uv.univ_cut", "keymap": "UV Editor"},
            # 修改默认项 → 后台不可验证
            {"sig": "s3", "kind": "modified", "idname": "view3d.rotate", "keymap": "3D View"},
            # 修改插件注册项（如用户改过的 univ 快捷键）→ 后台不可验证
            {"sig": "s7", "kind": "addon", "idname": "uv.univ_rotate", "keymap": "UV Editor"},
            # 模态项（无 idname）→ 后台不可验证
            {"sig": "s4", "kind": "added", "idname": "", "keymap": "Knife Tool Modal Map"},
            # 已匹配项
            {"sig": "s5", "kind": "added", "idname": "mmy.ok", "keymap": "Mesh"},
            # 旧格式（纯字符串签名）→ 按不可验证处理，不误判丢失
            "s6",
        ]
        actual = {"s5"}
        existing_ops = {"mmy.custom", "view3d.rotate", "uv.univ_rotate"}
        result = core.classify_keymap_audit(
            expected, actual, lambda idname: idname in existing_ops
        )
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["lost_count"], 1)
        self.assertEqual(result["lost"][0]["idname"], "mmy.custom")
        self.assertEqual(result["orphan_operators"], ["uv.univ_cut"])
        self.assertEqual(result["unverifiable_count"], 4)

    def test_classify_keymap_audit_no_false_positive_when_all_explainable(self):
        # 真机场景复现：差异项全部可归因（插件后台未注册/修改默认项）时不得判丢失
        expected = [
            {"sig": "s1", "kind": "added", "idname": "uv.univ_cut", "keymap": "UV Editor"},
            {"sig": "s2", "kind": "modified", "idname": "view3d.rotate", "keymap": "3D View"},
        ]
        result = core.classify_keymap_audit(expected, set(), lambda idname: False)
        self.assertEqual(result["lost_count"], 0)
        self.assertEqual(result["orphan_operators"], ["uv.univ_cut"])
        self.assertEqual(result["unverifiable_count"], 1)

    def test_extract_portable_backup_skips_manifest_and_restores(self):
        backup = self.root / "MMY_Backup_Portable_v5.1.0_20260818_1200.zip"
        with zipfile.ZipFile(backup, "w") as zf:
            zf.writestr("portable/5.1/config/userpref.blend", b"preferences")
            zf.writestr("manifest.json", json.dumps({"type": "portable"}))
        dest = self.root / "restore_dest"
        count = core.extract_portable_backup(backup, dest)
        self.assertEqual(count, 1)
        self.assertEqual(
            (dest / "portable" / "5.1" / "config" / "userpref.blend").read_bytes(),
            b"preferences",
        )
        self.assertFalse((dest / "manifest.json").exists())
        manifest = core.read_portable_backup_manifest(backup)
        self.assertEqual(manifest.get("type"), "portable")

    def test_multi_target_continues_after_single_failure(self):
        profile = core.create_profile(self.snapshot(), self.output)
        good_root = self.root / "target" / "5.3"
        good_root.mkdir(parents=True)
        good_probe = {
            "version": [5, 3, 0],
            "user_root": str(good_root),
            "config_dir": str(good_root / "config"),
            "scripts_dir": str(good_root / "scripts"),
            "datafiles_dir": str(good_root / "datafiles"),
            "extensions_dir": str(good_root / "extensions"),
        }

        def fake_probe(exe, timeout=60):
            if "Good" in str(exe):
                return good_probe
            raise core.MigrationError("目标 Blender 不存在")

        def fake_audit(*args, **kwargs):
            report_path = args[4]
            report = {"status": "success", "disabled_addons": [], "warnings": []}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            return report

        with mock.patch.object(core, "run_target_probe", side_effect=fake_probe), \
             mock.patch.object(core, "run_target_audit", side_effect=fake_audit), \
             mock.patch.object(core, "windows_executable_is_running", return_value=False):
            used_profile, outcomes = core.execute_existing_profile_migration_multi(
                profile.path,
                [self.root / "Good 5.3" / "blender.exe",
                 self.root / "Bad 5.3" / "blender.exe"],
                self.root / "migration_output",
                self.user_root,
                self.root / "migration_worker.py",
                current_pid=123,
            )
        self.assertEqual(len(outcomes), 2)
        good = [o for o in outcomes if o.result is not None]
        bad = [o for o in outcomes if o.error]
        self.assertEqual(len(good), 1)
        self.assertEqual(len(bad), 1)
        self.assertIn("目标 Blender 不存在", bad[0].error)
        # HTML 报告应已生成
        self.assertTrue(good[0].result.report_html_path.is_file())
        self.assertTrue(
            (good_root / "config" / "userpref.blend").is_file()
        )


class PackScriptTest(unittest.TestCase):
    """pack_script.py 的命名与 manifest 写入（B1）。"""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "mmy_pack_script",
            ROOT / "mmy_pack_config" / "pack_script.py",
        )
        cls.pack = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.pack
        spec.loader.exec_module(cls.pack)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.portable = self.root / "Blender 5.1" / "portable"
        (self.portable / "5.1" / "config").mkdir(parents=True)
        (self.portable / "5.1" / "config" / "userpref.blend").write_bytes(b"p")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_filename_uses_new_prefix(self):
        name = self.pack.normalize_output_path("", self.portable, "5.1.0")
        self.assertIn("MMY_Backup_Portable_v5.1.0_", Path(name).name)

    def test_pack_writes_manifest(self):
        output = self.root / "out" / "MMY_Backup_Portable_v5.1.0_test.zip"
        self.pack.pack_portable(self.portable, output, version="5.1.0")
        with zipfile.ZipFile(output) as zf:
            self.assertIn("manifest.json", zf.namelist())
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        self.assertEqual(manifest["type"], "portable")
        self.assertEqual(manifest["blender_version"], "5.1.0")
        self.assertEqual(manifest["file_count"], 1)
        self.assertTrue(manifest.get("machine") is not None)


class DetectConfigFileTest(unittest.TestCase):
    """detect_config_file：统一导入的类型自动识别（v1.4.0）。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_zip(self, name, entries):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as zf:
            for arcname, data in entries.items():
                zf.writestr(arcname, data)
        return path

    def test_detects_profile_by_manifest_without_type(self):
        path = self._write_zip(
            "MMY_Backup_Profile_v5.1.0_20260906.zip",
            {"manifest.json": json.dumps({"schema_version": 2, "source": {}})},
        )
        self.assertEqual(core.detect_config_file(path), "profile")

    def test_detects_portable_by_manifest_type(self):
        path = self._write_zip(
            "MMY_Backup_Portable_v5.1.0_20260906.zip",
            {"manifest.json": json.dumps({"type": "portable"}), "portable/5.1/config/x": "d"},
        )
        self.assertEqual(core.detect_config_file(path), "portable")

    def test_detects_recovery_json(self):
        path = self.root / "recovery.json"
        path.write_text("{}", encoding="utf-8")
        self.assertEqual(core.detect_config_file(path), "recovery")

    def test_unknown_for_other_json_and_broken_zip(self):
        other = self.root / "other.json"
        other.write_text("{}", encoding="utf-8")
        self.assertEqual(core.detect_config_file(other), "unknown")
        broken = self.root / "broken.zip"
        broken.write_bytes(b"not a zip")
        self.assertEqual(core.detect_config_file(broken), "unknown")
        no_manifest = self._write_zip("nomani.zip", {"a.txt": "x"})
        self.assertEqual(core.detect_config_file(no_manifest), "unknown")


if __name__ == "__main__":
    unittest.main()
