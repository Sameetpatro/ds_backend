"""
LangChain RAG Service for Humsafar AI Chatbot (SHREE).
Uses LangChain InMemoryVectorStore with OpenRouter embeddings (openai/text-embedding-3-small)
and OpenRouter Chat (openai/gpt-4o-mini).

Grounded in the `prompts`, `nodes`, and `heritage_sites` tables.
Enforces node-level boundaries, guardrails, and anti-hallucination directives.
"""

import os
import logging
from typing import Optional, List, Dict, Tuple
from dotenv import load_dotenv

from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy.orm import Session

from app.models import Prompt, HeritageSite, Node

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
CHAT_MODEL = "openai/gpt-4o-mini"

# In-memory vector store cache per site_id: {site_id: InMemoryVectorStore}
_SITE_VECTORSTORES: Dict[int, InMemoryVectorStore] = {}

_embeddings: Optional[OpenAIEmbeddings] = None
_llm: Optional[ChatOpenAI] = None


def get_embeddings() -> OpenAIEmbeddings:
    global _embeddings
    if _embeddings is None:
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set in environment.")
        _embeddings = OpenAIEmbeddings(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            model=EMBEDDING_MODEL,
        )
    return _embeddings


def get_llm(temperature: float = 0.2) -> ChatOpenAI:
    global _llm
    if _llm is None or _llm.temperature != temperature:
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set in environment.")
        _llm = ChatOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            model=CHAT_MODEL,
            temperature=temperature,
            max_tokens=400,
            timeout=45.0,
        )
    return _llm


def invalidate_site_cache(site_id: int) -> None:
    """Invalidates the in-memory vector store for a site so it is rebuilt on next query."""
    if site_id in _SITE_VECTORSTORES:
        del _SITE_VECTORSTORES[site_id]
        logger.info(f"[RAG] Invalidated cached vector store for site_id={site_id}")


def clear_all_caches() -> None:
    """Clears all cached site vector stores."""
    _SITE_VECTORSTORES.clear()
    logger.info("[RAG] Cleared all site vector store caches")


def build_site_documents(db: Session, site_id: int) -> Tuple[List[Document], str, Optional[HeritageSite]]:
    """
    Extracts knowledge documents and prompt guardrails from database tables:
    1. `prompts` table (both node-level and site-level)
    2. `nodes` table (spot names, descriptions, sequences)
    3. `heritage_sites` table (summary, history, fun_facts)
    """
    site = db.query(HeritageSite).filter(HeritageSite.id == site_id).first()
    site_name = site.name if site else f"Site #{site_id}"

    docs: List[Document] = []

    # 1. Prompts table (richest source of curated heritage knowledge & boundaries)
    prompts = db.query(Prompt).filter(Prompt.site_id == site_id).all()
    for p in prompts:
        if not p.content or not p.content.strip():
            continue
        p_type = "site_overview" if p.node_id is None else f"node_{p.node_id}"
        docs.append(
            Document(
                page_content=p.content.strip(),
                metadata={
                    "site_id": site_id,
                    "site_name": site_name,
                    "node_id": p.node_id,
                    "title": p.title or f"Prompt for {site_name}",
                    "source": "prompts_table",
                    "type": p_type,
                }
            )
        )

    # 2. Nodes table (descriptions & sequences)
    nodes = db.query(Node).filter(Node.site_id == site_id).all()
    for n in nodes:
        node_parts = [f"Spot: {n.name} (Stop #{n.sequence_order}) at {site_name}"]
        if n.is_king:
            node_parts.append("This is the King Node (Main Entrance / Starting Point) for this heritage site.")
        if n.description:
            node_parts.append(f"Description: {n.description}")
        docs.append(
            Document(
                page_content="\n".join(node_parts),
                metadata={
                    "site_id": site_id,
                    "site_name": site_name,
                    "node_id": n.id,
                    "title": n.name,
                    "source": "nodes_table",
                    "type": f"node_{n.id}",
                }
            )
        )

    # 3. HeritageSite table (general history, summary, fun facts)
    if site:
        site_parts = [f"Heritage Site: {site.name}"]
        if site.summary:
            site_parts.append(f"Summary: {site.summary}")
        if site.history:
            site_parts.append(f"History: {site.history}")
        if site.fun_facts:
            site_parts.append(f"Fun Facts: {site.fun_facts}")
        if site.helpline_number:
            site_parts.append(f"Helpline: {site.helpline_number}")
        
        docs.append(
            Document(
                page_content="\n".join(site_parts),
                metadata={
                    "site_id": site_id,
                    "site_name": site.name,
                    "node_id": None,
                    "title": f"{site.name} Overview",
                    "source": "heritage_sites_table",
                    "type": "site_overview",
                }
            )
        )

    return docs, site_name, site


def get_or_build_site_vectorstore(db: Session, site_id: int) -> InMemoryVectorStore:
    """
    Returns the cached vector store for a site, or builds it from database rows.
    """
    if site_id in _SITE_VECTORSTORES:
        return _SITE_VECTORSTORES[site_id]

    docs, site_name, site = build_site_documents(db, site_id)
    if not docs:
        # Minimal placeholder document if site has no rows yet
        docs = [Document(page_content=f"{site_name} is a heritage site.", metadata={"site_id": site_id})]

    # Split into focused semantic chunks for precision retrieval
    splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=80)
    split_docs = splitter.split_documents(docs)

    embeddings = get_embeddings()
    vectorstore = InMemoryVectorStore.from_documents(split_docs, embedding=embeddings)

    _SITE_VECTORSTORES[site_id] = vectorstore
    logger.info(f"[RAG] Indexed {len(split_docs)} chunks into vector store for site_id={site_id} ({site_name})")
    return vectorstore


