# MMY GUI 工具设计（迁移 + 打包）

> 目标：把 `migrate.py` / `pack.py` 从「黑窗口 + 对话框串联」升级为**小白可用的可视化工具**。
> 交付形态：先脚本（`.py` + `.bat` 双击），验证稳定后再用 PyInstaller 打包 exe。

---

## 1. 问题定义：用户说的「不直白」到底是什么

现有 GUI（`migrate.py::run_tkinter_ui`、`pack.py::_run_tkinter_ui`）的形态是
**纯对话框串联**：

```
双击 run_migrate.bat → 黑窗口 →「选择配置包」对话框 →「选择 blender.exe」
→「确认迁移」是/否 → 黑窗口干等几十秒 →「迁移完成」弹窗
```

实测痛点（按严重度排序）：

| # | 痛点 | 真实后果 |
|---|------|----------|
| 1 | **迁移在主线程跑，窗口无响应** | 只能看黑窗口，不知道在干什么、有没有卡住 |
| 2 | **失败信息藏在弹窗里** | 用户实测「没报错」，实为弹了 `WinError 17` 但没人读技术错误 |
| 3 | 过程零反馈 | 解包 372MB + 目录交换 + 启动目标 Blender 审计，全程无进度 |
| 4 | 只有对话框，没有全局视图 | 选错了要重头再来，无法一眼看出「现在是什么状态」 |

> **设计结论**：拖拽只是入口形态的优化，**真正解决「不直白」的是「全程可见 + 结果明确」**。
> 因此本设计把「阶段进度 + 实时日志 + 结果卡片」作为内核能力，拖拽作为锦上添花。

---

## 2. 方案选型

| 维度 | 方案 A：纯对话框（现状） | 方案 B：单窗口可视化 + 拖拽（本设计） |
|------|------------------------|-----------------------------------|
| 过程可见性 | ❌ 黑窗口 | ✅ 阶段进度条 + 实时日志 |
| 结果明确性 | ⚠️ 一个弹窗 | ✅ 结果卡片 + 快捷打开报告/目录 |
| 输入方式 | 对话框 | 拖拽 **或** 点击（双通道） |
| GUI 线程 | ❌ 主线程阻塞 | ✅ 子线程 + 队列回传 |
| 依赖 | tkinter | tkinter（必需）+ tkinterdnd2（可选，缺失自动降级） |

**选定方案 B。**

### 拖拽依赖的风险与对策

`tkinterdnd2` 是第三方包。已实测本机可用（`package require tkdnd` 成功），
但**不能假设所有用户的机器都装了**，因此：

| 环境 | 行为 |
|------|------|
| 有 tkinterdnd2 | 投放区可拖入，也可点击选择 |
| 无 tkinterdnd2 | **自动隐藏拖拽提示**，投放区退化为「点击选择」，功能完整不缺失 |

绝不允许「因为缺拖拽库就双击没反应」。

---

## 3. 架构

```
gui_logic.py              纯逻辑，零第三方依赖（关键：可被无 tkinter 的解释器测试）
├─ parse_drop_paths()     拖拽数据 → 路径列表（同时吃 str 与 splitlist 的 list）
├─ first_matching_path()  路径筛选（目录/文件/存在性）
└─ explain_error()        技术错误 → 小白能懂的「原因 + 怎么办」

gui_kit.py                公共 UI 组件（两个标签页共用，依赖 tkinter）
├─ DND_AVAILABLE          tkinterdnd2 可用性探测（模块级常量）
├─ make_root()            按可用性返回 TkinterDnD.Tk() 或 tk.Tk()
├─ ScrollPane             带滚动条的内容容器（保证小屏也看得到结果卡片）
├─ DropZone               投放区：拖拽 + 点击双通道（整棵子树注册 drop）
├─ StageProgress          阶段进度：步骤列表 + 当前步高亮 + 可选百分比
├─ LogPanel               日志面板：stdout 重定向（_StdoutTee 逐行缓冲）
├─ ResultCard             结果卡片：成功/失败 + 打开报告/目录按钮 + 完成弹窗
├─ TaskRunner             子线程 + queue + root.after 轮询（GUI 不阻塞）
└─ _StdoutTee             逐行缓冲的 stdout 替身（兜住 pythonw 的 None 流）

mmy_tool_gui.py           单一GUI 入口，双标签页
├─ ToolApp                双页签主窗口（每个页签独立滚动容器 + 独立线程）
├─ MigratePage            「配置迁移」页（双投放区）
└─ PackPage               「配置打包」页（单投放区）
run_mmy_tool.bat          唯一双击启动器（解释器探测 + tkinterdnd2 自检）
```

