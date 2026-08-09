# Blender 5.x 跨版本配置一键迁移方案

> 首次制定：2026-08-10
> 实现版本：MMY Blender Configure 1.2.0
> 状态：已实现，等待 Windows Blender 实机验收

## 一、目标与边界

本功能面向同一台 Windows 电脑上的 Blender 小版本升级，解决 5.1 配置迁移到 5.2、5.3 时需要重复复制快捷键、插件和偏好的问题。

目标：

- 从旧版 Blender 选择目标 `blender.exe` 后完成一键迁移。
- 支持普通安装、Portable，以及两种模式之间互迁。
- 支持将一份配置快照重复迁移到多个更高的同主版本。
- 目标已有配置先备份，迁移失败自动回滚。
- 在目标版本真实加载插件，失败插件自动禁用并报告。

第一版不处理：

- 多台电脑或云端持续同步。
- 多个 Blender 版本实时共用同一用户目录。
- 降级迁移和跨主版本自动迁移。
- 自动联网更新、下载或替换第三方插件。
- 使用 `BLENDER_USER_CONFIG`、`BLENDER_USER_SCRIPTS` 等变量拆分到用户根目录之外的目标环境。

## 二、关键设计决策

### 2.1 不实时共用 userpref.blend

Blender 每个版本使用独立用户目录。多个版本同时读写同一个 `userpref.blend` 会产生版本转换、字段覆盖和并发写入风险，因此迁移采用“不可变快照 + 独立目标目录”，不创建符号链接。

### 2.2 由目标 Blender 转换配置

插件不解析 `.blend` 二进制格式。配置文件安装到目标用户目录后，启动目标 Blender 后台进程，让目标版本自行读取和保存 `userpref.blend`、`startup.blend`。

目标路径由以下探针实时返回，不按安装文件夹名称猜测：

```python
bpy.app.version
bpy.utils.resource_path("USER")
bpy.utils.user_resource("CONFIG")
bpy.utils.user_resource("SCRIPTS")
bpy.utils.user_resource("DATAFILES")
bpy.utils.user_resource("EXTENSIONS")
```

### 2.3 快捷键双重保障

`userpref.blend` 仍是偏好和快捷键的主载体。同时调用 `preferences.keyconfig_export` 导出 `keymap.py`，并保存用户 Keymap 条目指纹。目标审计比较指纹，`keymap.py` 留作人工恢复兜底。

### 2.4 插件失败按严重程度处理

- Blender 能继续启动：使用官方 `addon_utils` 状态检查并禁用失败插件，迁移状态为 `degraded`。
- Blender 后台进程崩溃、超时或无法保存偏好：无法安全定位单个插件，回滚整个目标用户目录。

## 三、用户流程

### 3.1 当前环境直接迁移

1. 打开顶部栏“配置管理 > 迁移到新版”。
2. 配置来源选择“当前 Blender”。
3. 选择目标 `blender.exe`；插件会扫描常见安装目录并提供建议。
4. 选择输出目录和高级组件。
5. 插件保存当前偏好、导出 Keymap、创建快照、备份目标并执行迁移。
6. 迁移完成后打开报告检查插件、快捷键和路径状态。

### 3.2 使用已有快照

1. 配置来源选择“已有配置包”。
2. 选择 `MMY_Blender_Profile_*.zip` 和目标 `blender.exe`。
3. 清单中的来源版本必须与目标满足“同主版本、正向升级”。
4. 同一份 5.1 快照可以分别迁移到 5.2、5.3。

### 3.3 手动恢复

1. 打开“配置管理 > 恢复迁移前配置”。
2. 选择迁移目录中的 `recovery.json`。
3. 插件先备份目标当前状态，再恢复 `target_before.zip`。

## 四、配置快照格式

文件名：

```text
MMY_Blender_Profile_v{来源版本}_{YYYYMMDD_HHMMSS}.zip
```

目录：

```text
manifest.json
payload/config/userpref.blend
payload/config/startup.blend
payload/scripts/addons/
payload/scripts/presets/
payload/scripts/startup/              # 高级选项，默认关闭
payload/datafiles/                    # 高级选项，默认关闭
payload/extensions/
payload/config/bookmarks.txt          # 高级选项，默认关闭
payload/config/recent-files.txt       # 高级选项，默认关闭
fallback/keymap.py
fallback/keymap_fingerprint.json
```

`manifest.json` schema 版本为 2，主要字段：

```json
{
  "schema_version": 2,
  "source": {
    "blender_version": [5, 1, 0],
    "platform": "win32",
    "install_mode": "normal"
  },
  "target_policy": {
    "forward_only": true,
    "same_major_only": true
  },
  "components": [],
  "addons": [],
  "keymap": {
    "item_count": 0,
    "fingerprint_file": "fallback/keymap_fingerprint.json",
    "export_file": "fallback/keymap.py"
  },
  "files": [
    {
      "path": "payload/config/userpref.blend",
      "size": 0,
      "sha256": "...",
      "component": "preferences"
    }
  ]
}
```

插件清单只记录模块名、类型、版本、启用状态和扩展兼容版本范围，不读取插件偏好值或仓库凭据。

## 五、事务式迁移

### 5.1 预检

