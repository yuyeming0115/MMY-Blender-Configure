# Blender 配置导出资料汇总

> 整理时间：2026-05-22
> 来源：项目文档、Blender 官方 API、社区实践

---

## 一、Blender 配置目录结构

### 目录位置概览

| 平台 | 配置根目录 |
|------|-----------|
| **Windows** | `%APPDATA%\Blender Foundation\Blender\` |
| **macOS** | `~/Library/Application Support/Blender/` |
| **Linux** | `~/.config/blender/` |

### 子目录详解

```
Blender/4.5/                      # 版本号目录
├── config/                       # 核心配置目录
│   ├── userpref.blend            # 用户偏好设置（核心文件）
│   ├── bookmarks.txt             # 文件浏览器书签
│   ├── recent-files.txt          # 最近打开的文件列表
│   └── blender.desktop           # 桌面配置（仅 Linux）
│
├── scripts/                      # 用户脚本目录
│   ├── addons/                   # 用户安装的插件
│   │   ├── single_file.py        # 单文件插件
│   │   └── multi_file_package/   # 多文件插件包
│   │       └── __init__.py
│   ├── presets/                  # 用户预设
│   │   ├── render/               # 渲染预设
│   │   ├── modifier/             # 修改器预设
│   │   └── operator/             # 操作预设
│   └── startup/                  # 启动脚本
│       └── auto_run.py           # 自动执行脚本
│
├── datafiles/                    # 用户数据文件
│   ├── brushes/                  # 自定义笔刷
│   ├── matcaps/                  # 材质捕获贴图
│   ├── textures/                 # 用户贴图库
│   └── assets/                   # 本地资产库定义
│
├── studiolights/                 # 工作室灯光预设
│   └── custom_hdri.exr           # 自定义 HDRI 灯光
│
└── extensions/                   # Blender 4.5+ 扩展系统
    ├── user_installed/           # 用户安装的扩展
    └── repo_index.json           # 扩展仓库索引
```

---

## 二、Python API 参考

### 配置目录获取

```python
import bpy

# 核心配置目录（包含 userpref.blend）
config_dir = bpy.utils.user_resource('CONFIG')

# 脚本目录（addons/presets/startup 的父目录）
scripts_dir = bpy.utils.user_resource('SCRIPTS')

# 数据文件目录
datafiles_dir = bpy.utils.user_resource('DATAFILES')

# 工作室灯光目录
studiolights_dir = bpy.utils.user_resource('STUDIO_LIGHTS')

# 扩展目录（Blender 4.5+）
extensions_dir = bpy.utils.user_resource('EXTENSIONS')
```

### 插件目录详细路径

```python
# 用户插件目录
addons_dir = bpy.utils.user_resource('SCRIPTS', path="addons")