def get_active_guardrails_and_node(
    db: Session, site_id: int, node_id: Optional[int]
) -> Tuple[str, str, Optional[str]]:
    """
    Extracts the active node's guardrail guidelines and spot context.
    """
    site = db.query(HeritageSite).filter(HeritageSite.id == site_id).first()
    site_name = site.name if site else f"Site #{site_id}"

    if node_id:
        node = db.query(Node).filter(Node.id == node_id).first()
        node_name = node.name if node else f"Node #{node_id}"

        # Check for node-specific prompt
        node_prompt = db.query(Prompt).filter(
            Prompt.site_id == site_id,
            Prompt.node_id == node_id
        ).first()

        guardrail_text = node_prompt.content if node_prompt else (node.description or "")
        return site_name, node_name, guardrail_text

    # Site-level fallback (visitor has not scanned a node QR yet)
    site_prompt = db.query(Prompt).filter(
        Prompt.site_id == site_id,
        Prompt.node_id == None
    ).first()
    guardrail_text = site_prompt.content if site_prompt else (site.summary if site else "")
    return site_name, "General Site Entrance", guardrail_text


def format_retrieved_context(docs: List[Document]) -> str:
    """Formats retrieved document chunks into clean, cited text for the LLM prompt."""
    if not docs:
        return "No specific database documents retrieved."
    
    sections = []
    for i, doc in enumerate(docs, start=1):
        title = doc.metadata.get("title", "Document")
        sections.append(f"--- Context Snippet {i} ({title}) ---\n{doc.page_content.strip()}")
    return "\n\n".join(sections)


async def run_rag_chat(
    site_id: int,
    node_id: Optional[int],
    message: str,
    history: List,
    db: Session,
    language_instruction: Optional[str] = None,
) -> str:
    """
    Executes LangChain RAG pipeline with strict guardrails:
    1. Fetches or builds cached in-memory vector store for the site.
    2. Performs semantic similarity search against the user's query.
    3. Pinned node guardrails ensure persona, boundary adherence, and anti-hallucination.
    4. Invokes LLM via OpenRouter.
    """
    site_name, node_name, active_guardrails = get_active_guardrails_and_node(db, site_id, node_id)

    # 1. Retrieve relevant knowledge chunks
    try:
        vectorstore = get_or_build_site_vectorstore(db, site_id)
        # Search for top-3 relevant context chunks
        retrieved_docs = vectorstore.similarity_search(query=message, k=3)
        retrieved_context = format_retrieved_context(retrieved_docs)
    except Exception as exc:
        logger.warning(f"[RAG] Vector retrieval failed ({exc}), falling back to direct prompt context")
        retrieved_context = active_guardrails or f"{site_name} is a heritage site."

    # 2. Build system prompt combining Persona, Guardrails, and Grounded Context
    if node_id:
        location_header = (
            f"The visitor is standing at: {node_name} within {site_name}. "
            f"You must speak as the guide for this specific spot."
        )
    else:
        location_header = (
            f"The visitor is currently exploring {site_name}. "
            f"You are introducing the heritage site as a whole."
        )

    lang_line = f"\nLANGUAGE DIRECTIVE: {language_instruction}\n" if language_instruction else ""
    system_prompt = f"""You are SHREE, the official, engaging AI heritage guide of HUMSAFAR.{lang_line}
{location_header}

CURRENT SPOT GUARDRAILS & BOUNDARIES:
{active_guardrails or 'Keep responses focused strictly on this location.'}

STRICT OPERATIONAL RULES:
1. Grounding & Anti-Hallucination: Answer the visitor's question ONLY using the factual information in the "Retrieved Heritage Knowledge" and "Spot Guardrails" below.
2. Boundaries: Do NOT answer questions unrelated to {site_name}. If the visitor asks off-topic questions (e.g. general trivia, coding, unrelated history), politely inform them that as Shree, you can only assist with this heritage site.
3. Amenities: If the visitor asks about washrooms, drinking water, restaurants, or nearby facilities, guide them to use the "Directions" feature in the site details screen.
4. Media: If the visitor asks for video or audio guides, tell them to tap the media buttons in the app.
5. Unknown Facts: If the answer is NOT present in the retrieved knowledge, do NOT invent or guess historical figures, dates, or details. Simply state politely that you do not have that specific information for this spot.
6. Tone & Format: Be warm, descriptive, and conversational (3 to 5 sentences maximum). Do NOT use markdown, bold asterisks (**), or bullet points — use plain natural speech only.

RETRIEVED HERITAGE KNOWLEDGE:
---------------------------------------------
{retrieved_context}
---------------------------------------------
"""

    # 3. Assemble LangChain messages (System + History + Current Query)
    messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]

    for turn in history:
        role = turn.role.lower() if hasattr(turn, "role") else turn.get("role", "").lower()
        content = turn.content if hasattr(turn, "content") else turn.get("content", "")
        if not content:
            continue
        if role in ("user", "human"):
            messages.append(HumanMessage(content=content))
        elif role in ("assistant", "ai", "bot"):
            messages.append(AIMessage(content=content))

    messages.append(HumanMessage(content=message))

    # 4. Invoke LLM
    llm = get_llm(temperature=0.2)
    response = await llm.ainvoke(messages)
    return response.content.strip()
