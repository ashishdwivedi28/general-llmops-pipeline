"""ADK Agent definition — the core LLM Agent with RAG retrieval.

Uses Google Agent Development Kit (ADK) to create a production-ready agent
with tools, callbacks, guardrails, and observability.

When a **pipeline artifact manifest** is provided the agent auto-configures
itself from pipeline outputs — using the active model, vector search endpoint,
and prompt version recorded by the offline pipeline system.

Integrates with:
- **Prompt Registry** — loads versioned system prompts instead of hardcoded strings.
- **Model Router** — resolves the serving model from config chain with fallback.
- **Manifest** — auto-discovers vector endpoints and active model/prompt version.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent
from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin

from serving.prompt import get_system_prompt, get_tool_instructions
from serving.tools import create_tools
from serving.utils.config import ServerConfig

if TYPE_CHECKING:
    from llmops_pipeline.io.manifest import PipelineManifest

logger = logging.getLogger(__name__)


def create_agent(
    config: ServerConfig | None = None,
    *,
    manifest: "PipelineManifest | None" = None,
) -> LlmAgent:
    """Create and configure the ADK LLM Agent.

    Args:
        config: Server configuration. If None, loads from environment.
        manifest: Pipeline artifact manifest. When provided, the agent uses the
            active model and vector endpoint recorded by the pipeline instead of
            relying solely on environment variables.

    Returns:
        Configured LlmAgent ready for serving.
    """
    if config is None:
        config = ServerConfig()

    # --- Resolve model from manifest (if available) --------------------------
    model_name = config.MODEL_NAME
    if manifest is not None and manifest.deployment.active_model:
        model_name = manifest.deployment.active_model
        logger.info("Using model from manifest: %s", model_name)

    logger.info("Creating agent: %s (model: %s)", config.AGENT_NAME, model_name)

    # Build tools (manifest-aware — auto-discovers vector DB from manifest)
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
        manifest=manifest,
    )

    # Build instruction — uses prompt registry (if configured) or built-in fallback
    system_prompt = get_system_prompt()

    # Enrich system prompt with tool-specific instructions from the prompt registry
    tool_instructions = get_tool_instructions()
    if tool_instructions:
        tool_section = "\n\nTool Instructions:\n"
        for tool_name, instruction in tool_instructions.items():
            tool_section += f"- {tool_name}: {instruction}\n"
        system_prompt += tool_section

    plugins = [
        GlobalInstructionPlugin(instruction=system_prompt),
    ]

    # Create the ADK agent
    agent = LlmAgent(
        name=config.AGENT_NAME,
        model=model_name,
        description=config.AGENT_DESCRIPTION,
        instruction=system_prompt,
        tools=tools,
        plugins=plugins,
    )

    logger.info("Agent created with %d tools (model: %s)", len(tools), model_name)
    return agent


# Module-level agent instance (created on import for ADK serving)
# Wrapped in try/except so importing this module in tests or pipelines
# does not crash when env vars are missing.
try:
    root_agent = create_agent()
except Exception as _e:
    logger.warning("Could not create root_agent at import time: %s", _e)
    root_agent = None  # type: ignore[assignment]
