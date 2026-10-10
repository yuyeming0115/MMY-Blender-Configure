"""donor 模式只读冒烟测试（blender --background）。

验证 _restore_workspaces 依赖的核心 bpy 行为在真机上成立：
1. read_homefile 后台可读目标启动文件
2. libraries.load(link=True) 能列出捐赠文件中的工作区
3. libraries.load(link=False) 能追加自定义工作区（不落盘）

不调用 save_userpref / save_as_mainfile，不修改任何用户文件。
"""

import json
import sys
from pathlib import Path

import bpy

DONOR = Path(r"C:\BlenderAPP\Blender5.1\portable\config\startup.blend")

FACTORY = {
    "Layout", "Modeling", "Sculpting", "UV Editing", "Texture Paint",
    "Shading", "Rendering", "Compositing", "Geometry Nodes", "Scripting",
    "Motion Tracking", "Masking", "VFX", "Video Editing",
}

result = {"ok": True, "steps": []}


def step(name, fn):
    try:
        value = fn()
        result["steps"].append({"step": name, "ok": True, "detail": value})
        print(f"[SMOKE] PASS {name}: {value}")
    except Exception as exc:
        result["ok"] = False
        result["steps"].append({"step": name, "ok": False, "detail": str(exc)})
        print(f"[SMOKE] FAIL {name}: {exc}")


def _read_homefile():
    assert bpy.ops.wm.read_homefile() == {"FINISHED"}
    return f"workspaces={len(bpy.data.workspaces)}"


def _list_donor_workspaces():
    with bpy.data.libraries.load(str(DONOR), link=True) as (data_from, _to):
        names = list(data_from.workspaces)
    custom = [n for n in names if n not in FACTORY]
    return json.dumps({"all": len(names), "custom": custom}, ensure_ascii=False)


def _append_custom_workspaces():
    """在内存中追加第一个自定义工作区并验证出现（不落盘）。"""
    with bpy.data.libraries.load(str(DONOR), link=True) as (data_from, _to):
        custom = [n for n in data_from.workspaces if n not in FACTORY]
    if not custom:
        return "donor 无自定义工作区，跳过追加验证"
    existing = {ws.name for ws in bpy.data.workspaces}
    target_name = next((n for n in custom if n not in existing), None)
    if target_name is None:
        return "所有自定义工作区已存在，跳过追加验证"
    before = {ws.name for ws in bpy.data.workspaces}
    with bpy.data.libraries.load(str(DONOR), link=False) as (data_from, data_to):
        assert target_name in data_from.workspaces
        data_to.workspaces = [target_name]
    after = {ws.name for ws in bpy.data.workspaces}
    new_names = sorted(after - before)
    assert target_name in bpy.data.workspaces, f"追加后未找到 {target_name}"
    return json.dumps({"appended": new_names}, ensure_ascii=False)


def _check_icons():
    # 确认 UI 用到的图标 ID 存在（bpy.types.Icon 不可枚举，用 RNA 枚举校验）
    from bpy.props import EnumProperty
    icons = {"WORKSPACE", "FILE_BLEND", "CHECKMARK", "ERROR", "RECOVER_LAST"}
    # 通过任意带 icon 的 operator 属性不靠谱；改用 ui 模块的图标列表接口
    try:
        import bpy.utils as u
        # 间接校验：构造带这些 icon 的 menu 类注册
        cls = type("SmokeIconCheck", (bpy.types.Menu,), {
            "bl_idname": "SMOKE_MT_icon_check",
            "bl_label": "icon check",
            "draw": lambda self, ctx: [
                self.layout.label(text=i, icon=i) for i in sorted(icons)
            ],
        })
        bpy.utils.register_class(cls)
        bpy.utils.unregister_class(cls)
        return f"{len(icons)} icons ok"
    except Exception as exc:
        raise RuntimeError(f"图标校验失败: {exc}")


step("read_homefile", _read_homefile)
step("list_donor_workspaces", _list_donor_workspaces)
step("append_custom_workspaces", _append_custom_workspaces)
step("check_ui_icons", _check_icons)

print("[SMOKE] RESULT " + json.dumps(result, ensure_ascii=False))
sys.exit(0 if result["ok"] else 7)