**为什么合并成单文件**：迁移和打包是同一件事的两端（导出 → 迁移），
用户心智里是一个工具，不该是两个 .py + 两个 .bat。合并后文件数从
7 个降到 4 个（gui_logic / gui_kit / mmy_tool_gui / run_mmy_tool.bat），
且两侧共用同一套组件与滚动逻辑，视觉与行为天然一致。

**为什么把逻辑与 UI 拆开**：`tkinter` 并非所有 Python 都有（本机 PATH 首个
解释器就没有）。逻辑与 UI 同文件会导致「无 tkinter 环境连纯函数都测不了」，
拆分后 `tests/test_gui_logic.py` 在任意解释器下可跑，GUI 冒烟单独用
`tests/smoke_gui.py` + 带 tkinter 的解释器执行。

**为什么共用 `gui_kit`**：两个工具必须视觉一致（同一套字体/间距/配色/组件），
共用组件是唯一可靠做法；同时保证后续加第三个工具时零成本复用。

---

## 4. 界面线框

### 4.1 迁移 GUI

```
┌──────────────────────────────────────────────────────────────┐
│  MMY 迁移工具                                     [标题栏]   │
├──────────────────────────────────────────────────────────────┤
│  ① 把文件拖到下面，或点击选择                                  │
│  ┌────────────────────────┐  ┌────────────────────────┐    │
│  │  配置包 *.zip           │  │  blender.exe           │    │
│  │  E:\MMY_Backup_...zip  │  │  C:\BlenderAPP\...\     │    │
│  │        [选择文件]       │  │        [选择文件]       │    │
│  └────────────────────────┘  └────────────────────────┘    │
├──────────────────────────────────────────────────────────────┤
│  ② 确认信息                                                 │
│    源版本 5.1  →  目标版本 5.2.1（实测）                      │
│    已启用插件 63 个 · 自定义工作区 2 个                        │
│    配置写入 C:\BlenderAPP\Blender5.2\portable  ✓ 便携模式    │
│                          [ 开始导入 ]                        │
├──────────────────────────────────────────────────────────────┤
│  ③ 进度                                                     │
│    ✔ 解析配置包                                             │
│    ✔ 检测目标 Blender                                       │
│    ✔ 备份目标现有配置                                        │
│    ⏳ 安装配置（37%）                                         │
│    ○ 目标审计（启动 Blender 校验）                            │
│    ○ 生成报告                                               │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 日志区（滚动，自动滚到底）                                 │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  ④ 结果   ✅ 迁移成功                                        │
│     目标 Blender 5.2.1 · 恢复工作区 XYZ-Shading · 快捷键丢失 0 │
│     [打开报告] [打开配置目录] [重新开始]                      │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 打包 GUI

结构一致，仅输入区不同：投放区是 **portable 文件夹**（拖文件夹），
输出路径 + 「压缩体积」勾选，阶段为「扫描 → 压缩 → 写 manifest → 完成」。

---

## 5. 线程模型（关键）

迁移/打包都是**长任务**，必须离开主线程：

```python
work(stage, log) -> result          # 在子线程执行
        │ stage(key, msg, ratio)     # 上报阶段
        │ log(msg)                   # 上报日志
        └─ 返回结果
                ↓ queue.Queue
        root.after(80, pump)         # 主线程轮询，刷新 UI
