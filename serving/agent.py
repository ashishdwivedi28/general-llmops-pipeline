"""ADK Agent definition — the core LLM Agent with RAG retrieval.

Uses Google Agent Development Kit (ADK) to create a production-ready agent
with tools, callbacks, guardrails, and observability.
"""

from __future__ import annotations

import logging

from google.adk.agents import LlmAgent
from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin

from serving.prompt import get_system_prompt
from serving.tools import create_tools
from serving.utils.config import ServerConfig

logger = logging.getLogger(__name__)


def create_agent(config: ServerConfig | None = None) -> LlmAgent:
    """Create and configure the ADK LLM Agent.

    Args:
        config: Server configuration. If None, loads from environment.

    Returns:
        Configured LlmAgent ready for serving.
    """
    if config is None:
        config = ServerConfig()

    logger.info("Creating agent: %s (model: %s)", config.AGENT_NAME, config.MODEL_NAME)

    # Build tools (auto-discovers Vector DB from GCS if env vars not set)
    tools = create_tools(
        rag_corpus_resource=config.RAG_CORPUS_RESOURCE,
        similarity_top_k=config.RAG_SIMILARITY_TOP_K,
        vector_distance_threshold=config.RAG_VECTOR_DISTANCE_THRESHOLD,
        project=config.GCP_PROJECT_ID,
        location=config.GCP_LOCATION,
        vector_search_index_endpoint=config.VECTOR_SEARCH_INDEX_ENDPOINT,
        deployed_index_id=config.VECTOR_SEARCH_DEPLOYED_INDEX_ID,
        embedding_model=config.EMBEDDING_MODEL,
        gcs_bucket=config.GCS_BUCKET,
    )

    # Build instruction plugin
    plugins = [
        GlobalInstructionPlugin(instruction=get_system_prompt()),
    ]

    # Create the ADK agent
    agent = LlmAgent(
        name=config.AGENT_NAME,
        model=config.MODEL_NAME,
        description=config.AGENT_DESCRIPTION,
        instruction=get_system_prompt(),
        tools=tools,
        plugins=plugins,
    )

    logger.info("Agent created with %d tools", len(tools))
    return agent


# Module-level agent instance (created on import for ADK serving)
# Wrapped in try/except so importing this module in tests or pipelines
# does not crash when env vars are missing.
try:
    root_agent = create_agent()
except Exception as _e:
    logger.warning("Could not create root_agent at import time: %s", _e)
    root_agent = None  # type: ignore[assignment]
