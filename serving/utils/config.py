"""Server configuration — Pydantic Settings loaded from environment variables."""

from __future__ import annotations

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class ServerConfig(BaseSettings):
    """Configuration for the ADK agent serving layer.

    All values come from env vars (or a ``.env`` file).
    """

    model_config = ConfigDict(env_file=".env", case_sensitive=True)

    # GCP
    GCP_PROJECT_ID: str = ""
    GCP_LOCATION: str = "us-central1"

    # Model
    MODEL_NAME: str = "gemini-2.0-flash"
    EMBEDDING_MODEL: str = "text-embedding-004"

    # Agent
    AGENT_NAME: str = "llmops-rag-agent"
    AGENT_DESCRIPTION: str = "RAG-powered chatbot agent built with Google ADK"

    # RAG Corpus (Vertex AI RAG Engine)
    RAG_CORPUS_RESOURCE: str = ""  # projects/{proj}/locations/{loc}/ragCorpora/{id}
    RAG_SIMILARITY_TOP_K: int = 10
    RAG_VECTOR_DISTANCE_THRESHOLD: float = 0.5

    # Guardrails
    VALID_TOPICS: list[str] = []
    INVALID_TOPICS: list[str] = []

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    LOG_LEVEL: str = "info"

    # Observability
    ENABLE_TRACING: bool = True
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
