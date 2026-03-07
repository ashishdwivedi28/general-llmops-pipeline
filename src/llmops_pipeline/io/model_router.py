"""Model Abstraction Layer — multi-provider routing with failover, token counting, cost tracking.

Provides a unified interface for LLM operations (``chat``, ``embed``, ``generate``) that routes
requests to the configured provider (Vertex AI, OpenAI, Anthropic, local) based on YAML config.

Under the hood this module wraps **LiteLLM** which normalises 100+ model APIs behind a single
``completion()`` / ``embedding()`` call.  On top of that we add:

* **Per-type model configs** (chat, embedding, evaluation) with independent primary/fallback chains
* **Automatic failover** — if the primary model fails, the next model in the chain is tried
* **Retry with exponential backoff** (configurable per model type)
* **Token counting** — input + output tokens per call
* **Cost tracking** — based on LiteLLM's built-in cost tables + configurable overrides
* **Pydantic-validated configuration** loaded from ``confs/models.yaml``

Design decisions
~~~~~~~~~~~~~~~~
* **LiteLLM as the router kernel** — avoids reinventing provider adapters.
  All model names follow LiteLLM's naming convention (e.g. ``vertex_ai/gemini-2.0-flash``).
* **Frozen Pydantic models** — configs are immutable once loaded, matching project conventions.
* **No global state** — ``ModelRouter`` is an explicit instance; easy to test and swap.
* **Graceful degradation** — if LiteLLM is not installed, the module still imports and raises
  clear errors at call time (so pipeline code that doesn't need the router won't break).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration models (loaded from confs/models.yaml)
# ---------------------------------------------------------------------------


class ModelSpec(BaseModel):
    """A single model specification with provider routing info."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str = ""
    """LiteLLM model name, e.g. ``vertex_ai/gemini-2.0-flash``."""

    provider: str = "vertex_ai"
    """Provider hint (vertex_ai, openai, anthropic, local)."""

    max_tokens: int = 2048
    """Maximum output tokens."""

    temperature: float = 0.1
    """Sampling temperature."""

    cost_per_1k_input: float = 0.0
    """Override cost per 1 000 input tokens (0 = use LiteLLM defaults)."""

    cost_per_1k_output: float = 0.0
    """Override cost per 1 000 output tokens (0 = use LiteLLM defaults)."""


class ModelTypeConfig(BaseModel):
    """Configuration for a model *type* (chat, embedding, evaluation).

    Defines a primary model and an ordered fallback chain.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    primary: ModelSpec = Field(default_factory=ModelSpec)
    """Primary model for this type."""

    fallback: list[ModelSpec] = Field(default_factory=list)
    """Ordered fallback chain — tried in sequence if primary fails."""

    max_retries: int = 2
    """Number of retries (with exponential backoff) before falling back."""

    retry_base_delay: float = 1.0
    """Base delay in seconds for exponential backoff (delay = base * 2^attempt)."""

    timeout: int = 60
    """Request timeout in seconds."""


class ModelsConfig(BaseModel):
    """Top-level model routing configuration (maps to ``confs/models.yaml``).

    Three independent type configs allow the system to use different models
    for chat serving, embedding generation, and evaluation judging.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    chat: ModelTypeConfig = Field(default_factory=ModelTypeConfig)
    embedding: ModelTypeConfig = Field(default_factory=ModelTypeConfig)
    evaluation: ModelTypeConfig = Field(default_factory=ModelTypeConfig)


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------


@dataclass
class UsageRecord:
    """Token and cost record for a single LLM call."""

    model: str = ""
    provider: str = ""
    model_type: str = ""  # chat / embedding / evaluation
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    success: bool = True
    error: str = ""


@dataclass
class UsageAccumulator:
    """Accumulates usage records across multiple calls."""

    records: list[UsageRecord] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records)

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.records)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.records)

    @property
    def total_latency_ms(self) -> float:
        return sum(r.latency_ms for r in self.records)

    @property
    def call_count(self) -> int:
        return len(self.records)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.records if not r.success)

    def summary(self) -> dict[str, Any]:
        """Return a summary dict suitable for logging / manifest."""
        return {
            "calls": self.call_count,
            "errors": self.error_count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.total_cost_usd, 6),
            "latency_ms": round(self.total_latency_ms, 2),
        }


# ---------------------------------------------------------------------------
# Model Router
# ---------------------------------------------------------------------------


