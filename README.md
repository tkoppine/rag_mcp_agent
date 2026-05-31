# RAG + MCP Hybrid Agent

A learning project that builds a **hybrid question-answering agent** combining two retrieval strategies:

1. **RAG (Retrieval-Augmented Generation)** — searches an internal company policy document stored in a local Qdrant vector database.
2. **Live Web Search via MCP** — connects to a DuckDuckGo MCP server to fetch real-time web results when the internal context is not enough.

The core intent is to understand how an **LLM acts as a decision-maker**, not just a text generator — it reads the internal context, decides whether it needs more information from the web, calls the right tools, and only then produces a final answer.

---

## How It Works

```
User Query
    │
    ▼
RAG Retriever  ──►  Qdrant Vector DB (company_policy.txt)
    │                    returns top-3 relevant chunks
    │
    ▼
LLM (Groq / LLaMA)  ◄──  MCP Tool Schemas (search, fetch_content)
    │
    ├── Enough context?  ──►  Final Answer
    │
    └── Need more info?
            │
            ▼
        MCP Client  ──►  duckduckgo-mcp-server (uvx)
            │                 search() / fetch_content()
            │
            ▼
        Tool Result appended to conversation
            │
            ▼
        LLM again  ──►  Final Answer
```

The LLM drives this loop — it calls tools zero, one, or multiple times depending on what it needs. This is the **agent pattern**, not a hardcoded pipeline.

---

## What This Project Teaches

| Concept | Where it appears |
|---|---|
| RAG with vector embeddings | `main.py` → `build_rag_retriever()` |
| MCP client implementation | `mcp_client_web_search.py` |
| LLM tool calling / agent loop | `main.py` → `run_agent()` while loop |
| MCP server configuration | `config.json` |
| Hybrid retrieval (internal + web) | Both combined in `run_agent()` |

### The difference between a pipeline and an agent

**Pipeline (hardcoded):** Code always runs search → fetch → RAG → LLM. The LLM only synthesizes.

**Agent (this project):** LLM sees the internal context and available tools, then decides what to call. If internal docs are sufficient, it skips web search entirely. If not, it searches, fetches, and loops back.

### Claude vs your own inference engine

Claude Desktop / Claude Code / Claude terminal are all inference engines — they take your query, attach MCP tool schemas, send to the Anthropic LLM API, execute tool calls, and loop until done.

`main.py` is the same thing, built from scratch — just backed by Groq instead of Anthropic, with a custom RAG store bolted on.

---

## Project Structure

```
RAG_Learning/
├── main.py                    # Entry point — agent loop (RAG + MCP + LLM)
├── mcp_client_web_search.py   # MCP client — connects to duckduckgo-mcp-server
├── config.json                # MCP server configuration
├── docs/
│   └── company_policy.txt     # Internal knowledge base (ACME Corp policy)
└── README.md
```

---

## Setup

### Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (`brew install uv`) — used to run the MCP server via `uvx`
- Qdrant running locally: `docker run -p 6333:6333 qdrant/qdrant`
- A Groq API key

### Install dependencies

```bash
pip install mcp langchain langchain-community langchain-huggingface \
            langchain-qdrant langchain-groq langchain-text-splitters \
            sentence-transformers python-dotenv
```

### Environment variables

Create a `.env` file:

```
GROQ_API_KEY=your_groq_api_key_here
```

### Run

```bash
python3 main.py
```

---

## Key Files Explained

### `main.py`

- `build_rag_retriever()` — loads `company_policy.txt`, chunks it, embeds with `all-MiniLM-L6-v2`, stores in Qdrant, returns a retriever.
- `mcp_tool_to_langchain_schema()` — converts the MCP server's tool definitions into the format Groq's tool-calling API expects.
- `run_agent()` — the agent loop: fetch RAG context → open MCP session → bind tools to LLM → loop until no more tool calls.

### `mcp_client_web_search.py`

Standalone MCP client that opens a stdio connection to `duckduckgo-mcp-server`, calls `search` and `fetch_content` tools, and returns results. Can also be run directly for testing:

```bash
python3 mcp_client_web_search.py
```

### `config.json`

Tells the MCP client how to start the DuckDuckGo server:

```json
{
    "mcpServers": {
        "duckduckgo-search": {
            "command": "uvx",
            "args": ["duckduckgo-mcp-server"]
        }
    }
}
```

`uvx` pulls and runs [`duckduckgo-mcp-server`](https://github.com/nickclyde/duckduckgo-mcp-server) without a manual install step.

---

## MCP Protocol — How the Client Works

MCP (Model Context Protocol) uses JSON-RPC 2.0 over stdio. The client:

1. Spawns the server as a subprocess (`uvx duckduckgo-mcp-server`)
2. Sends an `initialize` handshake
3. Calls `list_tools` to discover available tools and their input schemas
4. Calls `call_tool` with tool name + arguments to execute a tool
5. Receives structured results back

The LLM never talks to the MCP server directly — the client (your code, or Claude) acts as the bridge.