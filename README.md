# MMY Blender Configure

Blender 配置备份、导入、导出及插件耗时监控插件。

## 功能

- **备份配置**：将当前 Blender 配置打包为 zip，支持选择性备份
- **导入配置**：从 zip 还原配置，导入前自动临时备份防止误操作
- **导出配置**：复用备份逻辑，目标路径由用户指定
- **插件耗时监控**：面板显示各插件加载耗时，异常插件标红提示

## 支持的配置类型

| 类型 | 说明 | 包含内容 |
|------|------|----------|
| 用户快捷键 | keymap | 自定义快捷键配置 |
| 软件设置 | prefs | 界面偏好、主题等 |
| 插件 | addons | 用户安装的插件 |
| 用户配置 | config | bookmarks、recent-files、searches 等 |
| 预设 | presets | 用户自定义预设 |
| 启动脚本 | startup | 启动时自动执行的脚本 |
| 数据文件 | datafiles | 笔刷、灯光预设、扩展包等 |

## 安装

1. 将 `MMY-Blender-Configure` 文件夹复制到 Blender 的 `scripts/addons/` 目录
2. 在 Blender 偏好设置中启用插件
3. 顶部菜单栏最左侧出现按钮即可使用

## 使用

点击顶部菜单栏左侧的 📄 按钮，弹出配置管理面板：

1. 勾选需要备份/导入的配置类型
2. 点击 **备份** 保存到默认路径，或 **导出** 指定路径
3. 点击 **导入** 选择 zip 文件还原配置
4. 导入后需 **重启 Blender** 生效

## 版本兼容性

导入时会检查备份文件的 Blender 版本。主版本号不同时弹出警告，但仍可继续导入。

## 技术

- 备份格式：zip + manifest.json
- 耗时采集：Monkey-patch `bpy.utils._addon_utils.enable` + `bpy.app.timers` 兜底扫描
- 兼容 Blender 4.5+

## 开发

多文件包结构：

```
MMY-Blender-Configure/
├── __init__.py          # 插件入口
── operators.py         # 备份/导入/导出 Operator
├── ui.py                # Header 按钮 + Popover 面板
├── addon_timer.py       # 插件耗时采集
├── preferences.py       # 偏好设置
└── utils.py             # 路径工具、zip 打包/解包
```

## 作者

会叫喵的鱼
