"""Agent tools — RAG retrieval via Vertex AI RAG Engine or Vector Search.

Supports three retrieval modes (in priority order):
1. ADK VertexAiSearchTool — when RAG_CORPUS_RESOURCE is set
2. Direct Vertex AI Vector Search — when VECTOR_SEARCH_INDEX_ENDPOINT is set
3. Auto-discovery from GCS — reads pipeline_outputs/vector_db_config.json from GCS_BUCKET
4. Pure LLM mode — no retrieval, agent answers from parametric knowledge only
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _auto_discover_vector_db(project: str, gcs_bucket: str) -> dict:
    """Try to load vector DB config written by Pipeline Phase 1.

    Reads gs://{gcs_bucket}/pipeline_outputs/vector_db_config.json.
    Returns config dict or empty dict if not found.
    """
    if not gcs_bucket:
        return {}
    try:
        from google.cloud import storage

        client = storage.Client(project=project)
        bucket = client.bucket(gcs_bucket)
        blob = bucket.blob("pipeline_outputs/vector_db_config.json")

        if blob.exists():
            content = blob.download_as_text()
            config = json.loads(content)
            logger.info(
                "Auto-discovered vector DB from GCS: endpoint=%s",
                config.get("endpoint_resource_name", "unknown"),
            )
            return config
    except Exception as e:
        logger.warning("Could not auto-discover vector DB from GCS: %s", e)

    return {}


def _create_vector_search_tool(
    project: str,
    location: str,
    index_endpoint_name: str,
    deployed_index_id: str,
    embedding_model: str,
):
    """Create a callable tool that queries Vertex AI Vector Search directly.

    Returns a function that ADK can use as a function tool.
    """
    from google.cloud import aiplatform
    from langchain_google_vertexai import VertexAIEmbeddings

    aiplatform.init(project=project, location=location)
    embedder = VertexAIEmbeddings(model_name=embedding_model, project=project, location=location)
    endpoint = aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name)

    def search_knowledge_base(query: str) -> str:
        """Search the knowledge base for relevant information.

        Use this tool to find relevant context from company documents,
        policies, and technical documentation to answer the user's question.

        Args:
            query: The search query describing what information is needed.

        Returns:
            Relevant text passages from the knowledge base.
        """
        try:
            query_embedding = embedder.embed_query(query)
            results = endpoint.find_neighbors(
                deployed_index_id=deployed_index_id,
                queries=[query_embedding],
                num_neighbors=5,
            )
            if not results or not results[0]:
                return "No relevant documents found in the knowledge base."
            passages = []
            for i, neighbor in enumerate(results[0]):
                passages.append(
                    f"[Source {i + 1}] (score: {neighbor.distance:.3f}) ID: {neighbor.id}"
                )
            return "\n\n".join(passages) if passages else "No relevant documents found."
        except Exception as e:
            logger.error("Vector Search query failed: %s", e)
            return "Knowledge base search temporarily unavailable."

    return search_knowledge_base


def create_rag_retrieval_tool(
    rag_corpus_resource: str,
    similarity_top_k: int = 10,
    vector_distance_threshold: float = 0.5,
):
    """Create a Vertex AI RAG retrieval tool (ADK-native VertexAiSearchTool)."""
    from google.adk.tools import VertexAiSearchTool

    tool = VertexAiSearchTool(data_store_id=rag_corpus_resource)
    logger.info("RAG retrieval tool created for corpus: %s", rag_corpus_resource)
    return tool


def create_tools(
    rag_corpus_resource: str = "",
    similarity_top_k: int = 10,
    vector_distance_threshold: float = 0.5,
    project: str = "",
    location: str = "",
    vector_search_index_endpoint: str = "",
    deployed_index_id: str = "",
    embedding_model: str = "text-embedding-004",
    gcs_bucket: str = "",
) -> list:
    """Create all tools for the agent.

    Retrieval mode priority:
    1. RAG_CORPUS_RESOURCE → ADK VertexAiSearchTool (Vertex AI Search / RAG Engine)
    2. VECTOR_SEARCH_INDEX_ENDPOINT → Direct Vector Search query
    3. GCS_BUCKET auto-discovery → Reads pipeline output, then uses Vector Search
    4. No retrieval → Agent answers from parametric knowledge only
    """
    tools = []

    # Mode 1: ADK VertexAiSearchTool
    if rag_corpus_resource:
        rag_tool = create_rag_retrieval_tool(
            rag_corpus_resource=rag_corpus_resource,
            similarity_top_k=similarity_top_k,
            vector_distance_threshold=vector_distance_threshold,
        )
        tools.append(rag_tool)
        logger.info("Retrieval mode: ADK VertexAiSearchTool")
        return tools

    # Mode 2/3: Direct Vector Search (explicit config or auto-discovery from GCS)
    vs_endpoint = vector_search_index_endpoint
    vs_index_id = deployed_index_id
    vs_embedding = embedding_model

    if not vs_endpoint and gcs_bucket:
        # Auto-discover from Pipeline Phase 1 outputs
        db_config = _auto_discover_vector_db(project, gcs_bucket)
        vs_endpoint = db_config.get("endpoint_resource_name", "")
        vs_index_id = db_config.get("deployed_index_id", "")
        vs_embedding = db_config.get("embedding_model", vs_embedding)

    if vs_endpoint and vs_index_id:
        try:
            vs_tool = _create_vector_search_tool(
                project=project,
                location=location,
                index_endpoint_name=vs_endpoint,
                deployed_index_id=vs_index_id,
                embedding_model=vs_embedding,
            )
            tools.append(vs_tool)
            logger.info("Retrieval mode: Direct Vector Search (%s)", vs_endpoint)
        except Exception as e:
            logger.warning("Failed to create Vector Search tool: %s", e)

    # Mode 4: No retrieval
    if not tools:
        logger.warning("No retrieval configured — agent runs in pure LLM mode")

    return tools
