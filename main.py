import argparse
import os
import asyncio
import json
import logging
import time

os.environ["TOKENIZERS_PARALLELISM"] = "false"

from dotenv import load_dotenv

load_dotenv()

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Use module-level logger instead of bare print statements for production clarity.
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Resolve paths relative to this file so the script can be run from any cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_POLICY_PATH = os.path.join(_HERE, "docs", "company_policy.txt")
_CONFIG_PATH = os.path.join(_HERE, "config.json")


def build_rag_retriever():
    """Load company policy, embed it into Qdrant, and return a retriever.

    Raises:
        FileNotFoundError: If the policy document does not exist.
        RuntimeError: If the Qdrant vector store cannot be reached.
    """
    if not os.path.exists(_POLICY_PATH):
        raise FileNotFoundError(
            f"Policy document not found: {_POLICY_PATH}"
        )

    loader = TextLoader(_POLICY_PATH)
    documents = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_documents(documents)
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    try:
        vectorstore = QdrantVectorStore.from_documents(
            documents=chunks,
            embedding=embeddings,
            url=qdrant_url,
            collection_name="company_policy",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to connect to Qdrant at {qdrant_url}: {exc}"
        ) from exc

    return vectorstore.as_retriever(search_kwargs={"k": 3})


def mcp_tool_to_langchain_schema(mcp_tool) -> dict:
    """Convert an MCP tool definition into the format LangChain / Groq expects."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description,
            "parameters": mcp_tool.inputSchema,
        },
    }


async def run_agent(query: str) -> None:
    """Run the RAG + MCP agent loop for the given user query.

    The agent:
    1. Fetches relevant chunks from the internal Qdrant vector store.
    2. Opens an MCP session to expose web-search tools to the LLM.
    3. Iterates until the LLM produces a final answer or max iterations are hit.

    Args:
        query: The user question to answer.
    """
    # --- Step 1: RAG — always fetch internal context first ---
    logger.info("Fetching internal RAG context...")
    retriever = build_rag_retriever()
    rag_docs = retriever.invoke(query)
    internal_context = "\n\n---\n\n".join(d.page_content for d in rag_docs)
    logger.info("Retrieved %d internal chunk(s).", len(rag_docs))

    # --- Step 2: Open MCP session and expose tools to the LLM ---
    if not os.path.exists(_CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found: {_CONFIG_PATH}")

    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)

    sc = cfg["mcpServers"]["duckduckgo-search"]
    server_params = StdioServerParameters(
        command=sc["command"],
        args=sc["args"],
        env={**os.environ, **sc.get("env", {})},
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # Ask the MCP server what tools it has, convert to Groq format
            listed = await session.list_tools()
            tools = [mcp_tool_to_langchain_schema(t) for t in listed.tools]
            logger.info(
                "MCP tools available to LLM: %s",
                [t["function"]["name"] for t in tools],
            )

            llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0)
            llm_with_tools = llm.bind_tools(tools)

            # --- Step 3: Agent loop — LLM decides which tools to call ---
            system_prompt = (
                "You are an HR assistant. You have been given internal company "
                "policy context. If it is sufficient, answer directly. "
                "If you need more up-to-date or broader information, use the "
                "search and fetch_content tools.\n\n"
                f"Internal company policy context:\n{internal_context}"
            )
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=query),
            ]

            iteration = 0
            max_iterations = 5
            while iteration < max_iterations:
                iteration += 1
                logger.info("--- LLM iteration %d ---", iteration)

                response = None
                for attempt in range(3):
                    try:
                        response = llm_with_tools.invoke(messages)
                        break
                    except Exception as exc:
                        if "429" in str(exc) and attempt < 2:
                            wait = 25
                            logger.warning(
                                "Rate limit hit, waiting %ds (attempt %d/3)...",
                                wait,
                                attempt + 1,
                            )
                            time.sleep(wait)
                        else:
                            raise

                if response is None:
                    logger.error("LLM returned no response after 3 attempts.")
                    break

                if not response.tool_calls:
                    # LLM has enough context — final answer
                    print(f"\nFinal Answer:\n{response.content}")
                    return

                # LLM decided to call one or more tools
                messages.append(response)  # assistant message with tool_calls

                for tool_call in response.tool_calls:
                    name = tool_call["name"]
                    args = tool_call["args"]
                    logger.info("LLM calls tool: '%s' with args: %s", name, args)

                    result = await session.call_tool(name, arguments=args)

                    if not result.content:
                        logger.warning("Tool '%s' returned empty content.", name)
                        tool_output = "[no content returned]"
                    else:
                        # Keep context small for TPM limit
                        tool_output = result.content[0].text[:800]

                    logger.info("Tool returned %d chars.", len(tool_output))

                    # Feed the result back so LLM can continue reasoning
                    messages.append(
                        ToolMessage(
                            content=tool_output,
                            tool_call_id=tool_call["id"],
                        )
                    )

            logger.warning(
                "Agent reached max iterations (%d) without a final answer.",
                max_iterations,
            )


if __name__ == "__main__":
    _DEFAULT_QUERY = "What is the company's policy on remote work?"
    parser = argparse.ArgumentParser(
        description="RAG + MCP agent for HR policy questions."
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=_DEFAULT_QUERY,
        help=(
            "The question to answer (default: %(default)r). "
            "Wrap multi-word queries in quotes."
        ),
    )
    args = parser.parse_args()
    asyncio.run(run_agent(args.query))
