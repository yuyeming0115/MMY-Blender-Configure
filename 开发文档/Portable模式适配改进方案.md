# Blender Portable 模式适配改进方案

> 讨论时间：2026-05-22
> 状态：✅ 已确认并实施

---

## 一、背景说明

### Portable 模式概述

Blender Portable 模式是一种"便携式运行"方式，所有配置文件都存储在 Blender 安装目录下的 `portable/` 文件夹中，而不是系统用户目录。

**触发条件**：在 Blender 可执行文件所在目录创建 `portable/` 文件夹，Blender 启动时自动检测并切换为 portable 模式。

### 目录结构对比

| 模式 | 配置目录位置 |
|------|-------------|
| **普通模式** | `%APPDATA%\Blender Foundation\Blender\4.5\config\` |
| **Portable 模式** | `{Blender安装目录}\portable\4.5\config\` |

```
portable/
├── 4.5/                       ← 版本号目录（重要！）
│   ├── config/
│   │   ├── userpref.blend
│   │   ├── startup.blend
│   │   ├── bookmarks.txt
│   │   └── recent-files.txt
│   ├── scripts/
│   │   ├── addons/
│   │   ├── presets/
│   │   └── startup/
│   ├── datafiles/
│   ├── extensions/
│   └── studiolights/
└── (无版本目录时，使用当前版本)
```

---

## 二、现有代码分析

### 当前实现

```python
# utils.py
def get_config_dir() -> Path:
    return Path(bpy.utils.user_resource('CONFIG'))

def get_addons_dir() -> Path:
    return Path(bpy.utils.user_resource('SCRIPTS')) / "addons"
```

### API 行为验证

`bpy.utils.user_resource()` 的行为：

| 模式 | 返回值 |
|------|--------|
| 普通 | `C:\Users\{用户}\AppData\Roaming\Blender Foundation\Blender\4.5\config` |
| Portable | `{安装目录}\portable\4.5\config` |

**结论**：API 已自动适配，无需手动处理路径差异。

---

## 三、插件现有问题清单

### 问题 1：Manifest 缺少 portable 模式信息

**现状**：`utils.py` 中 manifest 结构：
```python
manifest = {
    "blender_version": "4.5.0",
    "created_at": "2026-05-22T...",
    "includes": ["keymap", "prefs", "addons", ...]
}
```

**缺失字段**：
- `portable_mode` - 是否来自 portable 模式
- `portable_base_dir` - portable 基路径（用于判断路径依赖）
- `source_config_path` - 源配置相对路径

**影响**：导入时无法判断备份来源模式，无法提供针对性提示。

### 问题 2：导入时没有模式兼容检测

**现状**：`operators.py` 只检查版本号：
```python
def _check_version(self, manifest):
    backup_ver = manifest.get("blender_version", "")
    current_ver = ".".join(str(v) for v in bpy.app.version)
    # 仅检查主版本号是否匹配
