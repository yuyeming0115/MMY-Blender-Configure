# MMY Blender Configure

Blender 跨版本配置迁移、Portable 配置打包及插件加载耗时监控工具。

## 功能

- **跨版本一键迁移**：将当前 Blender 的偏好、快捷键、启动文件、插件、扩展和预设迁移到更高的同主版本。
- **配置快照复用**：导出带清单和 SHA-256 校验的 ZIP，同一份 5.1 快照可分别迁移到 5.2、5.3。
- **事务式恢复**：覆盖目标前完整备份，验证失败自动回滚，也可稍后手动恢复。
- **插件兼容审计**：在目标 Blender 中实际加载插件；失败插件自动禁用并写入报告。
- **Portable 打包**：保留原有 Portable 目录打包和每周自动打包功能。
- **加载耗时监控**：显示插件启动耗时、错误和重测结果。

## 安装

1. 下载发布包 ZIP。
2. 打开 Blender 的“编辑 > 偏好设置 > 插件”。
3. 选择发布包并启用 `MMY Blender Configure`。
4. 顶部栏左侧会出现“配置管理”。

插件兼容 Blender 4.5 及以上版本。跨版本一键迁移第一版仅支持 Windows；普通安装和 Portable 均可作为来源或目标。

## 跨版本迁移

1. 在旧版 Blender 中打开“配置管理 > 迁移到新版”。
2. 选择配置来源：当前 Blender，或之前导出的配置包。
3. 选择目标版本的 `blender.exe`。插件会自动建议常见安装目录中的版本。
4. 确认输出目录和可选组件，开始迁移。
5. 完成后通过“打开报告”查看被禁用插件、快捷键差异和失效路径。

迁移规则：

- 仅允许同主版本正向迁移，例如 5.1→5.2、5.1→5.3、5.2→5.3。
- 禁止降级和跨主版本自动迁移。
- 来源与目标使用独立用户目录，不通过符号链接或环境变量实时共用配置。
- 目标已有配置会先备份，再整体替换。
- 一般插件加载失败会自动禁用；若插件导致目标 Blender 整体崩溃，则自动回滚整个目标配置。

## 快照内容

默认包含：

- `config/userpref.blend`：偏好、快捷键、插件偏好及启用状态。
- `config/startup.blend`：启动场景和工作区。
- `scripts/addons/`：传统插件。
- `extensions/`：Blender 扩展及仓库内容。
- `scripts/presets/`：用户预设。
- 独立导出的 `keymap.py` 和快捷键指纹。

默认排除缓存、日志、最近文件、书签、启动脚本和大型数据文件。启动脚本、历史记录和数据文件可在迁移窗口中单独启用。

快照格式：

```text
MMY_Blender_Profile_v5.1.0_YYYYMMDD_HHMMSS.zip
├── manifest.json
├── payload/
│   ├── config/
│   ├── scripts/
│   ├── datafiles/
│   └── extensions/
└── fallback/
    ├── keymap.py
    └── keymap_fingerprint.json
```

`manifest.json` 记录 schema 版本、来源环境、组件、插件清单和逐文件 SHA-256。配置包未加密，应作为包含用户配置的敏感本地文件保管。

## 恢复

迁移记录默认位于配置包输出目录下：

```text
MMY_Migration_Recovery/
└── 5.1.0_to_5.2.0_<运行编号>/
    ├── recovery.json
    ├── target_before.zip
    ├── migration_report.json
    ├── target_blender.log
    └── fallback/
```

使用“配置管理 > 恢复迁移前配置”，选择对应的 `recovery.json`。恢复前还会再次备份目标当前状态。

## Portable 打包

打开“配置管理 > 打包 Portable 配置”，选择 `portable` 文件夹和输出目录。默认排除缓存、日志、临时文件和 Blender 自动备份文件。

## 开发

核心模块：

```text
mmy_pack_config/
├── migration.py          # Blender Operator、会话状态采集、异步任务
├── migration_core.py     # 快照、校验、目标探测、事务切换与恢复
├── migration_worker.py   # 目标 Blender 后台兼容审计
├── ui.py                 # 顶部配置管理入口与 Portable 打包
├── preferences.py        # 插件设置与耗时监控界面
└── addon_timer.py        # 插件加载耗时采集
```

运行纯 Python 测试：

```bash
python3 -m unittest discover -s tests -v
```

详细设计见 `开发文档/Blender跨版本配置一键迁移方案.md`。

## 作者

会叫喵的鱼