```

铁律：

| 规则 | 原因 |
|------|------|
| 所有 UI 更新只在主线程 | tkinter 非线程安全 |
| 子线程只写 `queue.Queue` | 避免竞态 |
| `progress` / `log` 回调**吞掉自身异常** | GUI 崩溃绝不能影响迁移结果 |
| 关闭窗口时若任务在跑 → 询问确认 | 避免半途丢弃导致状态不明 |

### stdout 捕获

核心层现有大量 `print`（`pack_portable` 甚至每 1000 文件报进度）。
在子线程内用 `contextlib.redirect_stdout` 接管，逐行 emit 到日志面板：

```python
class _LineEmitter:          # 逐行缓冲，避免半行刷屏
    def write(self, text): ...    # 累积到 \n 才 emit
    def flush(self): ...
    def isatty(self): return False   # 某些库会检查
```

---

## 6. 核心层 progress 回调契约

```python
progress(stage_key: str, message: str, ratio: float | None = None) -> None
```

- `stage_key`：对应阶段表的 key（如 `install`）
- `ratio`：可选 0~1，用于百分比；未知则传 `None`

改造点（**默认 None，既有调用完全兼容**）：

| 文件 | 函数 | 上报阶段 |
|------|------|----------|
| `migration_core.py` | `_install_profile` / `_install_profile_for_target` | `backup` / `install` / `audit` / `report` |
| `migration_core.py` | `execute_existing_profile_migration_multi` | 透传 `progress` |
| `pack.py` | `pack_portable` | `scan` / `compress`（用已有 `packed_files` 算比例） |
| `pack.py` | `pack_addon` | `compress` |

插件内 UI（`migration.py`）调用时不传 `progress`，行为不变。

---

## 7. 小白友好错误文案

`explain_error(exc)` 把技术错误映射为「原因 + 怎么办」：

| 技术错误 | 展示给用户 |
|----------|-----------|
| 版本不满足同主版本正向迁移 | 源 4.5 不能直接迁到 5.x，请先在源机器升级到 5.x 再导出 |
| 目标不是便携版（配置会写进系统目录） | 目标 Blender 不是便携版，请先在其同级建 `portable` 文件夹 |
| 目标 Blender 正在运行 | 请先关闭目标 Blender 再导入 |
| 配置包不是迁移配置包 | 这是 pack.py 的整目录备份，请直接解压覆盖 portable 文件夹 |
| 磁盘空间不足 | 目标盘空间不足，请清理后重试 |
| 其它 | 原文展示 + recovery 目录路径 + 「复制错误详情」按钮 |

原则：**永远给出下一步动作**，而不是只报错。

---

## 8. 视觉规范

| 项 | 值 | 理由 |
|----|----|------|
| 默认字体 | Microsoft YaHei UI 11pt | 中文明晰 |
| 日志字体 | Consolas 10pt | 等宽对齐 |
| 窗口尺寸 | 960×720 | 容纳日志区且不挡屏 |
| 主色 | `#2d6cdf`（蓝） | 与 Blender 橙 `#ea7600` 区分 |
| 成功 / 失败 | `#1a7f37` / `#cf222e` | 语义色，色盲友好（配合 ✅❌ 图标） |
| 投放区虚线边框 | 2px dashed | 明确「可拖入」暗示 |
| 正文最小字号 | ≥10pt | 视力适配 |

---

## 9. 验证