# 系统插件目录（Blender 内置）
system_addons = bpy.utils.system_resources('SCRIPTS', path="addons")
```

### 保存用户偏好

```python
# 确保配置已写入磁盘
bpy.ops.wm.save_userpref()
```

---

## 三、userpref.blend 结构分析

### 内容组成

`userpref.blend` 是 Blender 的核心配置文件，包含：

| 类别 | 具体内容 |
|------|---------|
| **界面设置** | 主题、语言、字体、界面缩放 |
| **编辑器偏好** | 默认编辑器布局、视图设置 |
| **输入设置** | 鼠标、键盘、NDOF 设备配置 |
| **快捷键映射** | 所有自定义快捷键定义 |
| **插件状态** | 已启用插件的列表和配置 |
| **系统设置** | 内存、GPU、渲染路径等 |
| **路径设置** | 渲染输出、临时文件、资产库路径 |

### 版本兼容性

- Blender 使用 `.blend` 格式存储配置，内部版本号标记
- 高版本可读取低版本配置（向后兼容）
- 低版本无法读取高版本配置（向前不兼容）
- 主版本号不同时（如 3.x → 4.x）存在重大不兼容风险

---

## 四、配置导出策略

### 备份包格式设计

```
mmy_config_20260522_143000.zip
├── manifest.json                 # 元数据文件
├── userpref.blend                # 核心配置
├── addons/                       # 插件目录
│   ├── addon_a.py
│   └── addon_b/
│       └── __init__.py
├── config/                       # 用户配置
│   ├── bookmarks.txt
│   └── recent-files.txt
├── scripts/presets/              # 预设目录
├── scripts/startup/              # 启动脚本
├── datafiles/                    # 数据文件
├── studiolights/                 # 灯光预设
└── extensions/                   # 扩展（4.5+）
```

### manifest.json 结构

```json
{
    "blender_version": "4.5.0",
    "blender_version_sub": 91,
    "created_at": "2026-05-22T14:30:00",
    "platform": "Windows",
    "includes": [
        "keymap",
        "prefs",
        "addons",
        "config",
        "presets",
        "startup",
        "datafiles",
        "studiolights",
        "extensions"
    ],
    "addon_count": 15,
    "preset_count": 8
}
```

---

## 五、版本兼容性矩阵

### 兼容性评估表

| 导出版本 | 导入目标 | 兼容性 | 风险说明 |
|---------|---------|--------|---------|
| 4.5 | 4.5 | ✅ 完全兼容 | 直接迁移 |
| 4.5 | 4.4 | ⚠ 部分兼容 | extensions 目录无效，新功能设置丢失 |
| 4.5 | 4.3 | ⚠ 部分兼容 | 同上，更多设置可能不兼容 |
| 4.5 | 3.x | ❌ 不兼容 | 主版本跨度过大，会崩溃 |
| 4.4 | 4.5 | ⚠ 部分兼容 | 缺少 4.5 新功能设置，需补全 |
| 3.x | 4.x | ❌ 禁止 | 快捷键系统重构，节点系统大改 |

### 关键版本变更点

| 版本变更 | 主要变化 | 影响范围 |
|---------|---------|---------|
| **2.x → 3.x** | 工作区系统重构 | userpref.blend |
| **3.x → 4.0** | 节点系统大改、EEVEE 重构 | presets, datafiles |
| **4.0 → 4.1** | 快捷键系统重构 | keymap |
| **4.4 → 4.5** | 新增扩展系统 | extensions |

---

## 六、风险分类

### 低风险（推荐导出）

| 配置项 | 原因 |
|--------|------|
| **config (bookmarks, recent-files)** | 纯文本格式，跨版本不变 |
| **studiolights** | 灯光系统相对稳定 |
| **presets** | 预设系统稳定，参数可缺失 |

### 中风险（谨慎导出）

| 配置项 | 原因 |
|--------|------|
| **userpref.blend** | Blender 可自动转换，但新版本设置丢失 |
| **datafiles** | 笔刷格式可能随版本演进 |
| **presets** | 新版本可能新增/删除参数 |

### 高风险（需测试）

| 配置项 | 原因 |
|--------|------|
| **addons** | 插件依赖特定 API 版本 |
| **keymap** | 快捷键系统可能重构 |

### 禁止跨版本迁移

| 配置项 | 原因 |
|--------|------|
| **startup 脚本** | API 变更会导致脚本报错甚至崩溃 |
| **extensions (4.5+)** | 仅 4.5+ 存在此系统 |

---

## 七、导入安全流程

### 推荐流程

```
1. 版本检查 → 主版本号不同则警告
      ↓
2. 临时备份 → 导入前备份当前配置
      ↓
3. 选择性导入 → 按勾选项导入，非全量覆盖
      ↓
4. 重启 Blender → 让配置生效
      ↓
5. 功能验证 → 检查快捷键、插件、设置
      ↓
6. 异常回滚 → 如出错，使用临时备份还原
```

### 临时备份命名

```python
# 临时备份文件名格式
temp_backup_name = f".mmy_temp_backup_{timestamp}.zip"

