# RAG + MCP Hybrid Agent

A learning project that builds a hybrid question-answering agent combining local RAG retrieval with live web search via the Model Context Protocol (MCP).

---

## What It Does

- Searches an internal company policy document stored in a local Qdrant vector database (RAG).
- Optionally fetches live web results via a DuckDuckGo MCP server when internal context is insufficient.
- The LLM (not hardcoded logic) decides whether to call web-search tools or answer directly from RAG context — this is the agent pattern.

---

## How It Works

```
User Query
    │
    ▼
build_rag_retriever()
    └── loads docs/company_policy.txt
    └── chunks (size=500, overlap=50) → embeds (all-MiniLM-L6-v2) → Qdrant
    └── returns top-3 relevant chunks
    │
    ▼
run_agent()
    ├── injects RAG chunks into system prompt
    ├── opens MCP session → duckduckgo-mcp-server (via uvx)
    ├── binds MCP tool schemas to LLM (Groq / llama-3.1-8b-instant)
    │
    └── Agent loop (max 5 iterations):
            │
            ├── LLM has enough context?  ──►  print Final Answer, return
            │
            └── LLM calls a tool?
                    │
                    ▼
                MCP client calls search() or fetch_content()
                Tool output (truncated to 800 chars) appended to messages
                    │
                    ▼
                LLM iterates again
```

The LLM drives this loop. It can call tools zero, one, or multiple times per query.

---

## Setup & Installation

### Prerequisites

- Python **3.12.0** (pinned via `.python-version` / pyenv)
- [uv](https://github.com/astral-sh/uv) — used to run the MCP server via `uvx` (`brew install uv`)
- Qdrant running locally on port 6333:
  ```bash
  docker run -p 6333:6333 qdrant/qdrant
  ```
- A [Groq API key](https://console.groq.com/)

### Install dependencies

```bash
pip install -r requirements.txt
```

### Environment variables

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_groq_api_key_here
```

`python-dotenv` loads this automatically at startup. The `.env` file is gitignored — never commit it.

---

## Usage

Run with the default query (hardcoded in `__main__`):

```bash
python main.py
```

Run with a custom query (CLI arg support):

```bash
python main.py "What is the vacation policy?"
```

Run the standalone MCP web-search demo:

```bash
python mcp_client_web_search.py
```

This runs a hardcoded demo query (`"company remote work policy best practices 2025"`) and prints the first 800 chars of each result.

---

## Configuration

### `.env`

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Required. Your Groq API key. |

### `config.json`

Tells the MCP client how to launch the DuckDuckGo server:

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

- `uvx` pulls and runs [`duckduckgo-mcp-server`](https://github.com/nickclyde/duckduckgo-mcp-server) on demand — no manual install needed.
- An optional `"env"` key inside the server block is merged with `os.environ` and passed to the subprocess (env passthrough supported).

### Qdrant URL

The Qdrant URL is hardcoded to `http://localhost:6333` in `main.py`. Override it by editing that value directly, or set a `QDRANT_URL` environment variable if you add support for it later.

---

## Key Files

### `main.py`

- `build_rag_retriever()` — loads `docs/company_policy.txt`, splits into chunks (size=500, overlap=50), embeds with `all-MiniLM-L6-v2`, stores in Qdrant collection `company_policy`, returns a retriever (`k=3`).
- `mcp_tool_to_langchain_schema()` — converts MCP tool definitions into the dict format Groq's tool-calling API expects.
- `run_agent(query)` — the agent loop: fetch RAG context → open MCP session → bind tools to LLM → iterate until final answer or max iterations.
- Sets `TOKENIZERS_PARALLELISM=false` at startup to suppress HuggingFace tokenizer warnings.
- Uses `__file__`-relative paths so the script works from any working directory.

### `mcp_client_web_search.py`

- `fetch_web_context(query, max_results=3)` — searches DDG via MCP, extracts URLs from results, skips PDFs, deduplicates, fetches page content for each URL, returns `list[dict]` with `"url"` and `"content"` keys.
- Calls `call_tool` directly (does not call `list_tools`) — the available tools (`search`, `fetch_content`) are assumed from the server.
- Can be run standalone for testing; uses a hardcoded demo query.
- Uses `__file__`-relative path for `config.json`.

### `config.json`

MCP server configuration. See [Configuration](#configuration) above.

### `requirements.txt`

Pinned dependencies for the full RAG + MCP stack:

```
langchain-community, langchain-text-splitters, langchain-huggingface,
langchain-qdrant, langchain-groq, langchain-core,
sentence-transformers, mcp, python-dotenv
```

---

## Project Structure

```
RAG_Learning/
├── .claude/
│   ├── commands/          # Custom Claude Code slash commands
│   │   ├── code-quality.md
│   │   ├── commit.md
│   │   ├── deploy.md
│   │   ├── push.md
│   │   └── readme-updater.md
│   ├── hooks/
│   │   └── confirm-push.sh
│   └── settings.json
├── docs/
│   └── company_policy.txt # Internal knowledge base (ACME Corp policy)
├── chroma_db/             # LEGACY — ChromaDB leftover, safe to delete
├── .env                   # GROQ_API_KEY — gitignored, never commit
├── .gitignore
├── .python-version        # 3.12.0 (pyenv)
├── config.json            # MCP server configuration
├── main.py                # Entry point — RAG + MCP agent loop
├── mcp_client_web_search.py  # Standalone MCP web-search client
├── requirements.txt       # Pinned dependencies
└── README.md
```

---

## Runtime Behavior

### Retry logic (rate limits)

- On a 429 (rate limit) error from Groq, the agent sleeps 25 seconds and retries up to 3 times per LLM call.
- If all 3 attempts fail, the exception is re-raised.

### Iteration cap

- The agent loop runs for at most `max_iterations=5` iterations.
- If the LLM has not produced a final answer by then, a warning is logged and the function returns without output.

### Tool output truncation

- Each MCP tool result is truncated to 800 characters before being appended to the message history.
- This keeps token usage within Groq's TPM (tokens per minute) limit.

### Logging

- All runtime information is emitted via Python's `logging` module at `INFO` level.
- Format: `LEVEL: message` (e.g., `INFO: Retrieved 3 internal chunk(s).`).

---

## Known Limitations / Cleanup Notes

- **`chroma_db/`** — legacy directory from an earlier ChromaDB iteration. Not used. Safe to delete: `rm -rf chroma_db/`
- **No test suite** — no unit or integration tests exist. All verification is manual.
- **Hardcoded model** — `llama-3.1-8b-instant` on Groq at `temperature=0` is not configurable without editing `main.py`.
- **Hardcoded Qdrant URL** — `http://localhost:6333` is not read from an environment variable.
- **No CLI arg support (currently)** — the query in `main.py`'s `__main__` block may be hardcoded; CLI arg support (`python main.py "query"`) may be added.
- **Learning project** — not intended for production use.
