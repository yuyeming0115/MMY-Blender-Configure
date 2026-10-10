#!/usr/bin/env python3
"""
MMY Blender Configure — 独立迁移工具（免插件安装）

把插件"一键导出"产生的迁移配置包应用到任意目标 Blender——
目标 Blender **不需要安装本插件**，适用于全新便携版 / 新机器。

用法：
  python migrate.py                              # 图形模式（tkinter 双选框）
  python migrate.py <profile.zip> <blender.exe>  # 命令行模式
  双击 run_migrate.bat                           # 图形模式（自动挑选带 tkinter 的解释器）

原理：迁移执行链（migration_core）是纯 Python，通过
blender.exe --background --python migration_worker.py 注入审计脚本，
不依赖插件注册成功。本脚本只是这条链路的独立触发器。
"""

import json
import os
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ADDON_DIR = REPO_ROOT / "mmy_pack_config"
WORKER_SCRIPT = ADDON_DIR / "migration_worker.py"
EXPORT_WORKER_SCRIPT = ADDON_DIR / "export_worker.py"

# 与 migration_core.SCHEMA_VERSION 对齐的配置包格式版本
PROFILE_SCHEMA_VERSION = 2


class MigrateInputError(Exception):
    """配置包输入校验失败（面向用户的中文错误）。"""


def load_migration_core():
    """加载纯 Python 迁移核心（不 import bpy，无需 Blender 环境）。"""
    if not WORKER_SCRIPT.is_file():
        raise MigrateInputError(
            f"缺少迁移执行文件：{WORKER_SCRIPT}\n"
            "请从完整仓库运行 migrate.py（mmy_pack_config/ 必须随脚本一起）。"
        )
    sys.path.insert(0, str(ADDON_DIR))
    import migration_core  # noqa: PLC0415

    return migration_core


# ============================================================
# 输入校验与摘要（纯函数，可单测）
# ============================================================