# 存放位置
temp_backup_path = config_dir / temp_backup_name
```

---

## 八、常见问题解答

### Q1: 为什么 userpref.blend 不分离 keymap？

Blender 本身不分离存储快捷键和偏好设置。强行分离需要解析 `.blend` 文件格式，复杂度过高，且可能破坏内部数据结构。

### Q2: 插件启用状态在哪？

插件启用状态存储在 `userpref.blend` 中。导入 `userpref.blend` 会自动恢复插件启用状态，但插件文件本身需要从 `addons/` 目录还原。

### Q3: 如何处理路径依赖？

配置中的绝对路径（如书签、资产库路径）在新机器可能无效。建议：
- 书签：导出前检查是否为相对路径
- 资产库：使用相对路径或环境变量

### Q4: 扩展系统 (extensions) 是什么？

Blender 4.5 引入的官方扩展平台，替代传统插件系统的一部分功能。扩展通过 Blender 内置的扩展管理器安装，与传统 addons 有不同的存储和管理方式。

### Q5: startup.blend 和 userpref.blend 有什么区别？

| 文件 | 内容 | 用途 |
|------|------|------|
| **startup.blend** | 默认场景和界面布局 | 新建文件时的初始状态 |
| **userpref.blend** | 用户偏好设置、插件状态、快捷键 | 全局配置 |

两个文件都在 Blender 启动时加载，`startup.blend` 定义初始场景，`userpref.blend` 定义全局偏好。

### Q6: 如何实现便携式/便携模式？

在 Blender 可执行文件所在目录创建 `portable` 文件夹，Blender 会将所有配置、插件、预设保存在该文件夹中，适合 U 盘携带或云同步。

```bash
# Windows 示例
C:\Program Files\Blender Foundation\Blender 4.5\portable\

# portable 目录结构
portable/
├── 4.5/
│   ├── config/
│   ├── scripts/
│   └── datafiles/
```

### Q7: Python 如何保存用户偏好？

```python
# 方法 1: 使用 Operator（推荐）
bpy.ops.wm.save_userpref()

# 方法 2: 设置自动保存标志
bpy.context.preferences.use_preferences_save = True
# 修改偏好后自动保存

# 方法 3: 修改插件偏好
addon_prefs = bpy.context.preferences.addons[__package__].preferences
addon_prefs.some_property = "new value"
bpy.ops.wm.save_userpref()
```

### Q8: 如何手动导出快捷键？

在 Blender 界面操作：
1. `Edit > Preferences > Keymap`
2. 点击 `Export` 按钮
3. 保存为 `.py` 文件

导入时使用同一位置的 `Import` 按钮。

### Q9: 多台电脑如何同步配置？

| 方案 | 操作 | 适用场景 |
|------|------|---------|
| **Blender Cloud Sync** | 使用官方 Blender Cloud 插件 | 有 Blender Cloud 账户 |
| **手动复制** | 复制 config 文件夹到云盘 | 免费方案 |
| **Portable + 云同步** | Blender portable 模式放在 Dropbox/OneDrive | 便携需求 |
| **符号链接** | 将 config 目录链接到云同步位置 | 高级用户 |

### Q10: 版本升级时如何迁移插件？

1. 安装新版本 Blender 时勾选 "Import Blender Preferences"
2. 手动复制 `scripts/addons/` 目录到新版本
3. 检查插件是否兼容新版本 API（特别是主版本号变化）

**注意事项**：
- 主版本号变化（如 3.x → 4.x）可能导致插件不兼容
- 使用 [blender-addon-updater](https://github.com/CGCookie/blender-addon-updater) 模块可自动处理版本兼容

---

## 九、参考资源

### 官方文档

- [Blender Directory Layout](https://docs.blender.org/manual/en/latest/advanced/blender_directory_layout.html) — 目录结构官方说明
- [Preferences Editor](https://docs.blender.org/manual/en/latest/editors/preferences_editor.html) — 偏好设置编辑器
- [Python API Reference - bpy.utils](https://docs.blender.org/api/current/bpy.utils.html) — Python API 工具模块
- [Compatibility](https://docs.blender.org/manual/en/latest/files/blend/compatibility.html) — 版本兼容性说明
- [Add-ons Preferences](https://docs.blender.org/manual/en/latest/editors/preferences/addons.html) — 插件管理说明

### 项目文档

- `开发文档/开发计划.md` — 功能设计详解
- `开发文档/架构设计.md` — 模块化重构方案
- `开发文档/Blender配置导出风险分析.html` — 可视化风险分析

### 社区问答精选

| 问题 | 来源 | 关键答案 |
|------|------|---------|
| 多机同步配置 | [Blender StackExchange](https://blender.stackexchange.com/questions/120944) | 使用 Blender Cloud Sync 或手动复制 userpref.blend |
| 备份插件偏好 | [Blender Artists](https://blenderartists.org/t/how-to-backup-addon-preferences-without-blender-sync) | 复制 config 文件夹 |
| Python 保存偏好 | [Blender StackExchange](https://blender.stackexchange.com/questions/266390) | `bpy.ops.wm.save_userpref()` 或底层 API |
| 版本迁移插件 | [Blender StackExchange](https://blender.stackexchange.com/questions/320742) | 复制 scripts/addons 文件夹 |
| portable 模式 | [Blender Manual](https://docs.blender.org/manual/en/latest/advanced/blender_directory_layout.html) | 创建 portable 文件夹 |

### 视频教程

- [Backup and Restore Blender Startup Settings](https://www.youtube.com/watch?v=PnzYZXbbjDk)
- [Blender Addon Migration & File System Tips](https://www.youtube.com/watch?v=ZyQJ7Nmcq8Q)
- [Update Your Blender to 4.4 and Migrate Settings](https://www.youtube.com/watch?v=ebHrQrfrbOI)

### 工具与项目

- [blender-addon-updater](https://github.com/CGCookie/blender-addon-updater) — 插件自动更新模块
- [Blender Sync](https://studio.blender.org/blog/introducing-blender-sync) — Blender Cloud 配置同步服务

---

## 十、Python API 详细参考

### 配置目录 API

```python
import bpy
from pathlib import Path

