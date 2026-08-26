"""Blender 跨版本配置迁移的纯 Python 核心。

本模块不依赖 ``bpy``，负责配置快照、目标探测、安全解压、事务切换、
后台审计编排和恢复，因此可以在 Blender 外直接做单元测试。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCHEMA_VERSION = 2
PROFILE_PREFIX = "MMY_Backup_Profile"
LEGACY_PROFILE_PREFIX = "MMY_Blender_Profile"
PORTABLE_BACKUP_PREFIX = "MMY_Backup_Portable"
LEGACY_PORTABLE_BACKUP_PREFIX = "Blender_Portable"
RECOVERY_DIR_NAME = "MMY_Migration_Recovery"
PROBE_MARKER = "MMY_PROBE_JSON:"
MIN_FREE_SPACE_BYTES = 128 * 1024 * 1024

_STALE_ARTIFACT_PATTERN = re.compile(
    r"^\.[^\s].*\.mmy_(old|stage|failed|restore|removed)_"
)


class MigrationError(RuntimeError):
    """可向用户展示的迁移错误。"""

    def __init__(self, message: str, recovery_dir: Path | None = None):
        super().__init__(message)
        self.recovery_dir = recovery_dir


@dataclass(frozen=True)
class SourceSnapshot:
    version: tuple[int, int, int]
    platform: str
    install_mode: str
    binary_path: Path
    user_root: Path
    config_dir: Path
    scripts_dir: Path
    datafiles_dir: Path
    extensions_dir: Path
    keymap_export_path: Path
    keymap_fingerprint_path: Path
    keymap_item_count: int
    addons: list[dict[str, Any]]
    include_presets: bool = True
    include_datafiles: bool = False
    include_startup_scripts: bool = False
    include_app_templates: bool = False
    include_history: bool = False


@dataclass
class ProfileResult:
    path: Path
    manifest: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@dataclass
class MigrationResult:
    status: str
    profile_path: Path
    recovery_dir: Path
    report_path: Path
    target_version: tuple[int, int, int]
    disabled_addons: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report_html_path: Path | None = None
    keymap_lost_count: int = 0


@dataclass
class TargetMigrationOutcome:
    """多目标批量迁移中单个目标的结果。"""

    target_executable: Path
    result: MigrationResult | None = None
    error: str = ""
    exception: Exception | None = None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_id() -> str:
    return f"{_timestamp()}_{uuid.uuid4().hex[:8]}"


def version_string(version: Iterable[int]) -> str:
    values = list(version)
    return ".".join(str(value) for value in values[:3])


def classify_keymap_audit(
    expected_items: list,
    actual_signatures: set,
    operator_exists,
) -> dict[str, Any]:
    """对键位指纹差异做分类（纯函数，不依赖 bpy，可单测）。

    后台审计模式无法物化默认键位、且部分插件跳过后台注册，因此只有
    「用户新增 + 操作符在目标存在 + 目标缺失」才判定为真丢失（lost）：

    - ``kind != "added"`` 或 idname 为空 → unverifiable（"modified" 修改默认项
      依赖目标版本键位结构、"addon" 修改插件注册项依赖插件注册、模态项，
      后台均无法可靠验证）；
    - 操作符在目标审计环境不存在 → orphan（插件后台未注册，或目标版本
      已移除该操作；GUI 下能注册的插件会自行恢复自己的键位）；
    - 其余缺失 → lost（真丢失，触发 degraded）。
    """
    lost: list[dict[str, str]] = []
    orphans: list[str] = []
    unverifiable = 0
    matched = 0
    for raw in expected_items:
        entry = raw if isinstance(raw, dict) else {"sig": str(raw)}
        if entry.get("sig") in actual_signatures:
            matched += 1
            continue
        idname = str(entry.get("idname") or "")
        if not idname or entry.get("kind") != "added":
            unverifiable += 1
        elif not operator_exists(idname):
            orphans.append(idname)
        else:
            lost.append({"keymap": str(entry.get("keymap") or ""), "idname": idname})
    return {
        "matched_count": matched,
        "lost_count": len(lost),
        "lost": lost[:100],
        "orphan_operators": sorted(set(orphans)),
        "unverifiable_count": unverifiable,
    }


def normalize_version(value: Iterable[int]) -> tuple[int, int, int]:
    values = [int(part) for part in value]
    values.extend([0] * (3 - len(values)))
    return tuple(values[:3])


def validate_forward_version(
    source: Iterable[int], target: Iterable[int]
) -> tuple[int, int, int]:
    source_version = normalize_version(source)
    target_version = normalize_version(target)
    if source_version[0] != target_version[0]:
        raise MigrationError(
            f"仅支持同主版本迁移：当前 {version_string(source_version)}，"
            f"目标 {version_string(target_version)}"
        )
    if target_version <= source_version:
        raise MigrationError(
            f"目标版本必须高于当前版本：当前 {version_string(source_version)}，"
            f"目标 {version_string(target_version)}"
        )
    return target_version


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def path_is_within(path: Path | str, parent: Path | str) -> bool:
    try:
        _resolved(path).relative_to(_resolved(parent))
        return True
    except ValueError:
        return False


def ensure_external_output(output_dir: Path, protected_roots: Iterable[Path]) -> Path:
    output = _resolved(output_dir)
    for root in protected_roots:
        if root and path_is_within(output, root):
            raise MigrationError(f"迁移输出目录不能位于 Blender 用户目录内部：{output}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def ensure_free_space(path: Path, required_bytes: int) -> None:
    probe = path if path.exists() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    required = max(int(required_bytes), MIN_FREE_SPACE_BYTES)
    if free < required:
        raise MigrationError(
            f"磁盘空间不足：需要约 {required / 1024 / 1024:.0f} MB，"
            f"可用 {free / 1024 / 1024:.0f} MB"
        )


_SKIP_DIR_NAMES = {
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "cacheddata",
    "code cache",
    "gpucache",
    "crashpad",
    ".git",
    ".hg",
    ".svn",
}
_SKIP_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".temp",
    ".bak",
    ".backup",
    ".old",
    ".blend1",
    ".blend2",
    ".blend3",
    ".part",
    ".crdownload",
}
_SKIP_FILE_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def should_skip_snapshot_file(relative_path: Path) -> bool:
    parts = {part.casefold() for part in relative_path.parts[:-1]}
    name = relative_path.name.casefold()
    return bool(
        parts.intersection(_SKIP_DIR_NAMES)
        or name in _SKIP_FILE_NAMES
        or name.startswith("._")
        or relative_path.suffix.casefold() in _SKIP_SUFFIXES
    )


def _iter_regular_files(
    source: Path,
    exclude_top_dirs: frozenset[str] = frozenset(),
) -> Iterable[tuple[Path, Path]]:
    if not source.exists():
        return
    for item in sorted(source.rglob("*"), key=lambda value: str(value).casefold()):
        if item.is_symlink() or not item.is_file():
            continue
        relative = item.relative_to(source)
        if exclude_top_dirs and relative.parts[0] in exclude_top_dirs:
            continue
        if not should_skip_snapshot_file(relative):
            yield item, relative


def _safe_archive_path(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise MigrationError(f"压缩包包含非法路径：{name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise MigrationError(f"压缩包包含越界路径：{name}")
    return path


def _zipinfo_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _json_dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


_APP_TEMPLATES_DIR_NAME = "bl_app_templates_user"


def _profile_sources(snapshot: SourceSnapshot) -> list[tuple[Path, str, str, frozenset[str]]]:
    """返回 (来源路径, 归档基路径, 组件名, 顶层排除子目录) 列表。

    归档路径刻意保持 ``payload/scripts/startup/bl_app_templates_user``，
    与 Blender 标准用户模板位置一致，恢复侧按路径落位即可，无需特判。
    """
    sources: list[tuple[Path, str, str, frozenset[str]]] = [
        (snapshot.config_dir / "userpref.blend", "payload/config/userpref.blend", "preferences", frozenset()),
        (snapshot.config_dir / "startup.blend", "payload/config/startup.blend", "startup_file", frozenset()),
        (snapshot.scripts_dir / "addons", "payload/scripts/addons", "addons", frozenset()),
        (snapshot.extensions_dir, "payload/extensions", "extensions", frozenset()),
    ]
    if snapshot.include_presets:
        sources.append((snapshot.scripts_dir / "presets", "payload/scripts/presets", "presets", frozenset()))
    if snapshot.include_datafiles:
        sources.append((snapshot.datafiles_dir, "payload/datafiles", "datafiles", frozenset()))
    if snapshot.include_startup_scripts:
        # 应用模板从启动脚本组件中排除，避免与独立组件重复打包触发路径重复校验
        sources.append(
            (
                snapshot.scripts_dir / "startup",
                "payload/scripts/startup",
                "startup_scripts",
                frozenset({_APP_TEMPLATES_DIR_NAME}),
            )
        )
    if snapshot.include_app_templates:
        sources.append(
            (
                snapshot.scripts_dir / "startup" / _APP_TEMPLATES_DIR_NAME,
                f"payload/scripts/startup/{_APP_TEMPLATES_DIR_NAME}",
                "app_templates",
                frozenset(),
            )
        )
    if snapshot.include_history:
        sources.extend(
            [
                (snapshot.config_dir / "bookmarks.txt", "payload/config/bookmarks.txt", "history", frozenset()),
                (snapshot.config_dir / "recent-files.txt", "payload/config/recent-files.txt", "history", frozenset()),
            ]
        )
    return sources


def create_profile(snapshot: SourceSnapshot, output_dir: Path) -> ProfileResult:
    output = ensure_external_output(
        output_dir,
        [
            snapshot.user_root,
            snapshot.config_dir,
            snapshot.scripts_dir,
            snapshot.datafiles_dir,
            snapshot.extensions_dir,
        ],
    )
    version = version_string(snapshot.version)
    profile_path = output / f"{PROFILE_PREFIX}_v{version}_{_timestamp()}.zip"
    counter = 1
    while profile_path.exists():
        profile_path = output / f"{PROFILE_PREFIX}_v{version}_{_timestamp()}_({counter}).zip"
        counter += 1

    if not (snapshot.config_dir / "userpref.blend").is_file():
        raise MigrationError("未找到 userpref.blend，请先保存 Blender 偏好设置")
    if not snapshot.keymap_export_path.is_file():
        raise MigrationError("快捷键导出文件不存在")
    if not snapshot.keymap_fingerprint_path.is_file():
        raise MigrationError("快捷键指纹文件不存在")

    files: list[dict[str, Any]] = []
    components: set[str] = set()
    warnings: list[str] = []
    archive_names: set[str] = set()

    def add_file(zf: zipfile.ZipFile, source: Path, archive_name: str, component: str) -> None:
        archive_name = str(_safe_archive_path(archive_name))
        if archive_name in archive_names:
            raise MigrationError(f"配置快照内路径重复：{archive_name}")
        archive_names.add(archive_name)
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as source_handle, zf.open(
            archive_name,
            "w",
            force_zip64=True,
        ) as archive_handle:
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                archive_handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        files.append(
            {
                "path": archive_name,
                "size": size,
                "sha256": digest.hexdigest(),
                "component": component,
            }
        )
        components.add(component)

    try:
        with zipfile.ZipFile(profile_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for source, archive_base, component, exclude_top_dirs in _profile_sources(snapshot):
                if source.is_symlink():
                    warnings.append(f"已跳过符号链接：{source}")
                    continue
                if source.is_file():
                    add_file(zf, source, archive_base, component)
                    continue
                if source.is_dir():
                    found = False
                    for item, relative in _iter_regular_files(source, exclude_top_dirs):
                        found = True
                        add_file(
                            zf,
                            item,
                            f"{archive_base}/{relative.as_posix()}",
                            component,
                        )
                    if not found:
                        warnings.append(f"目录为空或不存在可迁移文件：{source}")
                    continue
                if component in {"startup_file", "addons", "extensions"}:
                    warnings.append(f"来源组件不存在：{source}")

            add_file(
                zf,
                snapshot.keymap_export_path,
                "fallback/keymap.py",
                "keymap_fallback",
            )
            add_file(
                zf,
                snapshot.keymap_fingerprint_path,
                "fallback/keymap_fingerprint.json",
                "keymap_fallback",
            )

            manifest = {
                "schema_version": SCHEMA_VERSION,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "source": {
                    "blender_version": list(normalize_version(snapshot.version)),
                    "platform": snapshot.platform,
                    "install_mode": snapshot.install_mode,
                },
                "target_policy": {
                    "forward_only": True,
                    "same_major_only": True,
                },
                "components": sorted(components),
                "addons": snapshot.addons,
                "keymap": {
                    "item_count": snapshot.keymap_item_count,
                    "fingerprint_file": "fallback/keymap_fingerprint.json",
                    "export_file": "fallback/keymap.py",
                },
                "files": files,
                "warnings": warnings,
            }
            zf.writestr("manifest.json", _json_dump(manifest))
    except Exception:
        profile_path.unlink(missing_ok=True)
        raise

    return ProfileResult(path=profile_path, manifest=manifest, warnings=warnings)


def read_profile_manifest(profile_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(profile_path, "r") as zf:
            manifest_entries = [
                info for info in zf.infolist() if info.filename == "manifest.json"
            ]
            if len(manifest_entries) != 1:
                raise MigrationError("配置快照必须且只能包含一个 manifest.json")
            info = manifest_entries[0]
            if _zipinfo_is_symlink(info):
                raise MigrationError("manifest.json 不能是符号链接")
            manifest = json.loads(zf.read(info).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"无效的配置快照：{exc}") from exc
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise MigrationError(
            f"不支持的配置快照版本：{manifest.get('schema_version')}"
        )
    if not isinstance(manifest.get("files"), list):
        raise MigrationError("配置快照缺少文件清单")
    return manifest


def extract_profile(
    profile_path: Path,
    staging_root: Path,
    fallback_root: Path,
) -> dict[str, Any]:
    manifest = read_profile_manifest(profile_path)
    if staging_root.exists() or fallback_root.exists():
        raise MigrationError("迁移临时目录已存在，已停止以避免覆盖")
    staging_root.mkdir(parents=True)
    fallback_root.mkdir(parents=True)
    try:
        records: dict[str, dict[str, Any]] = {}
        for record in manifest["files"]:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise MigrationError("配置快照文件清单格式无效")
            name = str(_safe_archive_path(record["path"]))
            if name in records:
                raise MigrationError(f"配置快照文件清单重复：{name}")
            records[name] = record

        with zipfile.ZipFile(profile_path, "r") as zf:
            archive_entries = [
                info
                for info in zf.infolist()
                if not info.is_dir() and info.filename != "manifest.json"
            ]
            archive_files = {info.filename: info for info in archive_entries}
            if len(archive_entries) != len(archive_files):
                raise MigrationError("配置快照包含重复文件条目")
            if set(archive_files) != set(records):
                missing = sorted(set(records) - set(archive_files))
                extra = sorted(set(archive_files) - set(records))
                raise MigrationError(f"配置快照文件清单不一致，缺少 {missing}，多出 {extra}")

            for name, record in records.items():
                info = archive_files[name]
                _safe_archive_path(info.filename)
                if _zipinfo_is_symlink(info):
                    raise MigrationError(f"配置快照不允许符号链接：{name}")
                expected_size = int(record.get("size", -1))
                if info.file_size != expected_size:
                    raise MigrationError(f"文件大小校验失败：{name}")

                if name.startswith("payload/"):
                    relative = _safe_archive_path(name[len("payload/") :])
                    target = staging_root.joinpath(*relative.parts)
                elif name.startswith("fallback/"):
                    relative = _safe_archive_path(name[len("fallback/") :])
                    target = fallback_root.joinpath(*relative.parts)
                else:
                    raise MigrationError(f"配置快照包含未知区域：{name}")

                if not path_is_within(target, staging_root) and not path_is_within(target, fallback_root):
                    raise MigrationError(f"配置快照目标路径越界：{name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with zf.open(info, "r") as source, target.open("xb") as destination:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        destination.write(chunk)
                        digest.update(chunk)
                if digest.hexdigest() != record.get("sha256"):
                    raise MigrationError(f"SHA-256 校验失败：{name}")
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(fallback_root, ignore_errors=True)
        raise
    return manifest


def create_directory_backup(source_root: Path, backup_path: Path) -> dict[str, Any]:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    skipped_symlinks: list[str] = []
    try:
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            if source_root.exists():
                for item in sorted(source_root.rglob("*"), key=lambda value: str(value).casefold()):
                    if item.is_symlink():
                        skipped_symlinks.append(str(item.relative_to(source_root)))
                        continue
                    if not item.is_file():
                        continue
                    relative = item.relative_to(source_root).as_posix()
                    _safe_archive_path(relative)
                    digest = sha256_file(item)
                    size = item.stat().st_size
                    zf.write(item, relative)
                    records.append({"path": relative, "size": size, "sha256": digest})
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    return {
        "path": str(backup_path),
        "sha256": sha256_file(backup_path),
        "files": records,
        "skipped_symlinks": skipped_symlinks,
    }


def extract_directory_backup(
    backup_path: Path,
    records: list[dict[str, Any]],
    staging_root: Path,
) -> None:
    if staging_root.exists():
        raise MigrationError(f"恢复临时目录已存在：{staging_root}")
    staging_root.mkdir(parents=True)
    expected = {str(_safe_archive_path(item["path"])): item for item in records}
    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            actual = {
                info.filename: info
                for info in zf.infolist()
                if not info.is_dir()
            }
            if set(actual) != set(expected):
                raise MigrationError("恢复包内容与清单不一致")
            for name, record in expected.items():
                info = actual[name]
                if _zipinfo_is_symlink(info) or info.file_size != int(record["size"]):
                    raise MigrationError(f"恢复包文件校验失败：{name}")
                target = staging_root.joinpath(*_safe_archive_path(name).parts)
                if not path_is_within(target, staging_root):
                    raise MigrationError(f"恢复包目标路径越界：{name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with zf.open(info) as source, target.open("xb") as destination:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        destination.write(chunk)
                        digest.update(chunk)
                if digest.hexdigest() != record["sha256"]:
                    raise MigrationError(f"恢复包 SHA-256 校验失败：{name}")
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def atomic_install(staging_root: Path, target_root: Path, run_id: str) -> Path | None:
    target_root.parent.mkdir(parents=True, exist_ok=True)
    old_root = target_root.parent / f".{target_root.name}.mmy_old_{run_id}"
    if old_root.exists():
        raise MigrationError(f"事务备份目录已存在：{old_root}")
    had_target = target_root.exists()
    if had_target:
        os.replace(target_root, old_root)
    try:
        os.replace(staging_root, target_root)
    except Exception:
        if had_target and old_root.exists() and not target_root.exists():
            os.replace(old_root, target_root)
        raise
    return old_root if had_target else None


def finalize_atomic_install(old_root: Path | None) -> None:
    if old_root and old_root.exists():
        shutil.rmtree(old_root)


def rollback_atomic_install(
    target_root: Path,
    old_root: Path | None,
    run_id: str,
) -> Path | None:
    failed_root = target_root.parent / f".{target_root.name}.mmy_failed_{run_id}"
    if target_root.exists():
        if failed_root.exists():
            shutil.rmtree(failed_root, ignore_errors=True)
        os.replace(target_root, failed_root)
    if old_root and old_root.exists():
        os.replace(old_root, target_root)
    return failed_root if failed_root.exists() else None


def parse_probe_output(output: str) -> dict[str, Any]:
    """从目标 Blender 输出中解析探针结果。

    从尾部扫描 marker 行；跳过 payload 不以 '{' 开头的行——目标启动失败时，
    错误堆栈会把探针表达式原文（含 marker 字符串）回显到输出里，
    不能把这种行当作探针结果。
    """
    found_marker = False
    for line in reversed(output.splitlines()):
        if PROBE_MARKER not in line:
            continue
        found_marker = True
        payload = line.split(PROBE_MARKER, 1)[1].strip()
        if not payload.startswith("{"):
            continue
        try:
            result = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MigrationError(f"目标 Blender 探针输出无效：{exc}") from exc
        required = {
            "version",
            "user_root",
            "config_dir",
            "scripts_dir",
            "datafiles_dir",
            "extensions_dir",
        }
        if not required.issubset(result):
            raise MigrationError("目标 Blender 探针缺少必要路径")
        return result
    if found_marker:
        raise MigrationError(
            "目标 Blender 探针表达式执行失败（输出中仅有错误回显），"
            "请手动以 --background 启动目标 Blender 排查"
        )
    raise MigrationError("目标 Blender 未返回探针结果")


def _blender_subprocess_env() -> dict[str, str]:
    """构造启动目标 Blender 的干净环境。

    移除可能污染目标内嵌 Python 的变量（PYTHONHOME/PYTHONPATH 等），
    避免宿主进程环境导致目标 Blender 标准库加载失败。
    """
    environment = os.environ.copy()
    for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE"):
        environment.pop(variable, None)
    return environment


def validate_target_resource_layout(probe: dict[str, Any], target_root: Path) -> None:
    for key in ("config_dir", "scripts_dir", "datafiles_dir", "extensions_dir"):
        value = probe.get(key)
        if value and not path_is_within(Path(value), target_root):
            raise MigrationError(
                f"目标 Blender 使用了独立的 {key} 环境变量路径，第一版不支持自动迁移"
            )


def _build_probe_expression() -> str:
    """构造目标 Blender 探针表达式。

    注意：必须使用普通字符串拼接。之前把 f-string 的 `{{` 与
    普通字符串的 `}}` 混用，导致表达式括号不平衡（v1.2.0 潜伏 bug，
    真机首次跑探针才暴露）。本函数有单测保证可编译。
    """
    return (
        "import bpy,json;"
        "print('" + PROBE_MARKER + "'+json.dumps({"
        "'version':list(bpy.app.version),"
        "'binary_path':bpy.app.binary_path,"
        "'user_root':bpy.utils.resource_path('USER'),"
        "'config_dir':bpy.utils.user_resource('CONFIG'),"
        "'scripts_dir':bpy.utils.user_resource('SCRIPTS'),"
        "'datafiles_dir':bpy.utils.user_resource('DATAFILES'),"
        "'extensions_dir':bpy.utils.user_resource('EXTENSIONS')"
        "},ensure_ascii=True))"
    )


def run_target_probe(target_executable: Path, timeout: int = 60) -> dict[str, Any]:
    executable = _resolved(target_executable)
    if not executable.is_file():
        raise MigrationError(f"目标 Blender 不存在：{executable}")
    expression = _build_probe_expression()
    command = [
        str(executable),
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "7",
        "--python-expr",
        expression,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            check=False,
            env=_blender_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise MigrationError("目标 Blender 探针启动超时") from exc
    combined = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        tail = "\n".join(combined.splitlines()[-3:])[:300]
        raise MigrationError(
            f"目标 Blender 探针失败，退出码 {completed.returncode}：{tail}"
        )
    return parse_probe_output(combined)


def windows_executable_is_running(
    target_executable: Path,
    ignore_pids: Iterable[int] = (),
) -> bool:
    if os.name != "nt":
        return False
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process -Filter \"Name='blender.exe'\" | "
        "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationError("无法确认目标 Blender 是否正在运行") from exc
    if completed.returncode != 0:
        raise MigrationError("无法确认目标 Blender 是否正在运行")
    raw = completed.stdout.strip()
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MigrationError("目标 Blender 进程检查结果无效") from exc
    entries = data if isinstance(data, list) else [data]
    ignored = {int(pid) for pid in ignore_pids}
    expected = os.path.normcase(str(_resolved(target_executable)))
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        process_id = int(entry.get("ProcessId", -1))
        if process_id in ignored:
            continue
        if not entry.get("ExecutablePath"):
            raise MigrationError(
                "存在无法确认路径的 Blender 进程，请关闭其他 Blender 后重试"
            )
        actual = os.path.normcase(str(_resolved(entry["ExecutablePath"])))
        if actual == expected:
            return True
    return False


def run_target_audit(
    target_executable: Path,
    worker_script: Path,
    manifest_path: Path,
    keymap_fingerprint_path: Path,
    report_path: Path,
    expected_user_root: Path,
    timeout: int = 300,
) -> dict[str, Any]:
    command = [
        str(target_executable),
        "--background",
        "--offline-mode",
        "--python-exit-code",
        "7",
        "--python",
        str(worker_script),
        "--",
        str(manifest_path),
        str(keymap_fingerprint_path),
        str(report_path),
        str(expected_user_root),
    ]
    environment = _blender_subprocess_env()
    environment["MMY_MIGRATION_AUDIT"] = "1"
    environment["MMY_MIGRATION_EXPECTED_ROOT"] = str(expected_user_root)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
            creationflags=creationflags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MigrationError(f"目标 Blender 验证超过 {timeout} 秒，已中止") from exc

    log_path = report_path.with_name("target_blender.log")
    log_path.write_text(
        f"STDOUT\n{completed.stdout}\n\nSTDERR\n{completed.stderr}",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise MigrationError(f"目标 Blender 验证失败，退出码 {completed.returncode}")
    if not report_path.is_file():
        raise MigrationError("目标 Blender 未生成迁移报告")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"目标 Blender 迁移报告无效：{exc}") from exc
    if report.get("status") not in {"success", "degraded"}:
        raise MigrationError(report.get("error") or "目标 Blender 验证未通过")
    return report


def _write_recovery_metadata(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(_json_dump(data), encoding="utf-8")
    os.replace(temp, path)


def _install_profile_for_target(
    profile: ProfileResult,
    source_version: Iterable[int],
    target_executable: Path,
    output_dir: Path,
    forbidden_roots: Iterable[Path],
    worker_script: Path,
    current_pid: int,
    audit_timeout: int,
) -> MigrationResult:
    """对单个目标执行探针校验 + 安装，供单目标与多目标流程复用。"""
    if os.name == "nt" and windows_executable_is_running(target_executable, [current_pid]):
        raise MigrationError(f"目标 Blender 正在运行，请关闭后重试：{target_executable}")
    probe = run_target_probe(target_executable)
    target_version = validate_forward_version(source_version, probe["version"])
    target_root = _resolved(probe["user_root"])
    validate_target_resource_layout(probe, target_root)
    for forbidden in forbidden_roots:
        if target_root == _resolved(forbidden):
            raise MigrationError("来源与目标 Blender 正在使用同一用户目录，不能执行迁移")
    if path_is_within(profile.path, target_root):
        raise MigrationError("配置包不能存放在即将被替换的目标用户目录内")
    output = ensure_external_output(
        Path(output_dir), [*_resolved_roots(forbidden_roots), target_root]
    )
    return _install_profile(
        profile=profile,
        source_version=source_version,
        target_version=target_version,
        target_root=target_root,
        target_executable=target_executable,
        output=output,
        worker_script=worker_script,
        current_pid=current_pid,
        audit_timeout=audit_timeout,
    )


def _resolved_roots(roots: Iterable[Path]) -> list[Path]:
    return [_resolved(root) for root in roots]


def execute_migration_multi(
    snapshot: SourceSnapshot,
    target_executables: Iterable[Path],
    output_dir: Path,
    worker_script: Path,
    current_pid: int,
    audit_timeout: int = 300,
) -> tuple[ProfileResult, list[TargetMigrationOutcome]]:
    """一份快照迁移到多个目标：先建一次 Profile，再逐目标独立安装。

    单目标失败不影响其他目标；逐目标结果通过 TargetMigrationOutcome 返回。
    """
    targets = [Path(target) for target in target_executables]
    if not targets:
        raise MigrationError("未选择任何目标 Blender")
    output = ensure_external_output(Path(output_dir), [snapshot.user_root])
    profile = create_profile(snapshot, output)
    outcomes: list[TargetMigrationOutcome] = []
    for target in targets:
        try:
            result = _install_profile_for_target(
                profile=profile,
                source_version=snapshot.version,
                target_executable=target,
                output_dir=output,
                forbidden_roots=[snapshot.user_root],
                worker_script=worker_script,
                current_pid=current_pid,
                audit_timeout=audit_timeout,
            )
            outcomes.append(TargetMigrationOutcome(target_executable=target, result=result))
        except Exception as exc:
            outcomes.append(
                TargetMigrationOutcome(target_executable=target, error=str(exc), exception=exc)
            )
    return profile, outcomes


def execute_migration(
    snapshot: SourceSnapshot,
    target_executable: Path,
    output_dir: Path,
    worker_script: Path,
    current_pid: int,
    audit_timeout: int = 300,
) -> MigrationResult:
    profile, outcomes = execute_migration_multi(
        snapshot,
        [target_executable],
        output_dir,
        worker_script,
        current_pid,
        audit_timeout=audit_timeout,
    )
    outcome = outcomes[0]
    if outcome.result is not None:
        return outcome.result
    if isinstance(outcome.exception, MigrationError):
        raise outcome.exception
    raise MigrationError(outcome.error or "迁移失败")


def execute_existing_profile_migration_multi(
    profile_path: Path,
    target_executables: Iterable[Path],
    output_dir: Path,
    current_user_root: Path,
    worker_script: Path,
    current_pid: int,
    audit_timeout: int = 300,
) -> tuple[ProfileResult, list[TargetMigrationOutcome]]:
    """已有配置包迁移到多个目标，逐目标独立安装。"""
    targets = [Path(target) for target in target_executables]
    if not targets:
        raise MigrationError("未选择任何目标 Blender")
    manifest = read_profile_manifest(profile_path)
    if manifest.get("source", {}).get("platform") != "win32":
        raise MigrationError("跨版本一键迁移第一版只接受 Windows 配置快照")
    source_version = normalize_version(
        manifest.get("source", {}).get("blender_version", [])
    )
    output = ensure_external_output(Path(output_dir), [current_user_root])
    profile = ProfileResult(
        path=_resolved(profile_path),
        manifest=manifest,
        warnings=list(manifest.get("warnings", [])),
    )
    outcomes: list[TargetMigrationOutcome] = []
    for target in targets:
        try:
            result = _install_profile_for_target(
                profile=profile,
                source_version=source_version,
                target_executable=target,
                output_dir=output,
                forbidden_roots=[current_user_root],
                worker_script=worker_script,
                current_pid=current_pid,
                audit_timeout=audit_timeout,
            )
            outcomes.append(TargetMigrationOutcome(target_executable=target, result=result))
        except Exception as exc:
            outcomes.append(
                TargetMigrationOutcome(target_executable=target, error=str(exc), exception=exc)
            )
    return profile, outcomes


def execute_existing_profile_migration(
    profile_path: Path,
    target_executable: Path,
    output_dir: Path,
    current_user_root: Path,
    worker_script: Path,
    current_pid: int,
    audit_timeout: int = 300,
) -> MigrationResult:
    profile, outcomes = execute_existing_profile_migration_multi(
        profile_path,
        [target_executable],
        output_dir,
        current_user_root,
        worker_script,
        current_pid,
        audit_timeout=audit_timeout,
    )
    outcome = outcomes[0]
    if outcome.result is not None:
        return outcome.result
    if isinstance(outcome.exception, MigrationError):
        raise outcome.exception
    raise MigrationError(outcome.error or "迁移失败")


def _install_profile(
    profile: ProfileResult,
    source_version: Iterable[int],
    target_version: tuple[int, int, int],
    target_root: Path,
    target_executable: Path,
    output: Path,
    worker_script: Path,
    current_pid: int,
    audit_timeout: int,
) -> MigrationResult:
    source_version = normalize_version(source_version)

    payload_size = sum(
        int(item["size"])
        for item in profile.manifest["files"]
        if item["path"].startswith("payload/")
    )
    target_size = directory_size(target_root)
    ensure_free_space(target_root.parent, int(payload_size * 1.15) + MIN_FREE_SPACE_BYTES)
    ensure_free_space(output, int(target_size * 1.1) + MIN_FREE_SPACE_BYTES)

    run_id = _run_id()
    recovery_dir = output / RECOVERY_DIR_NAME / (
        f"{version_string(source_version)}_to_{version_string(target_version)}_{run_id}"
    )
    recovery_dir.mkdir(parents=True, exist_ok=False)
    recovery_path = recovery_dir / "recovery.json"
    backup = create_directory_backup(target_root, recovery_dir / "target_before.zip")
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "preparing",
        "source_version": list(source_version),
        "target_version": list(target_version),
        "target_executable": str(_resolved(target_executable)),
        "target_root": str(target_root),
        "target_existed": target_root.exists(),
        "profile_path": str(profile.path),
        "backup": backup,
    }
    _write_recovery_metadata(recovery_path, metadata)

    staging_root = target_root.parent / f".{target_root.name}.mmy_stage_{run_id}"
    fallback_root = recovery_dir / "fallback"
    old_root: Path | None = None
    swapped = False
    try:
        manifest = extract_profile(profile.path, staging_root, fallback_root)
        manifest_path = recovery_dir / "source_manifest.json"
        manifest_path.write_text(_json_dump(manifest), encoding="utf-8")
        if os.name == "nt" and windows_executable_is_running(
            target_executable,
            [current_pid],
        ):
            raise MigrationError("目标 Blender 在迁移准备期间被打开，请关闭后重试")
        old_root = atomic_install(staging_root, target_root, run_id)
        swapped = True
        metadata["status"] = "validating"
        _write_recovery_metadata(recovery_path, metadata)

        report_path = recovery_dir / "migration_report.json"
        report = run_target_audit(
            _resolved(target_executable),
            worker_script,
            manifest_path,
            fallback_root / "keymap_fingerprint.json",
            report_path,
            target_root,
            timeout=audit_timeout,
        )
        finalize_atomic_install(old_root)
        metadata["status"] = report["status"]
        metadata["completed_at"] = datetime.now().isoformat(timespec="seconds")
        metadata["report_path"] = str(report_path)
        _write_recovery_metadata(recovery_path, metadata)
        html_report_path = report_path.with_suffix(".html")
        try:
            write_migration_report_html(report, metadata, html_report_path)
            metadata["report_html_path"] = str(html_report_path)
            _write_recovery_metadata(recovery_path, metadata)
        except Exception as html_exc:
            print(f"[MMY Migration] HTML 报告生成失败（不影响迁移）: {html_exc}")
            html_report_path = None
        return MigrationResult(
            status=report["status"],
            profile_path=profile.path,
            recovery_dir=recovery_dir,
            report_path=report_path,
            target_version=target_version,
            disabled_addons=list(report.get("disabled_addons", [])),
            warnings=list(profile.warnings) + list(report.get("warnings", [])),
            report_html_path=html_report_path,
            keymap_lost_count=int(
                (report.get("keymap") or {}).get("lost_count", 0)
            ),
        )
    except Exception as exc:
        failed_root = None
        if swapped:
            try:
                failed_root = rollback_atomic_install(target_root, old_root, run_id)
            except Exception as rollback_exc:
                metadata["rollback_error"] = str(rollback_exc)
        shutil.rmtree(staging_root, ignore_errors=True)
        metadata["status"] = "rolled_back" if swapped and "rollback_error" not in metadata else "failed"
        metadata["error"] = str(exc)
        if failed_root:
            metadata["failed_target_root"] = str(failed_root)
        _write_recovery_metadata(recovery_path, metadata)
        report_path = recovery_dir / "migration_report.json"
        if report_path.is_file():
            try:
                partial_report = json.loads(report_path.read_text(encoding="utf-8"))
                write_migration_report_html(
                    partial_report, metadata, report_path.with_suffix(".html")
                )
            except Exception:
                pass
        if isinstance(exc, MigrationError):
            if exc.recovery_dir is None:
                exc.recovery_dir = recovery_dir
            raise
        raise MigrationError(f"迁移失败：{exc}", recovery_dir=recovery_dir) from exc


def restore_recovery(recovery_file: Path, current_pid: int) -> dict[str, Any]:
    try:
        metadata = json.loads(recovery_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"恢复记录无效：{exc}") from exc
    if metadata.get("schema_version") != 1:
        raise MigrationError("不支持的恢复记录版本")

    target_executable = _resolved(metadata["target_executable"])
    target_root = _resolved(metadata["target_root"])
    if os.name == "nt":
        if windows_executable_is_running(target_executable, [current_pid]):
            raise MigrationError("目标 Blender 正在运行，请关闭后重试")
        probe = run_target_probe(target_executable)
        probed_root = _resolved(probe["user_root"])
        validate_target_resource_layout(probe, probed_root)
        if probed_root != target_root:
            raise MigrationError(
                "恢复记录中的目标目录与目标 Blender 实际用户目录不一致"
            )
    backup = metadata.get("backup") or {}
    backup_path = _resolved(backup.get("path", ""))
    if not path_is_within(backup_path, recovery_file.parent):
        raise MigrationError("恢复包必须位于 recovery.json 所在目录内")
    if not backup_path.is_file() or sha256_file(backup_path) != backup.get("sha256"):
        raise MigrationError("迁移前恢复包不存在或校验失败")

    run_id = _run_id()
    recovery_dir = recovery_file.parent
    current_backup = create_directory_backup(
        target_root,
        recovery_dir / f"target_before_manual_restore_{run_id}.zip",
    )
    staging_root = target_root.parent / f".{target_root.name}.mmy_restore_{run_id}"

    if metadata.get("target_existed"):
        extract_directory_backup(backup_path, list(backup.get("files", [])), staging_root)
        old_root = atomic_install(staging_root, target_root, f"restore_{run_id}")
        finalize_atomic_install(old_root)
    else:
        removed_root = target_root.parent / f".{target_root.name}.mmy_removed_{run_id}"
        if target_root.exists():
            os.replace(target_root, removed_root)
            shutil.rmtree(removed_root)

    result = {
        "status": "success",
        "restored_at": datetime.now().isoformat(timespec="seconds"),
        "target_root": str(target_root),
        "current_backup": current_backup,
    }
    (recovery_dir / f"restore_report_{run_id}.json").write_text(
        _json_dump(result), encoding="utf-8"
    )
    return result


def find_blender_executables(current_executable: Path) -> list[Path]:
    """扫描常见安装目录，返回全部候选 blender.exe（按目录版本号降序）。"""
    if os.name != "nt":
        return []
    candidates: set[Path] = set()
    roots = [Path(current_executable).parent.parent]
    program_files = os.environ.get("ProgramFiles")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if program_files:
        roots.append(Path(program_files) / "Blender Foundation")
    if local_app_data:
        roots.append(Path(local_app_data) / "Programs" / "Blender Foundation")
    for root in roots:
        if not str(root) or not root.exists():
            continue
        direct = root / "blender.exe"
        if direct.is_file():
            candidates.add(direct)
        candidates.update(root.glob("*/blender.exe"))
    current = _resolved(current_executable)
    choices = [item for item in candidates if _resolved(item) != current]

    def sort_key(path: Path) -> tuple[int, ...]:
        numbers = re.findall(r"\d+", str(path.parent))
        return tuple(int(value) for value in numbers[-3:])

    choices.sort(key=sort_key, reverse=True)
    return choices


def suggest_blender_executable(current_executable: Path, remembered: str = "") -> str:
    if remembered and Path(remembered).is_file():
        return remembered
    choices = find_blender_executables(current_executable)
    return str(choices[0]) if choices else ""


# ============================================================
# 插件兼容性预测（迁移预检用）
# ============================================================

def _version_triplet(value) -> tuple[int, int, int]:
    if isinstance(value, str):
        value = value.split(".")
    parts = []
    for part in list(value or [])[:3]:
        try:
            parts.append(int(part))
        except (TypeError, ValueError):
            parts.append(0)
    parts.extend([0] * (3 - len(parts)))
    return tuple(parts[:3])


def predict_addon_compatibility(
    addons: list[dict[str, Any]], target_version: Iterable[int]
) -> list[dict[str, str]]:
    """按插件声明的 blender_version_min/max 预测与目标版本的兼容性。

    仅检查已启用插件；返回预计不兼容的条目列表。
    """
    target = _version_triplet(target_version)
    incompatible: list[dict[str, str]] = []
    for addon in addons or []:
        if not isinstance(addon, dict) or not addon.get("enabled"):
            continue
        module = str(addon.get("module") or "")
        if not module:
            continue
        minimum = addon.get("blender_version_min")
        maximum = addon.get("blender_version_max")
        reason = ""
        if minimum and target < _version_triplet(minimum):
            reason = f"声明要求 Blender >= {minimum}"
        elif maximum and target > _version_triplet(maximum):
            reason = f"声明仅支持 Blender <= {maximum}"
        if reason:
            incompatible.append(
                {
                    "module": module,
                    "kind": str(addon.get("kind", "legacy")),
                    "reason": reason,
                }
            )
    return incompatible


# ============================================================
# 迁移 HTML 报告（M3）
# ============================================================

def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _report_html_status_banner(status: str) -> tuple[str, str, str]:
    mapping = {
        "success": ("#15803d", "#ecfdf3", "✅ 迁移成功"),
        "degraded": ("#b45309", "#fffbeb", "⚠️ 迁移完成（降级：部分内容未通过验证）"),
        "rolled_back": ("#b45309", "#fffbeb", "↩️ 迁移失败，已自动回滚"),
        "failed": ("#b91c1c", "#fef2f2", "❌ 迁移失败"),
    }
    return mapping.get(status, ("#4e5560", "#f0f2f5", f"状态：{status}"))


def build_migration_report_html(
    report: dict[str, Any], metadata: dict[str, Any]
) -> str:
    """根据目标审计报告 + recovery 元数据生成 HTML 报告文本。"""
    status = str(report.get("status") or metadata.get("status") or "unknown")
    color, bg, banner = _report_html_status_banner(status)
    source_version = version_string(report.get("source_version") or metadata.get("source_version") or [])
    target_version = version_string(report.get("target_version") or metadata.get("target_version") or [])
    target_root = report.get("target_user_root") or metadata.get("target_root", "")
    created_at = metadata.get("created_at", "")
    completed_at = metadata.get("completed_at") or report.get("audited_at", "")

    disabled = report.get("disabled_addons") or []
    keymap = report.get("keymap") or {}
    missing_paths = report.get("missing_paths") or []
    warnings = list(report.get("warnings") or [])
    error_text = report.get("error") or metadata.get("error") or ""

    rows_disabled = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _esc(item.get("module", "")),
            _esc(item.get("kind", "")),
            _esc(item.get("reason", "")),
            "已禁用" if item.get("disabled") else _esc(item.get("disable_error", "禁用失败")),
        )
        for item in disabled
    )
    rows_paths = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _esc(item.get("owner", "")),
            _esc(item.get("property", "")),
            _esc(item.get("path", "")),
        )
        for item in missing_paths
    )
    items_warnings = "".join(f"<li>{_esc(item)}</li>" for item in warnings)

    keymap_html = ""
    if keymap:
        if keymap.get("skipped"):
            keymap_html = f"""
  <h2>快捷键指纹比对</h2>
  <p>已跳过（{_esc(keymap.get('skipped'))}）：源环境默认键位未物化，无法可靠求差。</p>"""
        else:
            keymap_html = f"""
  <h2>快捷键指纹比对（用户差异项）</h2>
  <table>
    <tr><th>源差异项</th><th>匹配</th><th>丢失</th><th>后台不可验证</th></tr>
    <tr><td>{_esc(keymap.get('source_count', 0))}</td><td>{_esc(keymap.get('matched_count', 0))}</td><td>{_esc(keymap.get('lost_count', 0))}</td><td>{_esc(keymap.get('unverifiable_count', 0))}</td></tr>
  </table>
  <p class="muted">默认键位由目标版本自身提供，不参与比对；"修改默认"类差异依赖目标版本键位结构，后台审计无法可靠验证，打开目标 Blender（GUI）后生效。</p>"""
            lost_items = keymap.get("lost") or []
            if lost_items:
                rows_lost = "".join(
                    "<tr><td>{}</td><td><code>{}</code></td></tr>".format(
                        _esc(item.get("keymap", "")), _esc(item.get("idname", ""))
                    )
                    for item in lost_items
                )
                keymap_html += f"""
  <p>以下用户新增快捷键在目标版本中丢失：</p>
  <table>
    <tr><th>键位映射</th><th>操作</th></tr>
    {rows_lost}
  </table>"""
            orphan = keymap.get("orphan_operators") or []
            if orphan:
                keymap_html += (
                    "\n  <p>以下快捷键指向的操作在目标版本不存在（插件未注册或已被版本移除）：</p><ul>"
                    + "".join(f"<li><code>{_esc(op)}</code></li>" for op in orphan)
                    + "</ul>"
                )

    disabled_html = ""
    if disabled:
        disabled_html = f"""
  <h2>被禁用的不兼容插件（{len(disabled)}）</h2>
  <table>
    <tr><th>模块</th><th>类型</th><th>原因</th><th>处理</th></tr>
    {rows_disabled}
  </table>"""

    paths_html = ""
    if missing_paths:
        paths_html = f"""
  <h2>失效绝对路径（{len(missing_paths)}）</h2>
  <table>
    <tr><th>所属</th><th>属性</th><th>路径</th></tr>
    {rows_paths}
  </table>"""

    warnings_html = ""
    if warnings:
        warnings_html = f"""
  <h2>警告（{len(warnings)}）</h2>
  <ul>{items_warnings}</ul>"""

    error_html = ""
    if error_text:
        error_html = f"""
  <h2>错误信息</h2>
  <pre>{_esc(error_text)}</pre>"""

    guide_html = f"""
  <h2>恢复指引</h2>
  <p>如需回到迁移前状态：打开 Blender 顶部菜单「配置管理 → 备份记录」，找到本次迁移对应的
  <b>{_esc(source_version)} → {_esc(target_version)}</b> 条目，点击【恢复】。
  恢复记录文件位于本报告同目录的 <code>recovery.json</code>。</p>
  <p>回到旧版本 = 恢复该版本自己的备份；请勿将高版本配置直接拷回旧版本使用。</p>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>MMY 迁移报告 {source_version} → {target_version}</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 0; background: #f6f7f9; color: #1f2329; line-height: 1.7; }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 28px 22px 60px; }}
  .banner {{ background: {bg}; color: {color}; border: 1px solid {color}; border-radius: 10px; padding: 14px 20px; font-size: 17px; font-weight: 600; }}
  h1 {{ font-size: 21px; margin: 18px 0 4px; }}
  h2 {{ font-size: 16px; margin: 26px 0 8px; border-left: 4px solid #2563eb; padding-left: 10px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; }}
  th, td {{ border: 1px solid #e3e6ea; padding: 7px 10px; text-align: left; word-break: break-all; }}
  th {{ background: #f0f2f5; }}
  code {{ background: #eef1f4; padding: 1px 6px; border-radius: 4px; font-size: 12px; }}
  pre {{ background: #0f172a; color: #e2e8f0; padding: 14px; border-radius: 8px; overflow-x: auto; font-size: 12.5px; }}
  .meta {{ color: #4e5560; font-size: 13.5px; }}
  .muted {{ color: #6b7280; font-size: 12.5px; }}
  .card {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 10px; padding: 14px 20px; margin-top: 14px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="banner">{_esc(banner)}</div>
  <h1>MMY 跨版本迁移报告</h1>
  <p class="meta">Blender {_esc(source_version)} → {_esc(target_version)} ｜ 开始：{_esc(created_at)} ｜ 完成：{_esc(completed_at)}</p>
  <p class="meta">目标用户目录：<code>{_esc(target_root)}</code></p>
  <div class="card">
    {error_html}
    {disabled_html if disabled_html else '<h2>插件检查</h2><p>✅ 无需禁用的插件</p>' if status in {'success', 'degraded'} else ''}
    {keymap_html}
    {paths_html}
    {warnings_html}
    {guide_html}
  </div>
  <p class="meta" style="margin-top:24px">由 MMY Blender Configure 生成 ｜ 同目录 migration_report.json 为机器可读版本</p>
</div>
</body>
</html>
"""


def write_migration_report_html(
    report: dict[str, Any], metadata: dict[str, Any], html_path: Path
) -> Path:
    html_path = Path(html_path)
    html_path.write_text(
        build_migration_report_html(report, metadata), encoding="utf-8"
    )
    return html_path


# ============================================================
# 备份历史扫描（B2）
# ============================================================

def _parse_backup_zip_name(name: str) -> tuple[str, str] | None:
    """从备份文件名解析 (类型, 版本)。无法识别返回 None。"""
    match = re.match(
        r"^(MMY_Backup_Portable|MMY_Backup_Profile|Blender_Portable|MMY_Blender_Profile)"
        r"_v(\d+\.\d+(?:\.\d+)?)",
        name,
    )
    if not match:
        return None
    prefix, version = match.group(1), match.group(2)
    backup_type = (
        "portable" if "Portable" in prefix else "profile"
    )
    return backup_type, version


def list_backup_entries(output_dir: Path) -> list[dict[str, Any]]:
    """扫描备份输出目录，返回全部备份与迁移恢复条目（按时间倒序）。

    条目字段：type(portable/profile/recovery), name, path, version_label,
    created(epoch), size, status(仅 recovery), detail。
    """
    output = Path(output_dir)
    entries: list[dict[str, Any]] = []
    if not output.is_dir():
        return entries

    for item in output.glob("*.zip"):
        parsed = _parse_backup_zip_name(item.name)
        if not parsed:
            continue
        backup_type, version = parsed
        try:
            stat_result = item.stat()
        except OSError:
            continue
        detail = ""
        if backup_type == "portable":
            manifest = read_portable_backup_manifest(item)
            if manifest:
                machine = manifest.get("machine", "")
                detail = f"{manifest.get('file_count', '?')} 个文件" + (
                    f" ｜ {machine}" if machine else ""
                )
        entries.append(
            {
                "type": backup_type,
                "name": item.name,
                "path": str(item),
                "version_label": f"v{version}",
                "created": stat_result.st_mtime,
                "size": stat_result.st_size,
                "status": "",
                "detail": detail,
            }
        )

    recovery_root = output / RECOVERY_DIR_NAME
    if recovery_root.is_dir():
        for run_dir in sorted(recovery_root.iterdir()):
            if not run_dir.is_dir():
                continue
            recovery_file = run_dir / "recovery.json"
            if not recovery_file.is_file():
                continue
            try:
                metadata = json.loads(recovery_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            source_v = version_string(metadata.get("source_version", []))
            target_v = version_string(metadata.get("target_version", []))
            backup_info = metadata.get("backup") or {}
            backup_path = Path(backup_info.get("path", recovery_file))
            try:
                size = backup_path.stat().st_size if backup_path.is_file() else 0
                created = recovery_file.stat().st_mtime
            except OSError:
                size, created = 0, recovery_file.stat().st_mtime
            entries.append(
                {
                    "type": "recovery",
                    "name": run_dir.name,
                    "path": str(recovery_file),
                    "version_label": f"{source_v} → {target_v}",
                    "created": created,
                    "size": size,
                    "status": str(metadata.get("status", "")),
                    "detail": "迁移前目标备份",
                }
            )

    entries.sort(key=lambda entry: entry["created"], reverse=True)
    return entries


def read_portable_backup_manifest(zip_path: Path) -> dict[str, Any]:
    """读取 Portable 备份 zip 内的 manifest.json；不存在或损坏返回 {}。"""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if "manifest.json" not in names:
                return {}
            data = json.loads(zf.read("manifest.json").decode("utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError):
        return {}


def extract_portable_backup(zip_path: Path, dest_dir: Path) -> int:
    """安全解压 Portable 备份到指定目录，返回文件数。"""
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if info.filename == "manifest.json":
                continue
            relative = _safe_archive_path(info.filename)
            if _zipinfo_is_symlink(info):
                raise MigrationError(f"备份包含符号链接：{info.filename}")
            target = dest_dir.joinpath(*relative.parts)
            if not path_is_within(target, dest_dir):
                raise MigrationError(f"备份目标路径越界：{info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
            count += 1
    return count


# ============================================================
# 迁移残留清理（P2）
# ============================================================

def cleanup_stale_migration_artifacts(
    parents: Iterable[Path], max_age_hours: float = 24.0
) -> list[str]:
    """清理给定目录下超过 max_age_hours 小时的迁移事务残留目录。

    匹配 .{name}.mmy_old_/stage_/failed_/restore_/removed_ 形式的隐藏目录。
    返回已删除的路径列表；删除失败（占用等）静默跳过。
    """
    removed: list[str] = []
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    seen: set[str] = set()
    for parent in parents or []:
        parent = Path(parent)
        if not parent.is_dir():
            continue
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            if not _STALE_ARTIFACT_PATTERN.match(child.name):
                continue
            key = str(_resolved(child))
            if key in seen:
                continue
            seen.add(key)
            try:
                age = now - child.stat().st_mtime
            except OSError:
                continue
            if age < max_age_seconds:
                continue
            try:
                shutil.rmtree(child, ignore_errors=False)
                removed.append(str(child))
            except OSError:
                continue
    return removed
