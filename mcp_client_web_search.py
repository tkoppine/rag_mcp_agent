import asyncio
import json
import os
import re
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _load_server_params() -> StdioServerParameters:
    with open("config.json", "r") as f:
        config = json.load(f)
    server_config = config["mcpServers"]["duckduckgo-search"]
    return StdioServerParameters(
        command=server_config["command"],
        args=server_config["args"],
        env={**os.environ, **server_config.get("env", {})},
    )


async def fetch_web_context(query: str, max_results: int = 3) -> list[dict]:
    """
    Search DDG and fetch page content for each result.
    Returns a list of dicts: {"url": ..., "content": ...}
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
            search_text = search_response.content[0].text

            # Step 2: extract URLs from results
            urls = list(dict.fromkeys(re.findall(r'https?://[^\s)\]"]+', search_text)))

            # Step 3: fetch page content for each URL
            for url in urls[:max_results]:
                try:
                    fetch_response = await session.call_tool(
                        "fetch_content",          # correct tool name from the server
                        arguments={"url": url, "max_length": 3000},
                    )
                    content = fetch_response.content[0].text
                    results.append({"url": url, "content": content})
                except Exception as e:
                    results.append({"url": url, "content": f"[fetch failed: {e}]"})

    return results


# --- standalone test ---
if __name__ == "__main__":
    async def _demo():
        query = "company remote work policy best practices 2025"
        print(f"Searching: {query}\n")
        items = await fetch_web_context(query, max_results=2)
        for item in items:
            print(f"URL: {item['url']}")
            print(item["content"][:800])
            print("\n" + "=" * 60 + "\n")

    asyncio.run(_demo())