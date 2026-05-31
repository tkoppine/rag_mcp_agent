import asyncio
import json
import os
import re

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Resolve config path relative to this file so it works from any working directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_HERE, "config.json")


def _load_server_params() -> StdioServerParameters:
    """Read config.json and return MCP StdioServerParameters for duckduckgo-search."""
    if not os.path.exists(_CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found: {_CONFIG_PATH}")

    with open(_CONFIG_PATH) as f:
        config = json.load(f)

    server_config = config["mcpServers"]["duckduckgo-search"]
    return StdioServerParameters(
        command=server_config["command"],
        args=server_config["args"],
        env={**os.environ, **server_config.get("env", {})},
    )


async def fetch_web_context(query: str, max_results: int = 3) -> list[dict]:
    """Search DDG and fetch page content for each result.

    Args:
        query: The search query string.
        max_results: Maximum number of URLs to fetch content from.

    Returns:
        A list of dicts with keys ``"url"`` and ``"content"``.
        If a fetch fails the ``"content"`` value describes the error.
    """
    server_params = _load_server_params()
    results = []

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # Step 1: search
            search_response = await session.call_tool(
                "search",
                arguments={"query": query, "max_results": max_results},
            )

            if not search_response.content:
                return results

            search_text = search_response.content[0].text

            # Step 2: extract URLs from results, skip PDFs
            raw_urls = re.findall(r"https?://[^\s)\]\",]+", search_text)
            urls = [
                u
                for u in dict.fromkeys(raw_urls)
                if not u.lower().endswith(".pdf")
            ]

            # Step 3: fetch page content for each URL
            for url in urls[:max_results]:
                try:
                    fetch_response = await session.call_tool(
                        "fetch_content",
                        arguments={"url": url, "max_length": 3000, "backend": "auto"},
                    )
                    if not fetch_response.content:
                        results.append({"url": url, "content": "[empty response]"})
                    else:
                        content = fetch_response.content[0].text
                        results.append({"url": url, "content": content})
                except Exception as exc:
                    results.append({"url": url, "content": f"[fetch failed: {exc}]"})

    return results


# --- standalone test ---
if __name__ == "__main__":
    async def _demo() -> None:
        """Run a quick demo search and print the first 800 chars of each result."""
        query = "company remote work policy best practices 2025"
        print(f"Searching: {query}\n")
        items = await fetch_web_context(query, max_results=2)
        for item in items:
            print(f"URL: {item['url']}")
            print(item["content"][:800])
            print("\n" + "=" * 60 + "\n")

    asyncio.run(_demo())