```

**缺失检测**：
- portable → 普通模式迁移
- 普通 → portable 模式迁移
- 不同 portable 基路径迁移

**影响**：跨模式迁移时，用户无法获得路径依赖风险提示。

### 问题 3：UI 不显示当前模式状态

**现状**：`ui.py` 面板只显示配置类型选项：
```python
box.label(text="选择配置类型")
box.prop(prefs, "include_keymap")
box.prop(prefs, "include_prefs")
...
```

**缺失信息**：
- 当前运行模式（Portable / 普通）
- 当前配置路径位置
- 路径依赖状态提示

**影响**：用户不清楚当前配置存储位置，可能导致困惑。

### 问题 4：路径依赖检测缺失

**现状**：不检测书签、资产库等路径依赖项。

**风险场景**：
| 依赖项 | 文件位置 | 迁移风险 |
|--------|---------|---------|
| 书签 | `config/bookmarks.txt` | 绝对路径在新环境失效 |
| 最近文件 | `config/recent-files.txt` | 文件路径不存在 |
| 资产库 | `userpref.blend` 内 | 资产库路径失效 |
| 插件设置 | 各插件内部 | 插件存储的路径失效 |

**影响**：迁移后用户发现书签失效、资产库丢失，不知原因。

### 问题 5：临时备份位置问题

**现状**：`utils.py` 临时备份存放位置：
```python
backup_path = config_dir / f".mmy_temp_backup_{timestamp}.zip"
```

**潜在问题**：
| 场景 | 问题 |
|------|------|
| portable 在 U 盘 | U 盘空间可能不足 |
| portable 模式 | 用户可能找不到备份文件（在 portable 目录深处） |
| 多次导入 | 临时备份文件累积，占用空间 |

**建议改进**：
- 备份位置可选择（用户指定或默认系统临时目录）
- 导入成功后自动清理旧备份（保留最近 3 个）

---

### 问题汇总表

| # | 问题 | 风险等级 | 优先级 | 是否需要立即处理 |
|---|------|---------|--------|-----------------|
| 1 | Manifest 缺 portable 信息 | 中 | P2 | ⚠️ 建议添加 |
| 2 | 导入时无模式检测 | 中 | P2 | ⚠️ 建议添加 |
| 3 | UI 不显示模式状态 | 低 | P3 | 可选 |
| 4 | 路径依赖检测缺失 | 低 | P3 | 可选 |
| 5 | 临时备份位置 | 低 | P4 | 可选 |

---

## 四、需要考虑的问题

### 问题清单

| # | 问题 | 风险等级 | 是否需要处理 |
|---|------|---------|-------------|
| 1 | API 自动适配 | 低 | ❌ 已自动处理 |
| 2 | 同模式迁移（portable → portable） | 低 | ❌ 无问题 |
| 3 | 同模式迁移（普通 → 普通） | 低 | ❌ 无问题 |
| 4 | **跨模式迁移**（portable → 普通） | 中 | ⚠️ 需要提示 |
| 5 | **跨模式迁移**（普通 → portable） | 中 | ⚠️ 需要提示 |
| 6 | manifest 缺少模式信息 | 低 | ✅ 建议添加 |
| 7 | 用户不了解模式差异 | 低 | ✅ 建议提示 |
| 8 | portable 基路径依赖 | 中 | ⚠️ 需要说明 |
| 9 | 版本号目录变化 | 低 | ✅ 已处理 |

---

## 四、跨模式迁移场景分析

### 场景 1：Portable → 普通

```
导出环境：D:\Apps\Blender\portable\4.5\config\userpref.blend
导入环境：C:\Users\{用户}\AppData\...\Blender\4.5\config\userpref.blend
```

**问题**：
- 配置内容本身无问题
- 路径依赖项（bookmarks、资产库路径）可能失效
- 用户可能不清楚配置去向

**解决方案**：
- 提示用户：配置将导入到系统用户目录
- 路径依赖项需要用户手动调整

### 场景 2：普通 → Portable

```
导出环境：C:\Users\{用户}\AppData\...\Blender\4.5\config\userpref.blend
导入环境：D:\Apps\Blender\portable\4.5\config\userpref.blend
```

**问题**：
- 配置内容本身无问题
- 路径依赖项可能失效（原来的绝对路径指向不存在位置）
- 用户可能不清楚配置去向

**解决方案**：
- 提示用户：配置将导入到 portable 目录
- 路径依赖项需要用户手动调整

### 场景 3：Portable A → Portable B

```
导出环境：D:\Apps\Blender_A\portable\4.5\config\
导入环境：E:\PortableApps\Blender_B\portable\4.5\config\
```

**问题**：
- 配置内容无问题
- 路径依赖项可能失效（指向 D:\ 而非 E:\）
- 最常见的 portable 使用场景（U盘迁移）

**解决方案**：
- 提示用户检查路径依赖项
- 建议使用相对路径

---

## 五、改进方案

### 5.1 Portable 模式检测函数

```python
def is_portable_mode() -> bool:
    """
    检测 Blender 是否运行在 portable 模式。
    
    原理：
    1. 检查 Blender 安装目录下是否存在 portable 文件夹
    2. 验证 user_resource 返回的路径是否在 portable 目录下
    
    Returns:
        bool: True 表示 portable 模式，False 表示普通模式
    """
    import bpy
    from pathlib import Path
    
    blender_dir = Path(bpy.app.binary_path).parent
    portable_dir = blender_dir / "portable"
    
    if not portable_dir.exists():
        return False
    
    # 进一步验证：检查配置目录是否确实在 portable 下
    config_dir = Path(bpy.utils.user_resource('CONFIG'))
    
    # portable 目录结构：portable/{version}/config
    # 需要检查 config_dir 的父目录是否为 portable_dir
    try:
        # 找到 portable 目录在路径中的位置
        parts = config_dir.parts
        if 'portable' in parts:
            return True
    except:
        pass
    
    return False


