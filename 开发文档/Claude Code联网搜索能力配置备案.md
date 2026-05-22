# Claude Code 联网搜索能力配置备案

> 配置时间：2026-05-22
> 目的：确保 Claude Code 具备持久化的联网搜索能力

---

## 问题背景

Claude Code 内置的 `WebSearch` 工具在某些情况下会遇到 API 兼容性问题：

```
API Error: 400 model `claude-haiku-4-5-20251001` is not supported.
```

需要通过外部方案解决联网搜索需求。

---

## 解决方案

采用 **Tavily API + 自定义脚本 + 配置固化** 的组合方案。

### 方案架构

```
┌─────────────────────────────────────────────────────┐
│                    Claude Code                       │
├─────────────────────────────────────────────────────┤
│  CLAUDE.md (全局)                                   │
│  ├─ 默认规则                                        │
│  ├─ 网络搜索能力指令 ←── 触发搜索的行为规范          │
│  └─ ...                                             │
├─────────────────────────────────────────────────────┤
│  settings.json                                      │
│  ├─ permissions.allow                               │
│  │  └─ "Bash(python cc_search.py *)" ←── 预授权    │
│  └─ ...                                             │
├─────────────────────────────────────────────────────┤
│  cc_search.py                                       │
│  ├─ Tavily API 调用                                 │
│  ├─ 结果格式化                                      │
│  └─ UTF-8 输出处理                                  │
└─────────────────────────────────────────────────────┘
```

---

## 配置文件清单

| 文件 | 位置 | 作用 |
|------|------|------|
| **cc_search.py** | `C:\Users\EDY\.claude\cc_search.py` | 搜索脚本（调用 Tavily API） |
| **settings.json** | `C:\Users\EDY\.claude\settings.json` | 预授权搜索脚本权限 |
| **CLAUDE.md** | `C:\Users\EDY\.claude\CLAUDE.md` | 添加联网搜索指令 |
| **.mcp.json** | `C:\Users\EDY\.claude\.mcp.json` | MCP server 配置（备用） |
| **mcp_tavily.py** | `C:\Users\EDY\.claude\mcp_tavily.py` | MCP server 实现（备用） |

---

## 搜索脚本设计

### 核心功能

```python
# cc_search.py 核心逻辑

def tavily_search(query: str, search_depth: str = "basic", max_results: int = 10):
    response = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": search_depth,        # basic / advanced
            "include_answer": True,              # AI 概要回答
            "include_raw_content": False,
            "max_results": max_results
        },
        timeout=30.0
    )
    return response.json()
```

### 输出格式

```
## AI 概要回答
[AI 生成的答案]

## 搜索结果
1. **标题**
   URL: https://...
   摘要: 内容摘要...

---

Sources:
- [标题1](URL1)
- [标题2](URL2)
...
```

---

## 使用方法

### 命令格式

```bash
# 快速搜索（默认）
python C:/Users/EDY/.claude/cc_search.py "搜索关键词"

# 深度搜索（更多结果、更详细）
python C:/Users/EDY/.claude/cc_search.py "关键词" --depth=advanced

# 自定义结果数量
python C:/Users/EDY/.claude/cc_search.py "关键词" --max=15
```

### 自动触发场景

根据 CLAUDE.md 中的指令，以下场景会自动联网搜索：

- 查询官方文档（Blender、Python 等）
- 查找社区问答（StackExchange、Reddit 等）
- 了解最新技术动态
- 检查版本兼容性
- 其他需要外部资料的情况

---

## 权限配置

### settings.json 预授权

```json
{
  "permissions": {
    "allow": [
      "Bash(python C:/Users/EDY/.claude/cc_search.py *)"
    ]
  }
}
```

这样 Claude Code 可以直接调用搜索脚本，无需每次请求权限。

---

## CLAUDE.md 指令

### 全局配置内容

```markdown
## 网络搜索能力
- 当需要搜索外部资料时，使用 Tavily API 搜索工具
- 搜索命令：`python C:/Users/EDY/.claude/cc_search.py "<关键词>" [--depth=advanced]`
- `--depth=basic` 为快速搜索（默认），`--depth=advanced` 为深度搜索
- 搜索后必须在回答末尾列出 Sources 部分（Markdown 链接格式）
- 适用场景：查询官方文档、社区问答、最新技术动态、版本兼容性等
```

---

## CLAUDE.md 优先级机制

### 层级关系

```
全局 CLAUDE.md (C:\Users\EDY\.claude\CLAUDE.md)
    ↓ 基础配置，所有项目共享
    
项目 CLAUDE.md (项目目录\CLAUDE.md)
    ↓ 项目专属配置，叠加/覆盖全局
```

### 规则

| 情况 | 结果 |
|------|------|
| 全局有，项目没有 | 全局配置生效 |
| 全局有，项目也有相同内容 | 项目配置覆盖全局 |
| 全局有，项目有不同的额外内容 | 两者叠加生效 |

**本项目情况**：项目 CLAUDE.md 不包含联网搜索配置，因此全局配置自动生效。

---

## Tavily API 信息

- **服务商**：https://www.tavily.com/
- **API Key**：`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`
- **特点**：提供 AI 概要回答 + 多来源搜索结果
- **费用**：有免费额度，具体参见官网定价

---

## 测试验证

### 测试命令

```bash
python "C:/Users/EDY/.claude/cc_search.py" "Claude Code MCP server configuration"
```

### 测试结果

成功返回：
- AI 概要回答
- 10 条搜索结果
- Sources 链接列表

---

## 变更记录

| 时间 | 变更内容 |
|------|---------|
| 2026-05-22 | 创建联网搜索配置方案，包括脚本、权限、指令 |
| 2026-05-22 | 修复 Windows 控制台 UTF-8 编码问题 |
| 2026-05-22 | 更新全局 CLAUDE.md 和 settings.json |

---

## 附录：搜索脚本完整代码

参见：`C:\Users\EDY\.claude\cc_search.py`

关键代码片段：

```python
#!/usr/bin/env python3
"""Claude Code 网络搜索工具 - 使用 Tavily API"""

import httpx
import json
import sys

TAVILY_API_KEY = "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"

def tavily_search(query: str, search_depth: str = "basic", max_results: int = 10):
    """调用 Tavily API 搜索"""
    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": search_depth,
                "include_answer": True,
                "include_raw_content": False,
                "max_results": max_results
            },
            timeout=30.0
        )
        return response.json()
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')  # Windows 编码修复
    # ... 参数解析和输出格式化
```