# ===== 获取各类型资源目录 =====

# 配置目录（userpref.blend、startup.blend 所在位置）
config_dir = Path(bpy.utils.user_resource('CONFIG'))

# 脚本目录（addons/presets/startup 的父目录）
scripts_dir = Path(bpy.utils.user_resource('SCRIPTS'))

# 数据文件目录（笔刷、贴图、资产）
datafiles_dir = Path(bpy.utils.user_resource('DATAFILES'))

# 工作室灯光目录
studiolights_dir = Path(bpy.utils.user_resource('STUDIO_LIGHTS'))

# 扩展目录（Blender 4.5+）
extensions_dir = Path(bpy.utils.user_resource('EXTENSIONS'))

# ===== 组合路径获取 =====

# 直接获取插件目录
addons_dir = Path(bpy.utils.user_resource('SCRIPTS', path='addons', create=True))

# 直接获取预设目录
presets_dir = Path(bpy.utils.user_resource('SCRIPTS', path='presets', create=True))

# 直接获取启动脚本目录
startup_dir = Path(bpy.utils.user_resource('SCRIPTS', path='startup', create=True))
```

### 关键文件路径

```python
# userpref.blend 路径
userpref_path = config_dir / 'userpref.blend'

# startup.blend 路径
startup_path = config_dir / 'startup.blend'

# 书签文件
bookmarks_path = config_dir / 'bookmarks.txt'

# 最近文件列表
recent_files_path = config_dir / 'recent-files.txt'
```

### 偏好设置操作

```python
# ===== 保存用户偏好 =====

# 方法 1: 使用 Operator（最常用）
bpy.ops.wm.save_userpref()

# 方法 2: 设置自动保存标志
bpy.context.preferences.use_preferences_save = True
# 之后每次修改偏好都会自动保存

# ===== 获取/设置偏好设置 =====

# 获取偏好对象
prefs = bpy.context.preferences

# 系统偏好
system_prefs = prefs.system
# 例: 设置 GPU 渲染
system_prefs.gpu_render_stereo = True

# 视图偏好
view_prefs = prefs.view
# 例: 设置界面缩放
view_prefs.ui_scale = 1.2

# 文件路径偏好
filepaths_prefs = prefs.filepaths
# 例: 设置渲染输出路径
filepaths_prefs.render_output_directory = "//render/"