def get_portable_base_dir() -> Path | None:
    """
    获取 portable 模式的基目录（Blender 安装目录）。
    
    Returns:
        Path | None: portable 基目录，如果不是 portable 模式则返回 None
    """
    if not is_portable_mode():
        return None
    
    import bpy
    from pathlib import Path
    
    blender_dir = Path(bpy.app.binary_path).parent
    return blender_dir
```

### 5.2 Manifest 增强字段

```json
{
    "blender_version": "4.5.0",
    "blender_version_sub": 91,
    "created_at": "2026-05-22T15:00:00",
    
    // === 新增字段 ===
    "portable_mode": true,
    "portable_base_dir": "D:/Apps/Blender/",
    "source_config_path": "portable/4.5/config",
    
    // === 现有字段 ===
    "includes": ["keymap", "prefs", "addons", "config", "presets"],
    "enabled_addons": ["addon_a", "addon_b"],
    
    // === 新增：路径依赖警告 ===
    "has_path_dependencies": true,
    "path_dependency_types": ["bookmarks", "asset_library"]
}
```

### 5.3 导入时的模式兼容检查

```python
def check_mode_compatibility(manifest: dict) -> dict | None:
    """
    检查导入时模式兼容性。
    
    Args:
        manifest: 备份文件的 manifest 数据
        
    Returns:
        dict | None: 如果有兼容性问题返回警告信息，否则返回 None
    """
    current_portable = is_portable_mode()
    backup_portable = manifest.get("portable_mode", False)
    
    if current_portable == backup_portable:
        return None  # 模式一致，无问题
    
    # 模式不一致，生成警告
    if backup_portable and not current_portable:
        warning = {
            "type": "portable_to_normal",
            "title": "Portable → 普通模式迁移",
            "message": "备份来自 Portable 模式，当前为普通模式",
            "details": [
                "配置将导入到系统用户目录",
                f"路径：{bpy.utils.user_resource('CONFIG')}",
                "路径依赖项（书签、资产库）可能需要手动调整"
            ]
        }
    else:
        warning = {
            "type": "normal_to_portable",
            "title": "普通 → Portable 模式迁移",
            "message": "备份来自普通模式，当前为 Portable 模式",
            "details": [
                "配置将导入到 portable 目录",
                f"路径：{get_portable_base_dir()}/portable/{bpy.app.version[0]}.{bpy.app.version[1]}/config",
                "路径依赖项（书签、资产库）可能需要手动调整"
            ]
        }
    
    return warning
```

### 5.4 UI 提示设计

导入时检测到模式不一致，在报告信息中添加：

```python
# operators.py - ImportConfig.execute()

warning = check_mode_compatibility(manifest)
if warning:
    # 使用 WARNING 级别报告（不阻止操作）
    self.report({'WARNING'}, warning["message"])
    
    # 可选：弹出确认对话框
    # 但为了简化，先只在报告中显示
```

### 5.5 路径依赖检测

```python
def detect_path_dependencies() -> list[str]:
    """
    检测当前配置中的路径依赖项。
    
    Returns:
        list[str]: 存在路径依赖的类型列表
    """
    dependencies = []
    
    import bpy
    from pathlib import Path
    
    # 1. 检查书签文件
    bookmarks_file = Path(bpy.utils.user_resource('CONFIG')) / 'bookmarks.txt'
    if bookmarks_file.exists():
        content = bookmarks_file.read_text(encoding='utf-8')
        # 检查是否包含绝对路径
        if any(line.startswith('/') or ':' in line for line in content.splitlines()):
            dependencies.append('bookmarks')
    
    # 2. 检查资产库路径
    prefs = bpy.context.preferences
    if prefs.filepaths.asset_libraries:
        for lib in prefs.filepaths.asset_libraries:
            if lib.path and not lib.path.startswith('//'):
                dependencies.append('asset_library')
    
    return dependencies
