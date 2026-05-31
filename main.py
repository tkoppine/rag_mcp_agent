import os
import asyncio
import json
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


def build_rag_retriever():
    loader = TextLoader("docs/company_policy.txt")
    documents = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_documents(documents)
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    vectorstore = QdrantVectorStore.from_documents(
        documents=chunks,
        embedding=embeddings,
        url="http://localhost:6333",
        collection_name="company_policy",
    )
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


async def run_agent(query: str):
    # --- Step 1: RAG — always fetch internal context first ---
    print("Fetching internal RAG context...")
    retriever = build_rag_retriever()
    rag_docs = retriever.invoke(query)
    internal_context = "\n\n---\n\n".join(d.page_content for d in rag_docs)
    print(f"Retrieved {len(rag_docs)} internal chunk(s).\n")

    # --- Step 2: Open MCP session and expose tools to the LLM ---
    with open("config.json") as f:
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
            print(f"MCP tools available to LLM: {[t['function']['name'] for t in tools]}\n")

            llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0)
            llm_with_tools = llm.bind_tools(tools)

            # --- Step 3: Agent loop — LLM decides which tools to call ---
            messages = [
                SystemMessage(content=(
                    "You are an HR assistant. You have been given internal company policy context. "
                    "If it is sufficient, answer directly. "
                    "If you need more up-to-date or broader information, use the search and fetch_content tools.\n\n"
                    f"Internal company policy context:\n{internal_context}"
                )),
                HumanMessage(content=query),
            ]

            iteration = 0
            while True:
                iteration += 1
                print(f"--- LLM iteration {iteration} ---")
                response = llm_with_tools.invoke(messages)

                if not response.tool_calls:
                    # LLM has enough context — final answer
                    print(f"\nFinal Answer:\n{response.content}")
                    break

                # LLM decided to call one or more tools
                messages.append(response)  # append the assistant message with tool_calls

                for tool_call in response.tool_calls:
                    name = tool_call["name"]
                    args = tool_call["args"]
                    print(f"  LLM calls tool: '{name}' with args: {args}")

                    result = await session.call_tool(name, arguments=args)
                    tool_output = result.content[0].text

                    print(f"  Tool returned {len(tool_output)} chars.\n")

                    # Feed the result back so LLM can continue reasoning
                    messages.append(ToolMessage(
                        content=tool_output,
                        tool_call_id=tool_call["id"],
                    ))


if __name__ == "__main__":
    query = "What is the company's policy on remote work?"
    asyncio.run(run_agent(query))