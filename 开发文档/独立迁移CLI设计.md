# 独立迁移 CLI（migrate.py）设计

> 版本：v1.6.0 规划 | 分支：`feature/standalone-migrate-cli` | 依赖：PR #11（工作区级布局恢复）
> 状态：待评审

## 1. 背景与问题

v1.4 迁移体系的执行链（`migration_core.execute_existing_profile_migration_multi`）
是纯 Python（不 import bpy），通过 `blender.exe --background --python migration_worker.py`
把审计脚本按文件路径注入目标 Blender 执行——**worker 不依赖插件注册成功**。
插件 UI 只是这条链路的触发器之一。

因此存在结构性死锁：全新便携版 Blender 上没有任何插件，
用户想导入配置包，必须先安装插件才能点"导入"。
而安装插件本身需要：下载插件包 → 打开目标 Blender → 偏好设置 → 安装 → 启用，
对"只想把配置迁过去"的场景是多余的门槛。

结论：把触发器从插件 UI 解耦出来，做一个仓库级的独立脚本 `migrate.py`。

## 2. 方案

### 2.1 定位

`migrate.py` 与 `pack.py` 平级，同为**独立 Python 脚本**（仅标准库 + tkinter，
不依赖 Blender 环境）。pack.py 负责"配置 → 配置包"，migrate.py 负责
"配置包 → 任意目标 Blender"，形成免插件的打包-迁移闭环。

### 2.2 输入输出

```
输入：  迁移配置包 .zip（插件"一键导出"产物，schema_version=2）
       目标 blender.exe
输出：  目标 portable 用户目录被替换（带备份/回滚/审计报告，与插件内导入完全一致）
副产物：recovery/（回滚备份）与 migration_report.html 落在配置包所在目录
```

### 2.3 两种模式

| 模式 | 用法 | 场景 |
|---|---|---|
| 图形模式 | `python migrate.py`（双击） | 目标机器零命令行经验 |
| 命令行模式 | `python migrate.py <profile.zip> <blender.exe>` | 脚本化/CI |

图形模式流程：选配置包 → 选 blender.exe → **后台探针预检** → 确认摘要弹窗
（实测版本/实测配置目录/便携模式检测/预计不兼容插件数/自定义工作区数）→ 执行 →
结果弹窗（含配置写入路径、报告路径、回滚路径）。

### 2.3.1 预检（preflight_target）

迁移前启动一次目标 Blender 后台探针，拿到**实测**信息（不靠目录名猜）：

- 真实版本 → 版本策略（同主版本 + 向前-only）提前拦截，避免走完流程才失败
- 真实 `user_root` → **便携模式检测**：`user_root` 是否等于 exe 同级 `portable/`
- 目标是否正在运行（提前提示关闭）
- 按 manifest 预测不兼容插件（确认页列名）

**便携模式检测是全新便携版场景的头号坑**：Blender 官方判定条件是
「exe 所在目录存在名为 `portable` 的文件夹」。全新解压的官方 zip 没有该目录，
此时迁移会"成功"但配置写进 `%APPDATA%\Blender Foundation\Blender\x.y`——
对便携用户毫无意义且难以察觉。预检发现非便携模式时，确认页给出明确
警告 + 修复指引（新建 `portable` 文件夹后重跑）。

### 2.4 核心调用

```python
migration_core.execute_existing_profile_migration_multi(
    profile_path=配置包,
    target_executables=[blender.exe],
    output_dir=配置包所在目录,          # recovery/报告放旁边，天然在目标用户目录之外
    current_user_root=[],               # CLI 场景无源用户目录，见 §3 限制
    worker_script=mmy_pack_config/migration_worker.py,   # 随仓库分发
    current_pid=os.getpid(),
)
```

迁移语义（向前-only、donor 工作区恢复、禁用不兼容插件、失败回滚）
全部复用现有核心，**不实现第二套迁移逻辑**。

### 2.5 输入校验（纯函数，可单测）

`validate_profile(path)`：
- 必须是可读的 zip
- 必须含 `manifest.json` 且 `schema_version == 2`（与 SCHEMA_VERSION 对齐）
- 必须含 `source.blender_version` / `source.platform == win32`
- 明确拒绝 pack.py 便携备份（`schema_version=1, type=portable`）并给出指引文案

`describe_profile(manifest)` → 确认弹窗用的人类可读摘要。

`detect_target_version(exe_path)` → 从 exe 上级目录名解析版本（如
`C:\BlenderAPP\Blender5.1\blender.exe` → `5.1`），仅用于确认摘要展示，
真实版本以目标探针为准。

## 3. 防护与限制

- 目标正在运行 → 预检阶段即提示关闭（核心层另有 `windows_executable_is_running` 兜底）
- 配置包放在目标 portable 目录内 → 核心层已有 `path_is_within` 检查
- **目标未启用便携模式** → 预检检出并在确认页拦截式警告（见 §2.3.1）
- 版本策略非法（跨主版本 / 目标不高于源）→ 预检提前报错，流程不空跑
- **自我迁移限制**：插件内导入用 `current_user_root` 防止"在同一台机器的同一
  Blender 上导入自己导出的包"。CLI 不知道配置包来自哪台机器，该保护失效——
  若用户在本机对来源 Blender 执行导入，会覆盖其当前配置（有备份可回滚，
  但属于误操作）。缓解：确认弹窗明示"请确认目标不是配置包的来源 Blender"。
- 回滚：与插件内导入同一 recovery 机制（原子交换 + 恢复元数据 + restore_recovery）

## 4. 验证

- 单测：validate_profile 六分支（合法配置包/pack.py 备份/垃圾文件/缺文件/
  非 win32）、describe_profile 摘要（含预检展示/非便携警告/不兼容插件列举）、
  detect_target_version、is_portable_user_root、version_string
- 真机：Blender 5.1 无头只读探针（run_target_probe）确认 user_root 探测；
  preflight_target 真机跑通，实测识别 `portable=True`
- 全链路写入验证（真实执行迁移）不在本机执行——需隔离的测试 Blender，
  合并后由用户验证

## 5. 不做的事

- 不做导出（导出仍需在源 Blender 插件内完成，采集需要 bpy）
- 不支持 pack.py 便携备份直接导入（那是整目录拷贝场景，schema 不同；
  报错文案引导用户改用整目录拷贝或先在源机器用插件导出配置包）
- 不做多目标（CLI 第一版单目标够用，核心层本就支持多目标，未来可加）