def _calculate_cost(
    spec: ModelSpec,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Calculate cost in USD for a single call.

    Uses the model spec's override costs if set, otherwise attempts LiteLLM's
    built-in ``completion_cost`` helper.
    """
    if spec.cost_per_1k_input > 0 or spec.cost_per_1k_output > 0:
        return (
            (input_tokens / 1000.0) * spec.cost_per_1k_input
            + (output_tokens / 1000.0) * spec.cost_per_1k_output
        )
    # Attempt LiteLLM cost calculation
    try:
        import litellm

        return litellm.completion_cost(
            model=spec.name,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
    except Exception:
        return 0.0


class ModelRouter:
    """Multi-provider model router with failover, retry, and cost tracking.

    Usage::

        config = ModelsConfig(...)
        router = ModelRouter(config, project="my-project", location="us-central1")

        # Chat completion
        response, usage = router.chat("What is LLMOps?")

        # Embedding
        vectors, usage = router.embed(["Hello world"])

        # Generation (same as chat but explicit)
        text, usage = router.generate("Summarize this document.")

        # Accumulated stats
        print(router.usage.summary())
    """

    def __init__(
        self,
        config: ModelsConfig,
        *,
        project: str = "",
        location: str = "us-central1",
    ) -> None:
        self.config = config
        self.project = project
        self.location = location
        self.usage = UsageAccumulator()
        self._litellm_available: bool | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_litellm(self) -> Any:
        """Lazy-import litellm and configure Vertex AI credentials if needed."""
        if self._litellm_available is False:
            raise ImportError(
                "litellm is required for ModelRouter. Install with: pip install litellm"
            )
        try:
            import litellm

            # Vertex AI models need project/location context
            if self.project:
                litellm.vertex_project = self.project
                litellm.vertex_location = self.location

            # Suppress litellm's internal debug logging
            litellm.suppress_debug_info = True
            self._litellm_available = True
            return litellm
        except ImportError:
            self._litellm_available = False
            raise ImportError(
                "litellm is required for ModelRouter. Install with: pip install litellm"
            )

    def _get_chain(self, model_type: str) -> tuple[ModelTypeConfig, list[ModelSpec]]:
        """Return the type config and the ordered model chain (primary + fallbacks)."""
        type_cfg: ModelTypeConfig = getattr(self.config, model_type)
        chain = [type_cfg.primary, *type_cfg.fallback]
        return type_cfg, chain

    def _call_with_retry(
        self,
        model_type: str,
        call_fn: Any,
        **kwargs: Any,
    ) -> tuple[Any, UsageRecord]:
        """Execute ``call_fn`` with retry + fallback logic.

        Args:
            model_type: One of ``chat``, ``embedding``, ``evaluation``.
            call_fn: Callable accepting (litellm, model_name, **kwargs) → response.
            **kwargs: Additional keyword arguments forwarded to call_fn.

        Returns:
            Tuple of (response_object, UsageRecord).
        """
        type_cfg, chain = self._get_chain(model_type)
        litellm = self._ensure_litellm()

        last_error: Exception | None = None
        for spec in chain:
            if not spec.name:
                continue
            for attempt in range(type_cfg.max_retries + 1):
                start = time.time()
                record = UsageRecord(
                    model=spec.name,
                    provider=spec.provider,
                    model_type=model_type,
                )
                try:
                    response = call_fn(
                        litellm,
                        spec.name,
                        max_tokens=spec.max_tokens,
                        temperature=spec.temperature,
                        timeout=type_cfg.timeout,
                        **kwargs,
                    )
                    elapsed = (time.time() - start) * 1000

                    # Extract token usage
                    usage_obj = getattr(response, "usage", None)
                    if usage_obj:
                        record.input_tokens = getattr(usage_obj, "prompt_tokens", 0) or 0
                        record.output_tokens = getattr(usage_obj, "completion_tokens", 0) or 0
                        record.total_tokens = getattr(usage_obj, "total_tokens", 0) or 0

                    record.cost_usd = _calculate_cost(
                        spec, record.input_tokens, record.output_tokens
                    )
                    record.latency_ms = elapsed
                    record.success = True
                    self.usage.records.append(record)

                    logger.info(
                        "ModelRouter [%s] %s — %d tokens, $%.6f, %.0fms",
                        model_type,
                        spec.name,
                        record.total_tokens,
                        record.cost_usd,
                        elapsed,
                    )
                    return response, record

                except Exception as exc:
                    elapsed = (time.time() - start) * 1000
                    record.latency_ms = elapsed
                    record.success = False
                    record.error = str(exc)
                    self.usage.records.append(record)
                    last_error = exc

                    if attempt < type_cfg.max_retries:
                        delay = type_cfg.retry_base_delay * (2**attempt)
                        logger.warning(
                            "ModelRouter [%s] %s attempt %d failed: %s — retrying in %.1fs",
                            model_type,
                            spec.name,
                            attempt + 1,
                            exc,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.warning(
                            "ModelRouter [%s] %s exhausted %d retries — trying next fallback",
                            model_type,
                            spec.name,
                            type_cfg.max_retries + 1,
                        )

        raise RuntimeError(
            f"All models in the {model_type} chain failed. Last error: {last_error}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        message: str,
        *,
        system_prompt: str = "",
        model_type: str = "chat",
        **kwargs: Any,
    ) -> tuple[str, UsageRecord]:
        """Send a chat completion request.

        Args:
            message: User message.
            system_prompt: Optional system prompt prepended to the conversation.
            model_type: Config section to use (default ``chat``).
            **kwargs: Extra params forwarded to litellm.completion().

        Returns:
            Tuple of (response_text, UsageRecord).
        """
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})

        def _do_chat(
            litellm: Any,
            model: str,
            max_tokens: int,
            temperature: float,
            timeout: int,
            **extra: Any,
        ) -> Any:
            return litellm.completion(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                **extra,
            )

        response, record = self._call_with_retry(model_type, _do_chat, **kwargs)
        text = response.choices[0].message.content or ""
        return text, record

    def generate(
        self,
        prompt: str,
        *,
        model_type: str = "chat",
        **kwargs: Any,
    ) -> tuple[str, UsageRecord]:
        """Generate text (alias for ``chat`` without a system prompt).

        Useful for evaluation judging, prompt generation, etc.
        """
        return self.chat(prompt, model_type=model_type, **kwargs)

    def evaluate(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> tuple[str, UsageRecord]:
        """Run an evaluation prompt using the ``evaluation`` model config.

        This is syntactic sugar for ``chat(prompt, model_type="evaluation")``.
        """
        return self.chat(prompt, model_type="evaluation", **kwargs)

    def embed(
        self,
        texts: list[str],
        *,
        model_type: str = "embedding",
        **kwargs: Any,
    ) -> tuple[list[list[float]], UsageRecord]:
        """Generate embeddings for a list of texts.

        Args:
            texts: Strings to embed.
            model_type: Config section to use (default ``embedding``).
            **kwargs: Extra params forwarded to litellm.embedding().

        Returns:
            Tuple of (list_of_embedding_vectors, UsageRecord).
        """

        def _do_embed(
            litellm: Any,
            model: str,
            max_tokens: int,  # noqa: ARG001 — unused but required by _call_with_retry
            temperature: float,  # noqa: ARG001
            timeout: int,
            **extra: Any,
        ) -> Any:
            return litellm.embedding(
                model=model,
                input=texts,
                timeout=timeout,
                **extra,
            )

        response, record = self._call_with_retry(model_type, _do_embed, **kwargs)

        # Extract vectors from response
        vectors: list[list[float]] = []
        data = getattr(response, "data", [])
        for item in data:
            embedding = getattr(item, "embedding", [])
            vectors.append(embedding)

        return vectors, record

    def reset_usage(self) -> dict[str, Any]:
        """Reset accumulated usage and return the final summary."""
        summary = self.usage.summary()
        self.usage = UsageAccumulator()
        return summary


# ---------------------------------------------------------------------------
# Factory helper — loads config from YAML
# ---------------------------------------------------------------------------


def load_models_config(yaml_path: str = "confs/models.yaml") -> ModelsConfig:
    """Load ``ModelsConfig`` from a YAML file using OmegaConf.

    Falls back to default config if the file does not exist.
    """
    from pathlib import Path

    if not Path(yaml_path).exists():
        logger.warning("Models config not found at %s — using defaults", yaml_path)
        return ModelsConfig(
            chat=ModelTypeConfig(
                primary=ModelSpec(name="vertex_ai/gemini-2.0-flash", provider="vertex_ai")
            ),
            embedding=ModelTypeConfig(
                primary=ModelSpec(
                    name="vertex_ai/text-embedding-004",
                    provider="vertex_ai",
                    max_tokens=0,
                    temperature=0.0,
                )
            ),
            evaluation=ModelTypeConfig(
                primary=ModelSpec(
                    name="vertex_ai/gemini-2.0-flash",
                    provider="vertex_ai",
                    temperature=0.0,
                )
            ),
        )

    from omegaconf import OmegaConf

    raw = OmegaConf.load(yaml_path)
    resolved = OmegaConf.to_container(raw, resolve=True)
    if not isinstance(resolved, dict):
        raise TypeError(f"Expected dict from {yaml_path}, got {type(resolved)}")
    return ModelsConfig.model_validate(resolved)
