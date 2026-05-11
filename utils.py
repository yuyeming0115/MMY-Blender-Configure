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

        manifest = {
            "blender_version": ".".join(str(v) for v in bpy.app.version),
            "created_at": datetime.now().isoformat(),
            "includes": includes,
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
    config_dir = get_config_dir()
    addons_dir = get_addons_dir()

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
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