- 目标文件必须是存在的 `blender.exe`。
- 目标 Blender 不能正在运行，Windows 通过 CIM 进程路径确认。
- 目标与来源主版本必须相同，目标完整版本必须更高。
- 来源和目标用户根目录不能相同。
- 输出目录不得位于来源或目标用户目录内。
- 目标的 config/scripts/datafiles/extensions 必须位于同一个用户根目录下。
- 输出盘需要容纳目标完整备份，目标盘需要容纳 staging 配置并保留安全余量。

### 5.2 安装过程

```text
目标探针
  -> 创建或校验来源快照
  -> 完整备份目标到外部 recovery 目录
  -> 安全解压到目标同盘 staging 目录
  -> 校验路径、大小、SHA-256
  -> 目标目录原子改名为 mmy_old
  -> staging 原子切换为目标目录
  -> 启动目标 Blender 后台审计
  -> 成功后删除 mmy_old，保留 ZIP 恢复包
```

解压只接受清单中声明的 `payload/` 和 `fallback/` 文件，拒绝：

- 绝对路径、`..`、反斜杠路径和盘符路径。
- ZIP 符号链接。
- 重复条目、清单外条目、大小不一致和 SHA-256 不一致。

### 5.3 回滚

目标验证失败时：

1. 将失败目标目录原子改名为隐藏的 `mmy_failed` 目录，保留排查现场。
2. 将 `mmy_old` 原子恢复为目标目录。
3. `recovery.json` 状态写为 `rolled_back`。
4. 保留 `target_before.zip`、目标日志和错误信息。

## 六、目标审计

后台进程通过 `MMY_MIGRATION_AUDIT=1` 启动。本插件在该模式下只注册偏好、迁移 Operator 和 UI，不启动每周自动打包、启动计时和延迟扫描。

审计项目：

- 目标实际用户目录是否与探针一致。
- 来源/目标版本关系是否仍然有效。
- 来源已启用插件在目标中的加载状态。
- 扩展 `blender_version_min/max` 是否允许目标版本。
- 来源用户 Keymap 指纹是否完整存在。
- Keymap 对应的 Operator 是否存在。
- Blender 文件路径和插件 FILE_PATH/DIR_PATH 属性指向的绝对路径是否存在。
- `bpy.ops.wm.save_userpref()` 是否成功。

结果状态：

| 状态 | 含义 |
|------|------|
| `success` | 配置读取、保存、插件和快捷键审计通过 |
| `degraded` | 迁移可用，但存在自动禁用插件或快捷键缺失 |
| `failed` | 安装前或回滚过程失败 |
| `rolled_back` | 目标验证失败，已恢复迁移前配置 |

## 七、接口与模块

Operator：

| bl_idname | 职责 |
|-----------|------|
| `mmy.migrate_to_blender` | 从当前环境或已有配置包迁移到目标版本 |
| `mmy.export_migration_profile` | 只生成可重复使用的配置快照 |
| `mmy.restore_migration_backup` | 根据 `recovery.json` 恢复目标 |
| `mmy.open_migration_report` | 打开最近迁移报告目录 |

模块：

| 模块 | 职责 |
|------|------|
| `migration.py` | Blender 状态采集、Operator、异步任务和偏好回写 |
| `migration_core.py` | 纯 Python 快照、校验、探针、事务和恢复 |
| `migration_worker.py` | 目标 Blender 中的插件、Keymap 和路径审计 |

## 八、测试与验收

自动测试使用标准库 `unittest`，不依赖 `bpy`：

```bash
python3 -m unittest discover -s tests -v
```

已覆盖：

- 5.1→5.3 版本规则和已有快照直达迁移。
- 默认组件、可选数据文件和缓存排除。
- ZIP 路径穿越、重复条目、内容篡改和 SHA-256。
- 中文与空格路径。
- 磁盘空间不足和输出目录越界。
- 原子安装、失败回滚、完整备份和手动恢复。
- 自定义目标资源目录拒绝和结构化探针解析。

Windows Blender 实机验收矩阵：

| 来源 | 目标 | 组合 |
|------|------|------|
| 5.1 普通 | 5.2 普通 | 普通→普通 |
| 5.1 Portable | 5.2 Portable | Portable→Portable |
| 5.1 普通 | 5.2 Portable | 普通→Portable |
| 5.1 Portable | 5.2 普通 | Portable→普通 |
| 5.1 快照 | 5.3 任一模式 | 快照直达 |
| 5.2 任一模式 | 5.3 任一模式 | 逐级升级 |

每组需验证主题和偏好、快捷键、启动工作区、传统插件、扩展、预设、失败插件禁用、崩溃回滚及再次手动恢复。

## 九、安全说明

- 快照和恢复包未加密，可能包含用户名、路径和插件配置，应按敏感本地文件管理。
- 启动脚本可能执行任意 Python，默认不迁移。
- 配置文件不会上传，插件不会自动联网安装或更新。
- 第三方插件兼容性由插件作者决定，本功能只负责检测、隔离、报告和回滚。

## 十、参考依据

- Blender 5.2 Manual：用户目录按版本隔离，Portable 将偏好、启动文件、扩展和预设保存在 `portable` 目录。
- Blender 5.2 Manual：首次启动可导入上一版本的偏好、启动文件、插件和扩展，同时明确提醒旧插件可能不兼容。
- Blender 官方源码：`preferences.copy_prev` 复制上一版本用户目录后调用 `wm.read_userpref()`；`preferences.keyconfig_export` 可独立导出 Keymap。
