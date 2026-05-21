#!/usr/bin/env python3
"""
Simple DuckDuckGo Search MCP Server
无需 API key，免费使用
"""

from fastmcp import FastMCP
import requests

mcp = FastMCP("duckduckgo-search")


@mcp.tool()
def search(query: str, max_results: int = 5) -> str:
    """
    使用 DuckDuckGo 搜索网络

    Args:
        query: 搜索关键词
        max_results: 返回结果数量（默认 5）

    Returns:
        搜索结果的摘要文本
    """
    try:
        url = "https://api.duckduckgo.com/"
        params = {
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1
        }

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        results = []

        # 相关主题
        related = data.get("RelatedTopics", [])
        for item in related[:max_results]:
            if "Text" in item and "FirstURL" in item:
                results.append(f"- {item['Text']}\n  URL: {item['FirstURL']}")

        # 抽象摘要
        abstract = data.get("AbstractText", "")
        abstract_url = data.get("AbstractURL", "")
        if abstract:
            results.insert(0, f"摘要: {abstract}\n来源: {abstract_url}")

        if not results:
            return f"未找到 '{query}' 的相关结果"

        return f"搜索结果 ({query}):\\n\\n" + "\\n\\n".join(results)

    except Exception as e:
        return f"搜索失败: {str(e)}"


if __name__ == "__main__":
    mcp.run()