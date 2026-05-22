import bpy
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path


def get_config_dir() -> Path:
    return Path(bpy.utils.user_resource('CONFIG'))


def get_addons_dir() -> Path:
    return Path(bpy.utils.user_resource('SCRIPTS')) / "addons"


def is_portable_mode() -> bool:
    """检测 Blender 是否运行在 portable 模式。"""
    blender_dir = Path(bpy.app.binary_path).parent
    portable_dir = blender_dir / "portable"

    if not portable_dir.exists():
        return False

    config_dir = Path(bpy.utils.user_resource('CONFIG'))
    try:
        parts = config_dir.parts
        if 'portable' in parts:
            return True
    except:
        pass

    return False


def get_portable_base_dir() -> Path | None:
    """获取 portable 模式的基目录（Blender 安装目录）。"""
    if not is_portable_mode():
        return None
    return Path(bpy.app.binary_path).parent


def detect_path_dependencies() -> list[str]:
    """检测当前配置中的路径依赖项。"""
    dependencies = []

    # 1. 检查书签文件
    bookmarks_file = Path(bpy.utils.user_resource('CONFIG')) / 'bookmarks.txt'
    if bookmarks_file.exists():
        try:
            content = bookmarks_file.read_text(encoding='utf-8')
            if any(line.startswith('/') or ':' in line for line in content.splitlines() if line.strip()):
                dependencies.append('bookmarks')
        except:
            pass

    # 2. 检查资产库路径
    prefs = bpy.context.preferences
    if prefs.filepaths.asset_libraries:
        for lib in prefs.filepaths.asset_libraries:
            if lib.path and not lib.path.startswith('//'):
                dependencies.append('asset_library')
                break

    return dependencies


def check_mode_compatibility(manifest: dict) -> dict | None:
    """检查导入时模式兼容性。"""
    current_portable = is_portable_mode()
    backup_portable = manifest.get("portable_mode", False)

    if current_portable == backup_portable:
        # 同为 portable 模式，但基路径不同时也需提示
        if current_portable:
            backup_base = manifest.get("portable_base_dir", "")
            current_base = str(get_portable_base_dir())
            if backup_base and backup_base != current_base:
                return {
                    "type": "portable_path_change",
                    "title": "Portable 基路径变化",
                    "message": f"备份来自 {backup_base}，当前为 {current_base}",
                    "details": ["路径依赖项（书签、资产库）可能需要手动调整"]
                }
        return None

    # 模式不一致
    if backup_portable and not current_portable:
        return {
            "type": "portable_to_normal",
            "title": "Portable → 普通模式迁移",
            "message": "备份来自 Portable 模式，当前为普通模式",
            "details": [
                f"配置将导入到：{bpy.utils.user_resource('CONFIG')}",
                "路径依赖项（书签、资产库）可能需要手动调整"
            ]
        }
    else:
        portable_base = get_portable_base_dir()
        target_path = f"{portable_base}/portable/{bpy.app.version[0]}.{bpy.app.version[1]}/config" if portable_base else ""
        return {
            "type": "normal_to_portable",
            "title": "普通 → Portable 模式迁移",
            "message": "备份来自普通模式，当前为 Portable 模式",
            "details": [
                f"配置将导入到：{target_path}",
                "路径依赖项（书签、资产库）可能需要手动调整"
            ]
        }


def _dir_sources():
    """返回目录勾选项 -> (源目录路径, zip内路径) 的映射。"""
    return {
        "config":       (get_config_dir(), "config"),
        "presets":      (Path(bpy.utils.user_resource('SCRIPTS')) / "presets", "scripts/presets"),
        "startup":      (Path(bpy.utils.user_resource('SCRIPTS')) / "startup", "scripts/startup"),
        "datafiles":    (Path(bpy.utils.user_resource('DATAFILES')), "datafiles"),
        "studiolights": (Path(bpy.utils.user_resource('STUDIO_LIGHTS')), "studiolights"),
        "extensions":   (Path(bpy.utils.user_resource('EXTENSIONS')), "extensions"),
    }


def _pack_dir(zf: zipfile.ZipFile, src_dir: Path, arc_prefix: str):
    """将目录递归写入 zip。"""
    if not src_dir.exists():
        return
    for item in src_dir.rglob("*"):
        if item.is_file():
            zf.write(item, f"{arc_prefix}/{item.relative_to(src_dir)}")


