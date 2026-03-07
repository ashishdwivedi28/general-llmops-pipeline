"""Agent tools — RAG retrieval via Vertex AI RAG Engine or Vector Search.

Supports four retrieval modes (in priority order):
1. ADK VertexAiSearchTool — when RAG_CORPUS_RESOURCE is set
2. Direct Vertex AI Vector Search — when VECTOR_SEARCH_INDEX_ENDPOINT is set
3. **Manifest-based discovery** — reads pipeline artifact manifest from GCS for
   vector endpoint, deployed index ID, embedding model, and chunk metadata
4. Legacy auto-discovery from GCS — reads pipeline_outputs/vector_db_config.json
5. Pure LLM mode — no retrieval, agent answers from parametric knowledge only

The manifest (mode 3) is the preferred bridge between the offline pipeline system
and the online serving layer.  See ``llmops_pipeline.io.manifest`` for details.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmops_pipeline.io.manifest import PipelineManifest

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


def _resolve_from_manifest(manifest: "PipelineManifest | None") -> dict:
    """Extract vector-search config from the pipeline artifact manifest.

    Returns a dict with keys matching ``_auto_discover_vector_db`` format
    so the rest of the tool-creation logic can consume it uniformly.
    """
    if manifest is None:
        return {}
    fe = manifest.feature_engineering
    if not fe.vector_endpoint_resource_name:
        return {}
    logger.info(
        "Resolved vector search from manifest: endpoint=%s, index_id=%s",
        fe.vector_endpoint_resource_name,
        fe.deployed_index_id,
    )
    return {
        "endpoint_resource_name": fe.vector_endpoint_resource_name,
        "deployed_index_id": fe.deployed_index_id,
        "embedding_model": fe.embedding_model,
    }


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
    manifest: "PipelineManifest | None" = None,
) -> list:
    """Create all tools for the agent.

    Retrieval mode priority:
    1. RAG_CORPUS_RESOURCE → ADK VertexAiSearchTool (Vertex AI Search / RAG Engine)
    2. VECTOR_SEARCH_INDEX_ENDPOINT → Direct Vector Search query (explicit env var)
    3. **Manifest** → Pipeline artifact manifest vector endpoint (the bridge)
    4. GCS_BUCKET auto-discovery → Reads pipeline output, then uses Vector Search
    5. No retrieval → Agent answers from parametric knowledge only
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

    # Mode 2: Explicit env var config
    vs_endpoint = vector_search_index_endpoint
    vs_index_id = deployed_index_id
    vs_embedding = embedding_model

    # Mode 3: Manifest-based discovery (the bridge)
    if not vs_endpoint and manifest is not None:
        manifest_cfg = _resolve_from_manifest(manifest)
        vs_endpoint = manifest_cfg.get("endpoint_resource_name", "")
        vs_index_id = manifest_cfg.get("deployed_index_id", vs_index_id)
        vs_embedding = manifest_cfg.get("embedding_model", vs_embedding)

    # Mode 4: Legacy auto-discovery from GCS
    if not vs_endpoint and gcs_bucket:
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

    # Mode 5: No retrieval
    if not tools:
        logger.warning("No retrieval configured — agent runs in pure LLM mode")

    return tools