- 单测 `tests/test_gui_logic.py`（19 例，**无需 tkinter**）：
  - `parse_drop_paths`：普通路径 / 花括号+空格 / 混合 / 空 / 单花括号 / 花括号不配对
  - `first_matching_path`：目录与文件筛选、类型不符返回 None、**不存在路径必须跳过**
  - `explain_error`：六类错误映射、具体优先于宽泛、异常对象入参、原文不丢失
  - `pack_portable` 契约：阶段顺序（scan → compress）、ratio 边界 ∈ [0,1]、
    **回调抛异常不影响 zip 生成**、不传 progress 时不额外预扫描
- 单测 `tests/test_migration_core.py` 新增 2 例：阶段顺序
  `parse → probe → backup → install → audit → report`；回调异常不影响迁移结果
- 真机冒烟 `tests/smoke_gui.py`（需带 tkinter 的解释器）：
  - 两个 GUI 入口在隐藏窗口中完成实例化（含真实 `tkdnd` 注册）
  - `pack_portable` 阶段上报真实触发
- 迁移全链路由用户在测试 Blender 上验证（不自动执行真实迁移）

> 实施中单测抓到过一个真实缺陷：`first_matching_path` 未先判存在性，
> 导致「不存在的路径」会被当成文件放行（`is_dir()` 对不存在路径返回 False）。
> 已修复并保留回归用例。

---

## 10. 已知限制

| 限制 | 说明 |
|------|------|
| 拖拽依赖 `tkinterdnd2` | 缺失时自动降级为点击选择，不影响功能 |
| 日志区不支持搜索 | 大日志（>5000 行）自动滚动，不做虚拟滚动 |
| 迁移不可中途取消 | 长任务中途取消会留下不确定状态，仅支持「关闭前确认」 |
| exe 打包另计 | `migration_core` 是运行时动态加载，PyInstaller 需额外配置，属后续步骤 |

---

## 11. v1.5.2 真机测试反馈与根因（重要）

首版 GUI 交付后用户实测报三个问题。三个都**不是需求没做**，而是各自有
独立的根因，且有两个属于「静默失败」——表现与「程序没反应」完全一样，
最容易被误判成环境问题。

### 11.1 拖拽完全没反应

**根因**：`_on_drop` 里 `self.tk.splitlist(event.data)` 返回的是 **list**，
而`parse_drop_paths()` 只接受 `str`，执行 `re.findall(list)` 直接抛
`TypeError: expected string or bytes-like object, got 'list'`。

**为什么静默**：这个异常发生在 tkinter 事件回调里，tkinter 只往 `stderr`
打一行就继续跑。本工具经 `pythonw.exe` 启动**没有控制台**，`stderr` 无处可去；
而 `<<Drop>>` 的处理函数一抛异常就不会再往下执行——于是既没有路径入参、
也没有任何提示，用户看到的现象就是「拖进去什么都没发生」。

**修复**：
1. `parse_drop_paths()` 同时接受 `str` 与 `Iterable[str]`，内部统一走
   `_clean_paths()` 剥花括号、去空白。
2. `DropZone._on_drop` 拆成「薄壳 + `_handle_drop`」：薄壳全程 `try` 兜底，
   任何异常都弹窗 + `traceback.print_exc()`，**禁止静默失败**。

**顺带发现的风险**（比原bug 更隐蔽）：tkdnd 的 `%D` 替换在
`_subst_format_dnd` 里是**裸 `%D`，没有大括号包裹**。这意味着 Tcl 层把
drop 数据当词列表处理——路径含空格会断成两截，含反斜杠可能被当转义符吃掉。
同���份 data 可能解析成 `C:\a b\x.zip`（对）或 `C:abx.zip`（错）。

**防御设计**：`_handle_drop` 不信任任何单一解析策略，而是
`splitlist → 正则 → 原文整串` 依次展开候选，**用「路径必须真实存在且
类型匹配」做筛选**。只有磁盘上真的存在的那个才是对的。这条规则比任何
正则都可靠，因为用户的目标路径本来就是已知的具体文件。