def pack_config(
    dest: Path,
    include_keymap: bool, include_prefs: bool,
    include_addons: bool,
    include_config: bool, include_presets: bool,
    include_startup: bool, include_datafiles: bool,
) -> Path:
    bpy.ops.wm.save_userpref()
    config_dir = get_config_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = dest / f"mmy_config_{timestamp}.zip"
    includes = []

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # userpref.blend
        if include_prefs or include_keymap:
            userpref = config_dir / "userpref.blend"
            if userpref.exists():
                zf.write(userpref, "userpref.blend")
                if include_prefs:
                    includes.append("prefs")
                if include_keymap:
                    includes.append("keymap")

        # addons
        if include_addons:
            addons_dir = get_addons_dir()
            if addons_dir.exists():
                _pack_dir(zf, addons_dir, "addons")
            includes.append("addons")

        # 新增目录
        sources = _dir_sources()
        if include_config:
            src, arc = sources["config"]
            _pack_dir(zf, src, arc)
            includes.append("config")
        if include_presets:
            src, arc = sources["presets"]
            _pack_dir(zf, src, arc)
            includes.append("presets")
        if include_startup:
            src, arc = sources["startup"]
            _pack_dir(zf, src, arc)
            includes.append("startup")
        if include_datafiles:
            for key in ("datafiles", "studiolights", "extensions"):
                src, arc = sources[key]
                _pack_dir(zf, src, arc)
            includes.append("datafiles")

        path_deps = detect_path_dependencies()
        manifest = {
            "blender_version": ".".join(str(v) for v in bpy.app.version),
            "blender_version_sub": bpy.app.version[2],
            "created_at": datetime.now().isoformat(),
            "portable_mode": is_portable_mode(),
            "portable_base_dir": str(get_portable_base_dir()) if is_portable_mode() else "",
            "source_config_path": "portable/" + ".".join(str(v) for v in bpy.app.version[:2]) + "/config" if is_portable_mode() else "",
            "includes": includes,
            "has_path_dependencies": len(path_deps) > 0,
            "path_dependency_types": path_deps,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return zip_path


def _make_temp_backup(config_dir: Path) -> Path:
    """对现有配置目录做临时备份，返回备份 zip 路径。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = config_dir / f".mmy_temp_backup_{timestamp}.zip"
    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in config_dir.rglob("*"):
            if item.is_file() and item != backup_path:
                zf.write(item, item.relative_to(config_dir))
    return backup_path


def unpack_config(
    zip_path: Path,
    include_keymap: bool, include_prefs: bool,
    include_addons: bool,
    include_config: bool, include_presets: bool,
    include_startup: bool, include_datafiles: bool,
):
    if not zip_path.exists():
        raise FileNotFoundError(f"备份文件不存在：{zip_path}")

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            bad = zf.testzip()
            if bad is not None:
                raise zipfile.BadZipFile(f"备份文件损坏：{bad}")
    except zipfile.BadZipFile:
        raise
    except Exception:
        raise ValueError(f"无效的 zip 文件：{zip_path}")

    with zipfile.ZipFile(zip_path, 'r') as zf:
        config_dir = get_config_dir()
        addons_dir = get_addons_dir()
        names = zf.namelist()

        if "manifest.json" not in names:
            raise ValueError("备份文件格式无效：缺少 manifest.json")

        manifest = json.loads(zf.read("manifest.json"))
        includes_in_backup = manifest.get("includes", [])

        # 导入前先做临时备份
        _make_temp_backup(config_dir)

        # userpref.blend
        if (include_prefs or include_keymap) and "userpref.blend" in names:
            zf.extract("userpref.blend", config_dir)

        # addons
        if include_addons and any(n.startswith("addons/") for n in names):
            for name in names:
                if name.startswith("addons/") and not name.endswith("/"):
                    target = addons_dir / name[len("addons/"):]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(name))

        # 新增目录还原
        sources = _dir_sources()
        if include_config and "config" in includes_in_backup:
            _extract_dir_from_zip(zf, names, sources["config"][1], sources["config"][0])
        if include_presets and "presets" in includes_in_backup:
            _extract_dir_from_zip(zf, names, sources["presets"][1], sources["presets"][0])
        if include_startup and "startup" in includes_in_backup:
            _extract_dir_from_zip(zf, names, sources["startup"][1], sources["startup"][0])
        if include_datafiles and "datafiles" in includes_in_backup:
            for key in ("datafiles", "studiolights", "extensions"):
                src, target = sources[key]
                _extract_dir_from_zip(zf, names, src, target)

    return manifest


def _extract_dir_from_zip(zf: zipfile.ZipFile, names: list, arc_prefix: str, target_dir: Path):
    """从 zip 中提取特定前缀的文件到目标目录。"""
    for name in names:
        if name.startswith(f"{arc_prefix}/") and not name.endswith("/"):
            rel = name[len(arc_prefix) + 1:]
            target = target_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
