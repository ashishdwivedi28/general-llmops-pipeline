"""Agent tools — RAG retrieval via Vertex AI RAG Engine."""

from __future__ import annotations

import logging

from google.adk.tools import VertexAiSearchTool

logger = logging.getLogger(__name__)


def create_rag_retrieval_tool(
    rag_corpus_resource: str,
    similarity_top_k: int = 10,
    vector_distance_threshold: float = 0.5,
) -> VertexAiSearchTool:
    """Create a Vertex AI RAG retrieval tool for the agent.

    Args:
        rag_corpus_resource: Full resource name of the RAG corpus.
            Format: projects/{project}/locations/{location}/ragCorpora/{corpus_id}
        similarity_top_k: Number of top results to return.
        vector_distance_threshold: Minimum similarity score.

    Returns:
        Configured VertexAiSearchTool.
    """
    tool = VertexAiSearchTool(
        data_store_id=rag_corpus_resource,
    )
    logger.info("RAG retrieval tool created for corpus: %s", rag_corpus_resource)
    return tool


def create_tools(
    rag_corpus_resource: str,
    similarity_top_k: int = 10,
    vector_distance_threshold: float = 0.5,
) -> list:
    """Create all tools for the agent.

    Currently returns RAG retrieval tool. Extend this to add more tools.
    """
    tools = []

    if rag_corpus_resource:
        rag_tool = create_rag_retrieval_tool(
            rag_corpus_resource=rag_corpus_resource,
            similarity_top_k=similarity_top_k,
            vector_distance_threshold=vector_distance_threshold,
        )
        tools.append(rag_tool)
    else:
        logger.warning("No RAG_CORPUS_RESOURCE configured — agent will have no retrieval tool")

    return tools