# 编辑偏好
edit_prefs = prefs.edit
# 例: 设置撤销步数
edit_prefs.undo_steps = 64
```

### 插件状态管理

```python
# ===== 获取已启用的插件列表 =====

enabled_addons = []
for addon_name, addon_prefs in bpy.context.preferences.addons.items():
    enabled_addons.append(addon_name)

# ===== 检查插件是否启用 =====

def is_addon_enabled(module_name: str) -> bool:
    return module_name in bpy.context.preferences.addons

# ===== 启用/禁用插件 =====

# 启用插件
addon_utils.enable(module_name, default_set=True)

# 禁用插件
addon_utils.disable(module_name, default_set=True)

# 注意: addon_utils 需要导入
# import addon_utils
# addon_utils 模块位于 bpy.utils._addon_utils
```

### 快捷键操作

```python
# ===== 导出快捷键 =====

# 界面操作: Edit > Preferences > Keymap > Export
# Python 调用 Operator
bpy.ops.preferences.keyconfig_export(filepath="keymap.py")

# ===== 导入快捷键 =====

bpy.ops.preferences.keyconfig_import(filepath="keymap.py")

# ===== 获取当前快捷键配置 =====

keyconfigs = bpy.context.window_manager.keyconfigs
active_keyconfig = keyconfigs.active

# 用户自定义快捷键
user_keymap = keyconfigs.user
```

### 预设管理

```python
# ===== 预设目录结构 =====

# 渲染预设
render_presets = presets_dir / 'render'

# 修改器预设
modifier_presets = presets_dir / 'modifier'

# 操作预设
operator_presets = presets_dir / 'operator'

# ===== 预设文件格式 =====

# 预设是 Python 脚本，格式示例 (render_preset.py):
"""
preset_name = "My Render Preset"
preset_class = "RENDER_PT_presets"

def apply_preset():
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.render.resolution_x = 1920
    bpy.context.scene.render.resolution_y = 1080
"""
```

### 完整导出示例

```python
import bpy
import json
import shutil
import zipfile
from pathlib import Path
from datetime import datetime

def export_blender_config(dest_dir: Path):
    """完整的 Blender 配置导出"""

    # 1. 确保偏好已保存
    bpy.ops.wm.save_userpref()

    # 2. 获取各目录路径
    config_dir = Path(bpy.utils.user_resource('CONFIG'))
    addons_dir = Path(bpy.utils.user_resource('SCRIPTS')) / 'addons'
    presets_dir = Path(bpy.utils.user_resource('SCRIPTS')) / 'presets'
    datafiles_dir = Path(bpy.utils.user_resource('DATAFILES'))

    # 3. 创建备份文件名
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_path = dest_dir / f'blender_config_{timestamp}.zip'

    # 4. 打包配置
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # userpref.blend
        userpref = config_dir / 'userpref.blend'
        if userpref.exists():
            zf.write(userpref, 'config/userpref.blend')

        # startup.blend
        startup = config_dir / 'startup.blend'
        if startup.exists():
            zf.write(startup, 'config/startup.blend')

        # addons 目录
        if addons_dir.exists():
            for item in addons_dir.rglob('*'):
                if item.is_file() and '__pycache__' not in str(item):
                    zf.write(item, f'addons/{item.relative_to(addons_dir)}')

        # presets 目录
        if presets_dir.exists():
            for item in presets_dir.rglob('*'):
                if item.is_file():
                    zf.write(item, f'presets/{item.relative_to(presets_dir)}')

        # 5. 写入 manifest
        manifest = {
            'blender_version': '.'.join(str(v) for v in bpy.app.version),
            'created_at': datetime.now().isoformat(),
            'includes': ['userpref', 'startup', 'addons', 'presets'],
            'enabled_addons': list(bpy.context.preferences.addons.keys())
        }
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))

    return zip_path

# 使用示例
# export_path = export_blender_config(Path.home() / 'blender_backups')
```

---

*本文档基于 MMY Blender Configure 项目实践经验整理*