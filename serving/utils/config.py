"""Server configuration — Pydantic Settings loaded from environment variables."""

from __future__ import annotations

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class ServerConfig(BaseSettings):
    """Configuration for the ADK agent serving layer.

    All values come from env vars (or a ``.env`` file).
    Manifest-aware: when ``MANIFEST_ENABLED`` is True the serving layer reads
    artifact configuration from the pipeline manifest instead of requiring
    explicit env-var overrides for vector-search endpoints, models, etc.
    """

    model_config = ConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

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

    # Vector Search (from Pipeline Phase 1 — fallback when RAG_CORPUS_RESOURCE is empty)
    VECTOR_SEARCH_INDEX_ENDPOINT: str = ""  # projects/{proj}/locations/{loc}/indexEndpoints/{id}
    VECTOR_SEARCH_DEPLOYED_INDEX_ID: str = ""  # e.g. llmops_vector_index
    GCS_BUCKET: str = ""  # For auto-discovery of pipeline outputs

    # --- Manifest (Pipeline ↔ Serving bridge) --------------------------------
    MANIFEST_ENABLED: bool = True
    MANIFEST_APP_ID: str = "llmops-app"
    MANIFEST_BUCKET: str = ""  # Falls back to GCS_BUCKET if empty
    MANIFEST_REFRESH_INTERVAL: int = 120  # Seconds between manifest re-reads

    # --- Prompt Registry -------------------------------------------------------
    PROMPT_REGISTRY_ENABLED: bool = True
    PROMPT_ACTIVE_VERSION: int = 1  # Default active prompt version

    # --- Model Router ----------------------------------------------------------
    MODELS_CONFIG_PATH: str = "confs/models.yaml"

    # --- API Gateway -----------------------------------------------------------
    GATEWAY_CONFIG_PATH: str = "confs/gateway.yaml"

    # --- BigQuery (interactions, feedback, costs) ------------------------------
    BQ_DATASET: str = ""  # e.g. "llmops"  — empty = disabled

    # --- Secret Manager --------------------------------------------------------
    SECRET_MANAGER_ENABLED: bool = False
    SECRET_PREFIX: str = "llmops"  # Secret names: llmops-{key}

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

    @property
    def manifest_bucket(self) -> str:
        """Resolve the manifest bucket — falls back to GCS_BUCKET."""
        return self.MANIFEST_BUCKET or self.GCS_BUCKET