def validate_profile(profile_path) -> dict:
    """校验迁移配置包，返回 manifest。失败抛 MigrateInputError。"""
    path = Path(profile_path)
    if not path.is_file():
        raise MigrateInputError(f"配置包不存在：{path}")
    if path.suffix.lower() != ".zip":
        raise MigrateInputError(f"配置包必须是 .zip 文件：{path.name}")

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names:
                raise MigrateInputError(
                    f"{path.name} 中没有 manifest.json，不是插件导出的迁移配置包。"
                )
            try:
                manifest = json.loads(
                    zf.read("manifest.json").decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MigrateInputError(f"manifest.json 解析失败：{exc}")
    except zipfile.BadZipFile:
        raise MigrateInputError(f"{path.name} 不是有效的 ZIP 文件。")

    schema = manifest.get("schema_version")
    if schema != PROFILE_SCHEMA_VERSION:
        if schema == 1 and manifest.get("type") == "portable":
            raise MigrateInputError(
                f"{path.name} 是 pack.py 生成的便携版整目录备份，不是迁移配置包。\n"
                "整目录备份请直接解压覆盖 portable/ 文件夹（适合同版本）；\n"
                "跨版本迁移请先在源 Blender 中用插件「一键导出」生成配置包。"
            )
        raise MigrateInputError(
            f"不支持的配置包格式（schema_version={schema!r}），"
            f"本工具需要 schema_version={PROFILE_SCHEMA_VERSION}。"
        )

    source = manifest.get("source") or {}
    if not source.get("blender_version"):
        raise MigrateInputError("配置包缺少 source.blender_version 字段。")
    if source.get("platform") != "win32":
        raise MigrateInputError("跨版本迁移第一版只接受 Windows 配置快照。")
    return manifest


def version_string(version) -> str:
    """[5, 1, 0] -> '5.1'（末位为 0 时省略，与插件内展示一致）。"""
    parts = [int(p) for p in list(version or [])[:3]]
    parts.extend([0] * (3 - len(parts)))
    if parts[2] == 0:
        return f"{parts[0]}.{parts[1]}"
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def detect_target_version(executable_path) -> str | None:
    """从 blender.exe 上级目录名猜测版本（如 Blender5.1 -> 5.1）。

    仅用于确认摘要展示；真实目标版本以迁移时的目标探针为准。
    """
    parent_name = Path(executable_path).parent.name
    match = re.search(r"(\d+\.\d+(\.\d+)?)", parent_name)
    return match.group(1) if match else None


def is_portable_user_root(user_root, executable_path) -> bool:
    """user_root 是否为目标 exe 同级的 portable 目录。

    这是 Blender 便携模式的官方判定条件：exe 所在目录存在名为 portable
    的文件夹时，配置/脚本/扩展才会写在该文件夹内；否则写系统用户目录
    （Windows: %APPDATA%\\Blender Foundation\\...）。全新解压的官方 zip
    默认没有 portable 目录，直接迁移会导致配置进不了便携目录。
    """
    if not user_root:
        return False
    return Path(str(user_root)) == Path(executable_path).parent / "portable"


def source_user_root(manifest: dict) -> str:
    """配置包记录的源用户目录（v1.4 及更早的包没有该字段，返回空串）。"""
    return str((manifest.get("source") or {}).get("user_root") or "")


# 组件 → 它对应的 portable 相对路径（用于把「包里没有」翻译成人话）
COMPONENT_LABELS: dict[str, str] = {
    "preferences": "偏好设置 userpref.blend",
    "startup_file": "启动文件 startup.blend",
    "addons": "传统插件 scripts/addons",
    "extensions": "扩展 extensions",
    "presets": "预设 scripts/presets",
    "datafiles": "数据文件 datafiles",
    "startup_scripts": "启动脚本 scripts/startup",
    "app_templates": "应用模板 bl_app_templates_user",
    "history": "书签与最近文件",
}

# 用户明确关心的「文件夹级」组件：缺失时必须告诉他，而不是静默少拷一份
FOLDER_COMPONENTS: tuple[str, ...] = (
    "datafiles",
    "startup_scripts",
    "presets",
)


def missing_folder_components(manifest: dict) -> list[str]:
    """返回配置包中缺失的文件夹级组件（中文可读）。

    历史版本的导出默认值不含 datafiles / startup_scripts，导致迁移后目标
    portable 目录少一截文件，而用户完全不知情。这里显式检测并上报。

    >>> missing_folder_components({"components": ["preferences", "datafiles"]})
    ['启动脚本 scripts/startup', '预设 scripts/presets']
    >>> missing_folder_components({"components": ["preferences"]})
    ['数据文件 datafiles', '启动脚本 scripts/startup', '预设 scripts/presets']
    >>> missing_folder_components({})
    ['数据文件 datafiles', '启动脚本 scripts/startup', '预设 scripts/presets']
    """
    present = set(manifest.get("components") or ())
    return [
        COMPONENT_LABELS[name]
        for name in FOLDER_COMPONENTS
        if name not in present
    ]


def preflight_target(manifest: dict, executable_path) -> dict:
    """迁移前用目标探针读出真实信息：版本、用户目录、便携模式、版本合法性。

    比"按目录名猜版本"可靠；版本策略（同主版本 + 向前-only）在此提前拦截，
    避免用户走完流程才失败。
    """
    core = load_migration_core()
    target = Path(executable_path)
    if not target.is_file():
        raise MigrateInputError(f"目标 Blender 不存在：{target}")
    if core.windows_executable_is_running(target, [os.getpid()]):
        raise MigrateInputError(f"目标 Blender 正在运行，请关闭后重试：{target}")

    probe = core.run_target_probe(target)
    user_root = str(probe.get("user_root") or "")
    version = core.normalize_version(probe.get("version") or [])
    source_version = (manifest.get("source") or {}).get("blender_version") or []
    try:
        core.validate_forward_version(source_version, version)
    except core.MigrationError as exc:
        raise MigrateInputError(str(exc))

    # 自迁移检测：配置包记录了源用户目录时，禁止把包导回它自己
    origin = source_user_root(manifest)
    if origin and Path(origin) == Path(user_root):
        raise MigrateInputError(
            "目标 Blender 就是配置包的来源，不能自己导入自己：\n"
            f"  {user_root}\n"
            "请选择另一台机器/另一个版本的 Blender。"
        )

    incompatible = core.predict_addon_compatibility(
        manifest.get("addons") or [], version
    )
    return {
        "version": version_string(version),
        "user_root": user_root,
        "portable": is_portable_user_root(user_root, target),
        "source_user_root": origin,
        "incompatible": incompatible,
    }


def describe_profile(
    manifest: dict,
    target_exe,
    target_version: str | None,
    preflight: dict | None = None,
) -> str:
    """生成确认弹窗用的人类可读摘要。"""
    source = manifest.get("source") or {}
    workspaces = manifest.get("workspaces") or {}
    addons = manifest.get("addons") or []
    enabled_addons = [a for a in addons if isinstance(a, dict) and a.get("enabled")]

    lines = [
        "请确认迁移信息：",
        "",
        f"  配置包版本：Blender {version_string(source.get('blender_version'))}",
        f"  目标 Blender：{target_exe}",
    ]
    if preflight:
        lines.append(f"  目标版本（实测）：Blender {preflight['version']}")
        lines.append(f"  目标配置目录：{preflight['user_root']}")
    elif target_version:
        lines.append(f"  目标版本（按目录名推测）：{target_version}")
    lines.append(f"  已启用插件：{len(enabled_addons)} 个（不兼容的将在导入时自动禁用）")
    if workspaces:
        lines.append(
            f"  自定义工作区：{workspaces.get('custom_count', 0)} 个"
            f"（跨次版本时将追加到新版默认工作区）"
        )
    if preflight and preflight.get("incompatible"):
        names = ", ".join(item.get("name", "?") for item in preflight["incompatible"][:5])
        more = len(preflight["incompatible"]) - 5
        lines.append(
            f"  ⚠ 预计不兼容插件 {len(preflight['incompatible'])} 个：{names}"
            + (f" 等 {more} 个" if more > 0 else "")
        )

    lines.append("")
    if preflight and not preflight.get("portable"):
        lines += [
            "⚠⚠ 目标未启用便携模式！",
            f"   配置将写入：{preflight['user_root']}",
            "   若希望配置落在 Blender 目录内的 portable 文件夹，",
            "   请先关闭本工具，在 blender.exe 同级新建名为 portable 的文件夹，",
            "   然后重新运行。",
            "",
        ]

    # 配置包漏掉的文件夹：必须在动手前讲清楚，否则迁移完才发现少东西
    missing = missing_folder_components(manifest)
    if missing:
        lines.append("⚠ 配置包中不含以下内容，迁移后目标目录将没有它们：")
        lines.extend(f"   · {item}" for item in missing)
        lines.append("   → 若需要，请回到源 Blender，在插件偏好设置里勾选后重新导出。")
        lines.append("")

    lines.append("目标 Blender 的现有配置将备份，可随时回滚。")
    if preflight and preflight.get("source_user_root"):
        lines.append(f"  来源配置目录：{preflight['source_user_root']}（已自动排除自迁移）")
    else:
        lines.append("⚠ 配置包未记录来源目录，请自行确认目标不是它的来源 Blender。")
    return "\n".join(lines)


# ============================================================
# 迁移执行
# ============================================================


def run_migration(
    profile_path,
    executable_path,
    preflight: dict | None = None,
    progress=None,
) -> dict:
    """执行迁移，返回 {outcome, report_html, recovery_dir} 供展示。

    ``progress`` 为可选阶段回调 (stage_key, message, ratio)，供 GUI 上报进度；
    默认 None 时行为与命令行模式完全一致。
    """
    core = load_migration_core()
    manifest = validate_profile(profile_path)
    profile = Path(profile_path)
    target = Path(executable_path)

    if not target.is_file():
        raise MigrateInputError(f"目标 Blender 不存在：{target}")
    if target.name.lower() != "blender.exe":
        print(f"[MMY] [WARN] 目标文件名不是 blender.exe：{target.name}（继续执行）")

    _, outcomes = core.execute_existing_profile_migration_multi(
        profile_path=profile,
        target_executables=[target],
        output_dir=profile.parent,  # recovery/报告放配置包旁边，天然在目标用户目录之外
        # 有源用户目录就用它还原自迁移防护；v1.4 旧包没有该字段则传 None
        current_user_root=source_user_root(manifest) or None,
        worker_script=WORKER_SCRIPT,
        current_pid=os.getpid(),
        progress=progress,
    )

    outcome = outcomes[0]
    if outcome.result is None:
        message = outcome.error or "迁移失败"
        if outcome.exception is not None:
            raise outcome.exception
        raise RuntimeError(message)

    result = outcome.result
    return {
        "target_version": version_string(result.target_version),
        "status": result.status,
        "recovery_dir": str(result.recovery_dir),
        "report_html": str(result.report_html_path) if result.report_html_path else "",
        "workspaces_restored": result.workspaces_restored,
        "keymap_lost_count": result.keymap_lost_count,
        "user_root": (preflight or {}).get("user_root", ""),
        "manifest": manifest,
    }


# ============================================================
# 导出侧：在源 Blender 后台跑一次快照，产出迁移配置包
# ============================================================


def source_executable_from_portable(portable_path) -> str:
    """从 portable 文件夹推出同级的 blender.exe。

    portable/ 的约定是「exe 同级放一个名为 portable 的文件夹」，
    所以 exe 固定在 portable 的上两级。用户只需拖 portable 文件夹，
    拖 blender.exe 也行（GUI 两个投放区都支持）。
    """
    folder = Path(portable_path)
    if not folder.is_dir():
        raise MigrateInputError(f"portable 文件夹不存在：{folder}")
    if folder.name.lower() != "portable":
        raise MigrateInputError(
            f"这看起来不是 Blender 的 portable 文件夹：\n"
            f"  {folder}\n\n"
            f"请拖入 blender.exe 同级、名为 portable 的那个文件夹。\n"
            f"（当前文件夹名为「{folder.name}」）"
        )
    candidate = folder.parent / "blender.exe"
    if not candidate.is_file():
        raise MigrateInputError(
            f"在 {folder.parent} 下没有找到 blender.exe。\n"
            f"请确认拖入的是便携版 Blender 的 portable 文件夹。"
        )
    return str(candidate)


def validate_source_blender(portable_path, executable_path) -> dict:
    """校验源 Blender 与 portable 文件夹可用，返回展示信息（只读，不启动）。"""
    folder = Path(portable_path)
    if not folder.is_dir():
        raise MigrateInputError(f"portable 文件夹不存在：{folder}")
    config_dir = folder / "config"
    userpref = config_dir / "userpref.blend"
    info = {
        "user_root": str(folder.resolve(strict=False)),
        "has_userpref": userpref.is_file(),
        "components_on_disk": [
            name
            for name, sub in (
                ("config", config_dir),
                ("scripts", folder / "scripts"),
                ("datafiles", folder / "datafiles"),
                ("extensions", folder / "extensions"),
            )
            if sub.is_dir()
        ],
    }
    if executable_path:
        exe = Path(executable_path)
        if not exe.is_file():
            raise MigrateInputError(f"源 Blender 不存在：{exe}")
        if exe.name.lower() != "blender.exe":
            raise MigrateInputError(
                f"源 Blender 文件名不对：{exe.name}（应为 blender.exe）"
            )
        # 便携版判定：exe 同级必须有 portable 目录，否则该 Blender 的配置
        # 写在 %APPDATA%，拖入的 portable 文件夹对它没有意义
        if not (exe.parent / "portable").is_dir():
            raise MigrateInputError(
                f"{exe.name} 同级没有 portable 文件夹，它不是便携版。\n\n"
                f"便携版的约定是：blender.exe 同级放一个名为 portable 的文件夹，"
                f"配置才会写在 Blender 目录内。\n"
                f"当前 {exe.parent} 下没有该文件夹。"
            )
        info["executable"] = str(exe.resolve(strict=False))
        info["binary_dir"] = str(exe.parent.resolve(strict=False))
    if not info["has_userpref"]:
        raise MigrateInputError(
            f"portable 文件夹里没有 config/userpref.blend：\n  {userpref}\n\n"
            f"这个 Blender 从未保存过偏好设置（或不是它的配置目录）。\n"
            f"请先打开一次该 Blender 并保存偏好，再回来导出。"
        )
    return info


def core_is_running(executable_path) -> bool:
    """源 Blender 是否正在运行（仅检测，不阻断）。

    运行中导出拿到的是磁盘上**已保存**的偏好，不是用户眼前的当前状态。
    不阻断是因为多数情况磁盘版本就是用户想要的；只在日志里提醒，
    让用户自己判断刚改的偏好有没有保存。
    """
    try:
        core = load_migration_core()
        return bool(core.windows_executable_is_running(Path(executable_path)))
    except Exception:  # noqa: BLE001 —— 检测失败不该影响导出
        return False


def run_source_export(
    portable_path,
    executable_path,
    output_dir,
    components: dict | None = None,
    progress=None,
    timeout: int = 900,
) -> dict:
    """后台启动源 Blender 生成迁移配置包，返回 worker 报告。

    与 ``run_migration`` 对称：那边把包**装进**目标 Blender，这边从源
    Blender **导出**包。两者都靠 ``blender --background --python`` 注入
    脚本，不要求用户装过本插件。

    ``progress`` 为可选阶段回调 (stage_key, message, ratio)。
    """
    core = load_migration_core()
    portable = Path(portable_path)
    target_exe = Path(executable_path)
    if not target_exe.is_file():
        raise MigrateInputError(f"源 Blender 不存在：{target_exe}")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "mmy_export_report.json"
    if report_path.exists():
        report_path.unlink()

    options = {
        "presets": True,
        "datafiles": True,
        "startup_scripts": True,
        "app_templates": True,
        "history": False,
    }
    if components:
        options.update({k: bool(v) for k, v in components.items() if k in options})

    worker = EXPORT_WORKER_SCRIPT
    if not worker.is_file():
        raise MigrateInputError(
            f"缺少导出执行文件：{worker}\n"
            f"请从完整仓库运行本工具（mmy_pack_config/ 必须随脚本一起）。"
        )

    command = [
        str(target_exe),
        "--background",
        # 不加 --factory-startup：必须加载用户真实偏好，否则导出一份空配置
        "--python-exit-code",
        "7",
        "--python",
        str(worker),
        "--",
        str(portable.resolve(strict=False)),
        str(output.resolve(strict=False)),
        str(report_path.resolve(strict=False)),
        json.dumps(options, ensure_ascii=False),
    ]

    def _emit(stage: str, message: str, ratio=None) -> None:
        if progress is None:
            return
        try:
            progress(stage, message, ratio)
        except Exception:  # noqa: BLE001 —— 回调异常绝不打断导出
            pass

    _emit("launch", f"启动 {target_exe.name}（后台模式，约需十几秒）…", 0.0)
    creationflags = getattr(core.subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    environment = core._blender_subprocess_env()
    try:
        completed = core.subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            check=False,
            env=environment,
        )
    except core.subprocess.TimeoutExpired as exc:
        raise MigrateInputError(
            f"源 Blender 后台导出超时（超过 {timeout} 秒）。\n"
            f"如果 Blender 弹出过「是否信任」之类的窗口，请先处理好再试。"
        ) from exc

    _emit("collect", "收集配置内容…", 0.6)
    if not report_path.is_file():
        combined = f"{completed.stdout}\n{completed.stderr}"
        tail = "\n".join(
            line for line in combined.splitlines() if line.strip()
        )[-6:]
        raise MigrateInputError(
            f"源 Blender 没有产出报告（退出码 {completed.returncode}）。\n"
            f"最后几行输出：\n{tail[:600]}"
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    # 报告已拿到即可清理，避免输出目录里留下临时文件
    try:
        report_path.unlink()
    except OSError:
        pass

    if report.get("status") != "success":
        raise MigrateInputError(str(report.get("error") or "源 Blender 导出失败"))
    _emit("write", f"完成：{Path(report['profile_path']).name}", 1.0)
    return report


def describe_export_report(report: dict) -> str:
    """把 worker 报告渲染成确认区文本（含组件清单与警告）。"""
    components = report.get("components") or []
    friendly = {
        "preferences": "偏好设置 userpref.blend",
        "startup_file": "启动文件 startup.blend",
        "addons": "插件（scripts/addons）",
        "extensions": "扩展（extensions）",
        "presets": "预设 scripts/presets",
        "datafiles": "数据文件 datafiles",
        "startup_scripts": "启动脚本 scripts/startup",
        "app_templates": "应用模板 bl_app_templates_user",
        "history": "最近访问记录",
        "workspaces": "工作区清单",
        "keymap_fallback": "快捷键（keymap.py）",
    }
    lines = [
        f"将导出迁移配置包（{len(components)} 类组件）：",
        "",
    ]
    for key in components:
        lines.append(f"  · {friendly.get(key, key)}")
    lines += [
        "",
        f"  源版本：Blender {version_string(report.get('source_version') or [])}",
        f"  文件数：{report.get('file_count', 0)} 个",
        f"  输出到：{report.get('profile_path', '')}",
    ]
    for note in report.get("notes") or []:
        lines.append(f"  · {note}")
    warnings = report.get("warnings") or []
    if warnings:
        lines += ["", "注意："]
        for item in warnings:
            lines.append(f"  ! {item}")
    lines += [
        "",
        "该包可拖入「配置迁移」页导入到目标 Blender。",
        "导出过程会启动一次源 Blender（后台模式，不会弹出界面）。",
    ]
    return "\n".join(lines)


def format_result_summary(info: dict) -> str:
    lines = [
        "迁移完成！",
        "",
        f"  目标版本：Blender {info['target_version']}",
        f"  状态：{'成功' if info['status'] == 'success' else '完成（有降级警告，详见报告）'}",
    ]
    if info.get("user_root"):
        lines.append(f"  配置写入：{info['user_root']}")
    if info.get("workspaces_restored"):
        lines.append(
            f"  已恢复自定义工作区：{len(info['workspaces_restored'])} 个"
            f"（{', '.join(info['workspaces_restored'])}）"
        )
    if info.get("keymap_lost_count"):
        lines.append(f"  快捷键丢失：{info['keymap_lost_count']} 条（详见报告）")
    lines += [
        "",
        f"回滚备份：{info['recovery_dir']}",
    ]
    if info.get("report_html"):
        lines.append(f"审计报告：{info['report_html']}")
    lines += [
        "",
        "直接启动目标 Blender 即可使用迁移后的配置。",
    ]
    return "\n".join(lines)


# ============================================================
# 图形模式（tkinter）
# ============================================================


def run_tkinter_ui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print("[MMY] [ERROR] 当前环境无 tkinter，请使用命令行模式：")
        print("       python migrate.py <profile.zip> <blender.exe>")
        return 1

    root = tk.Tk()
    root.withdraw()

    profile_path = filedialog.askopenfilename(
        title="选择迁移配置包（插件「一键导出」生成的 .zip）",
        filetypes=[("迁移配置包", "*.zip"), ("所有文件", "*.*")],
    )
    if not profile_path:
        return 0

    try:
        manifest = validate_profile(profile_path)
    except MigrateInputError as exc:
        messagebox.showerror("配置包无效", str(exc))
        return 1

    executable_path = filedialog.askopenfilename(
        title="选择目标 Blender 可执行文件（blender.exe）",
        filetypes=[("Blender 可执行文件", "blender.exe"), ("所有文件", "*.*")],
    )
    if not executable_path:
        return 0

    root.config(cursor="watch")
    root.update()
    try:
        preflight = preflight_target(manifest, executable_path)
    except MigrateInputError as exc:
        root.config(cursor="")
        messagebox.showerror("目标 Blender 校验失败", str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        root.config(cursor="")
        messagebox.showerror("目标 Blender 检测失败", str(exc))
        return 1
    finally:
        root.config(cursor="")

    summary = describe_profile(manifest, executable_path, None, preflight)
    if not messagebox.askyesno("确认迁移", summary):
        return 0

    try:
        info = run_migration(profile_path, executable_path, preflight)
    except Exception as exc:  # noqa: BLE001 —— 错误上屏，统一出口
        messagebox.showerror("迁移失败", str(exc))
        return 1

    messagebox.showinfo("迁移完成", format_result_summary(info))
    return 0


# ============================================================
# 主函数
# ============================================================


def main() -> int:
    print("=" * 60)
    print("MMY Blender Configure — 独立迁移工具（免插件安装）")
    print("=" * 60)
    print()

    if len(sys.argv) == 1:
        return run_tkinter_ui()

    if len(sys.argv) != 3:
        print("用法：")
        print("  python migrate.py                              # 图形模式")
        print("  python migrate.py <profile.zip> <blender.exe>  # 命令行模式")
        return 2

    profile_path, executable_path = sys.argv[1], sys.argv[2]
    try:
        manifest = validate_profile(profile_path)
        print("[MMY] 正在检测目标 Blender（启动一次后台探针）...")
        preflight = preflight_target(manifest, executable_path)
        print(describe_profile(manifest, executable_path, None, preflight))
        print()
        info = run_migration(profile_path, executable_path, preflight)
    except MigrateInputError as exc:
        print(f"[MMY] [ERROR] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[MMY] [ERROR] 迁移失败：{exc}")
        import traceback

        traceback.print_exc()
        return 1

    print(format_result_summary(info))
    return 0


def _show_fatal_error(text: str) -> None:
    """图形模式经 pythonw 启动时没有控制台，用 Win32 弹窗兜底显示致命错误。

    否则双击后启动失败会完全静默，用户无从判断。
    """
    print(text)
    if os.name != "nt":
        return
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.user32.MessageBoxW(None, text, "MMY 迁移工具启动失败", 0x10)
    except Exception:  # noqa: BLE001 —— 兜底失败不再抛
        pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[MMY] 已取消。")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 —— 兜底：任何未预期异常都要可见
        import traceback

        _show_fatal_error(
            f"迁移工具启动或运行失败：\n\n{exc}\n\n详细信息：\n\n{traceback.format_exc()}"
        )
        sys.exit(1)