```

---

## 六、需要讨论的问题

### Q1: 是否需要在导入时阻止跨模式迁移？

| 选项 | 说明 |
|------|------|
| **A. 仅警告，不阻止** | 用户可以继续导入，自行处理路径问题 |
| **B. 强制确认** | 弹出对话框，用户确认后才导入 |
| **C. 自动转换路径** | 尝试自动转换路径依赖项（复杂度高） |

**建议**：选项 A，仅警告提示，不阻止操作。

### Q2: Manifest 是否需要记录 portable 基路径？

| 选项 | 说明 |
|------|------|
| **A. 记录完整基路径** | 如 `D:/Apps/Blender/`，便于用户了解来源 |
| **B. 仅记录模式状态** | 只记录 `portable_mode: true/false`，路径信息不重要 |
| **C. 不记录** | 认为用户不需要知道 |

**建议**：选项 A，记录基路径有助于用户判断路径依赖问题。

### Q3: 是否需要在导出时提示路径依赖？

| 选项 | 说明 |
|------|------|
| **A. 检测并提示** | 导出前检查书签、资产库等，提示用户可能存在路径依赖 |
| **B. 不提示** | 用户自行负责，导入时再提醒 |

**建议**：选项 B，导入时提醒更合理，导出时不打扰用户。

### Q4: portable 目录版本号处理

Portable 目录结构为 `portable/{version}/`，需要考虑：

- 用户升级 Blender 时，旧版本目录（如 `portable/4.4/`）不会自动迁移
- 导入配置时，版本号目录需要匹配当前版本

**现有处理**：`bpy.utils.user_resource()` 自动返回当前版本对应的路径，无需额外处理。

**结论**：已自动处理，无需改动。

---

## 七、改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `utils.py` | 新增 `is_portable_mode()`, `get_portable_base_dir()`, `detect_path_dependencies()` |
| `operators.py` | 导入时调用 `check_mode_compatibility()` 并显示警告 |
| `manifest.json` | 新增 `portable_mode`, `portable_base_dir`, `has_path_dependencies` 字段 |

---

## 八、讨论要点总结

1. **跨模式迁移是否需要强制确认？** — ✅ 已采纳：仅警告，不阻止
2. **Manifest 记录哪些 portable 信息？** — ✅ 已采纳：模式状态 + 基路径 + 路径依赖
3. **导出时是否提示路径依赖？** — ✅ 已采纳：不提示，导入时提醒
4. **是否需要路径自动转换？** — ✅ 已采纳：不实现

---

## 九、实施记录

### 2026-05-22 实施

**修改文件**：
- `mmy_pack_config/utils.py` — 新增 4 个函数
- `mmy_pack_config/operators.py` — 导入时添加模式兼容检查

**新增函数**：
| 函数 | 功能 |
|------|------|
| `is_portable_mode()` | 检测是否为 portable 模式 |
| `get_portable_base_dir()` | 获取 portable 基目录 |
| `detect_path_dependencies()` | 检测书签、资产库等路径依赖 |
| `check_mode_compatibility()` | 导入时模式兼容检查 |

**Manifest 新增字段**：
| 字段 | 说明 |
|------|------|
| `portable_mode` | bool，是否来自 portable 模式 |
| `portable_base_dir` | portable 基路径（仅 portable 模式有值） |
| `source_config_path` | 源配置相对路径 |
| `has_path_dependencies` | bool，是否存在路径依赖 |
| `path_dependency_types` | list，路径依赖类型列表 |
| `blender_version_sub` | 子版本号 |

**导入行为**：
- 模式不一致时显示 WARNING 级别警告
- 不阻止导入操作，用户可自行处理路径问题