**另一个真问题**：tkdnd 只把事件投递给**光标下最深**的已注册控件。原实现
只注册了 2 层Frame，光标落在中间的标签或「选择…」按钮上时事件被子控件
吞掉而不冒泡——表现为「有的地方能拖、有的地方不能」。改为 `_bind_drop()`
递归注册整棵子树。

### 11.2 导入完成没有提示

**根因**：`ResultCard` pack 在 `outer` 最末尾，而进度区是
`fill="both", expand=True`、日志面板 `height=10`。tkinter 的 pack 按添加
顺序分配空间，最后添加的卡片被前面的 expand 控件挤出可视区——**卡片确实
生成了，只是被推到窗口外看不见**，配合 `pythonw` 无控制台，用户自然理解成
「没跑完」。

**修复（三重保险，缺一不可）**：
1. **可滚动容器 `ScrollPane`**：Canvas + Scrollbar，内容再长也能滚到。
   切页签时重新计算滚动区域。
2. **`ResultCard.reveal()`**：卡片展开后沿父链找到 `ScrollPane` 并
   `scroll_to` 把自己带进视野。
3. **`notify=True` 弹模态提示**：完成/失败都弹窗。这条针对的是
   「用户跑完就去开 Blender 了、根本没盯窗口」的真实行为——
   卡片滚动得到 ≠ 用户看得到，模态框才能强制引起注意。

### 11.3 datafiles 与 scripts/startup 没传过去

**根因**：**不在迁移侧，而在导出侧**。`preferences.py` 里
`migration_include_datafiles` / `migration_include_startup_scripts`
默认是 `False`，于是配置包的 `components` 列表里根本没有
`datafiles` / `startup_scripts`，包里没有这些文件，迁移自然无从谈起。

迁移本身是**整目录原子替换**，包里有什么就装什么——它是忠实的，
问题在于源头就没打包。而且这类缺失天然是静默的：目标目录少一截文件，
不报错、不警告，用户往往几天后才发现。

**修复**：
1. 三个偏好的默认值改为 `True`，并写清各自的性质差异：
   `datafiles` 与应用模板是**纯数据**（贴图/字体/笔刷/模板），跨版本安全；
   `startup_scripts` 是**会在 Blender 启动时执行的代码**，若依赖旧版 API
   迁移后可能报错——所以仍在 description 里明确提示风险，让用户知情选择，
   而不是默默替他关掉。
2. 导出算子`MMY_OT_ExportConfig.execute` 里显式读取这三个属性，
   避免 `preferences` 为 `None` 时又回落成 `False`。
3. **新增检测与告知**：`migrate.missing_folder_components()` 从 manifest 的
   `components` 反查缺失的文件夹级组件，`describe_profile` 在确认信息里
   逐条列出并给出「回源 Blender 勾选后重新导出」的指引，
   `_on_migration_done` 的结果卡片也带一行「未包含」。
   **旧包不会因为这次修复自动补齐**——它们是真的没有这些文件，
   工具只能如实告知并给出补救路径。

### 11.4 测试策略的教训

这三个 bug 有一个共同点：**单测全绿，但功能实际不可用**。
纯逻辑层的 `parse_drop_paths` 单测用的是手写的 `str`，而真实输入是 `list`；
结果卡片的 `visible` 属性在窗口 `withdraw()` 状态下测不准。

因此 GUI 冒烟测试（`tests/smoke_gui.py`）改成**驱动真实 Tk 解析链路**：
用 `Tk.tk.splitlist()` 产出真实形态喂给 `DropZone._handle_drop()`，
用带空格 + 中文的名字（最容易触发 Tcl 转义问题的形态），
并在真实窗口里跑通 `ResultCard.show()` 后检查 `visible`。
这类"造真实输入"的测试比"造理想输入"更能挡住回归。

**注意**：不能用 `event generate -data <路径>` 来模拟——那会再过一层
Tcl 解析，把路径里的反斜杠吃掉，测出来的是构造错误而非产品行为。