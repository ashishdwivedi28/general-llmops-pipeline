"""Tests for the Model Router (model_router.py).

Tests cover:
- Configuration model validation
- Usage tracking (token counting, cost calculation)
- Retry and failover logic (mocked LiteLLM)
- Chat, embed, and evaluate methods
- Config loading from YAML
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from llmops_pipeline.io.model_router import (
    ModelRouter,
    ModelSpec,
    ModelTypeConfig,
    ModelsConfig,
    UsageAccumulator,
    UsageRecord,
    _calculate_cost,
    load_models_config,
)


# ---------------------------------------------------------------------------
# Configuration model tests
# ---------------------------------------------------------------------------


class TestModelSpec:
    """Test ModelSpec Pydantic validation."""

    def test_defaults(self):
        spec = ModelSpec()
        assert spec.name == ""
        assert spec.provider == "vertex_ai"
        assert spec.max_tokens == 2048
        assert spec.temperature == 0.1
        assert spec.cost_per_1k_input == 0.0

    def test_custom_values(self):
        spec = ModelSpec(
            name="openai/gpt-4o",
            provider="openai",
            max_tokens=4096,
            temperature=0.7,
            cost_per_1k_input=0.005,
            cost_per_1k_output=0.015,
        )
        assert spec.name == "openai/gpt-4o"
        assert spec.provider == "openai"
        assert spec.cost_per_1k_output == 0.015

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            ModelSpec(name="test", unknown_field="bad")


class TestModelTypeConfig:
    """Test ModelTypeConfig Pydantic validation."""

    def test_defaults(self):
        cfg = ModelTypeConfig()
        assert cfg.max_retries == 2
        assert cfg.retry_base_delay == 1.0
        assert cfg.timeout == 60
        assert cfg.fallback == []

    def test_with_fallback(self):
        cfg = ModelTypeConfig(
            primary=ModelSpec(name="primary"),
            fallback=[
                ModelSpec(name="fallback-1"),
                ModelSpec(name="fallback-2"),
            ],
        )
        assert len(cfg.fallback) == 2
        assert cfg.fallback[0].name == "fallback-1"


class TestModelsConfig:
    """Test top-level models configuration."""

    def test_defaults(self):
        cfg = ModelsConfig()
        assert cfg.chat.primary.name == ""
        assert cfg.embedding.primary.name == ""
        assert cfg.evaluation.primary.name == ""

    def test_full_config(self):
        cfg = ModelsConfig(
            chat=ModelTypeConfig(primary=ModelSpec(name="vertex_ai/gemini-2.0-flash")),
            embedding=ModelTypeConfig(primary=ModelSpec(name="vertex_ai/text-embedding-004")),
            evaluation=ModelTypeConfig(
                primary=ModelSpec(name="vertex_ai/gemini-2.0-flash", temperature=0.0)
            ),
        )
        assert cfg.chat.primary.name == "vertex_ai/gemini-2.0-flash"
        assert cfg.evaluation.primary.temperature == 0.0


# ---------------------------------------------------------------------------
# Usage tracking tests
# ---------------------------------------------------------------------------


class TestUsageAccumulator:
    """Test token and cost accumulation."""

    def test_empty(self):
        acc = UsageAccumulator()
        assert acc.call_count == 0
        assert acc.total_tokens == 0
        assert acc.total_cost_usd == 0.0

    def test_accumulates(self):
        acc = UsageAccumulator()
        acc.records.append(
            UsageRecord(
                model="m1",
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                cost_usd=0.001,
                latency_ms=200.0,
            )
        )
        acc.records.append(
            UsageRecord(
                model="m2",
                input_tokens=200,
                output_tokens=100,
                total_tokens=300,
                cost_usd=0.003,
                latency_ms=300.0,
            )
        )
        assert acc.call_count == 2
        assert acc.total_input_tokens == 300
        assert acc.total_output_tokens == 150
        assert acc.total_tokens == 450
        assert acc.total_cost_usd == pytest.approx(0.004)
        assert acc.total_latency_ms == pytest.approx(500.0)
        assert acc.error_count == 0

    def test_error_counting(self):
        acc = UsageAccumulator()
        acc.records.append(UsageRecord(success=True))
        acc.records.append(UsageRecord(success=False, error="timeout"))
        acc.records.append(UsageRecord(success=False, error="rate limit"))
        assert acc.error_count == 2

    def test_summary(self):
        acc = UsageAccumulator()
        acc.records.append(
            UsageRecord(input_tokens=10, output_tokens=5, total_tokens=15, cost_usd=0.0001)
        )
        summary = acc.summary()
        assert summary["calls"] == 1
        assert summary["input_tokens"] == 10
        assert summary["cost_usd"] == pytest.approx(0.0001)


class TestCalculateCost:
    """Test cost calculation helper."""

    def test_override_costs(self):
        spec = ModelSpec(cost_per_1k_input=0.01, cost_per_1k_output=0.03)
        cost = _calculate_cost(spec, input_tokens=1000, output_tokens=500)
        # (1000/1000)*0.01 + (500/1000)*0.03 = 0.01 + 0.015 = 0.025
        assert cost == pytest.approx(0.025)

    def test_zero_override_falls_through(self):
        spec = ModelSpec(cost_per_1k_input=0.0, cost_per_1k_output=0.0)
        # Without litellm installed in test env, should return 0.0
        cost = _calculate_cost(spec, input_tokens=100, output_tokens=50)
        assert cost == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Model Router tests (mocked LiteLLM)
# ---------------------------------------------------------------------------


def _make_router(**overrides) -> ModelRouter:
    """Create a ModelRouter with sensible test defaults."""
    config = ModelsConfig(
        chat=ModelTypeConfig(
            primary=ModelSpec(name="vertex_ai/gemini-2.0-flash"),
            fallback=[ModelSpec(name="vertex_ai/gemini-2.0-flash-lite")],
            max_retries=1,
            retry_base_delay=0.01,  # fast retries for tests
        ),
        embedding=ModelTypeConfig(
            primary=ModelSpec(
                name="vertex_ai/text-embedding-004",
                max_tokens=0,
                temperature=0.0,
            ),
            max_retries=0,
        ),
        evaluation=ModelTypeConfig(
            primary=ModelSpec(
                name="vertex_ai/gemini-2.0-flash",
                temperature=0.0,
            ),
            max_retries=0,
        ),
    )
    return ModelRouter(config, project="test-project", location="us-central1", **overrides)


@pytest.fixture()
def mock_litellm():
    """Fixture that mocks the litellm module."""
    mock = MagicMock()

    # Default completion response
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    choice = SimpleNamespace(message=SimpleNamespace(content="Test response"))
    mock.completion.return_value = SimpleNamespace(choices=[choice], usage=usage)

    # Default embedding response
    embed_item = SimpleNamespace(embedding=[0.1, 0.2, 0.3])
    mock.embedding.return_value = SimpleNamespace(
        data=[embed_item],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=0, total_tokens=5),
    )

    # cost helper
    mock.completion_cost.return_value = 0.0001

    with patch("llmops_pipeline.io.model_router.ModelRouter._ensure_litellm", return_value=mock):
        yield mock


class TestModelRouterChat:
    """Test chat completion through the router."""

    def test_chat_success(self, mock_litellm):
        router = _make_router()
        text, record = router.chat("Hello")
        assert text == "Test response"
        assert record.success is True
        assert record.input_tokens == 10
        assert record.output_tokens == 20
        assert record.model == "vertex_ai/gemini-2.0-flash"
        assert router.usage.call_count == 1

    def test_chat_with_system_prompt(self, mock_litellm):
        router = _make_router()
        text, record = router.chat("Hello", system_prompt="Be brief.")
        assert text == "Test response"
        # Verify the completion was called with system + user messages
        call_args = mock_litellm.completion.call_args
        messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
        assert len(messages) == 2
        assert messages[0]["role"] == "system"

    def test_chat_failover(self, mock_litellm):
        """Primary model fails, should fall back to secondary."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            model = kwargs.get("model", args[0] if args else "")
            if "gemini-2.0-flash-lite" not in model:
                raise RuntimeError("Primary model unavailable")
            usage = SimpleNamespace(prompt_tokens=5, completion_tokens=10, total_tokens=15)
            choice = SimpleNamespace(message=SimpleNamespace(content="Fallback response"))
            return SimpleNamespace(choices=[choice], usage=usage)

        mock_litellm.completion.side_effect = side_effect
        router = _make_router()
        text, record = router.chat("Hello")
        assert text == "Fallback response"
        assert record.model == "vertex_ai/gemini-2.0-flash-lite"
        # Primary retried once (max_retries=1) + 1 initial = 2, then fallback succeeds = 3
        assert router.usage.call_count >= 2

    def test_chat_all_fail(self, mock_litellm):
        """All models fail — should raise RuntimeError."""
        mock_litellm.completion.side_effect = RuntimeError("All down")
        router = _make_router()
        with pytest.raises(RuntimeError, match="All models in the chat chain failed"):
            router.chat("Hello")


class TestModelRouterEmbed:
    """Test embedding through the router."""

    def test_embed_success(self, mock_litellm):
        router = _make_router()
        vectors, record = router.embed(["Hello world"])
        assert len(vectors) == 1
        assert vectors[0] == [0.1, 0.2, 0.3]
        assert record.success is True
        assert record.model_type == "embedding"


class TestModelRouterEvaluate:
    """Test evaluation through the router."""

    def test_evaluate_uses_evaluation_config(self, mock_litellm):
        router = _make_router()
        text, record = router.evaluate("Rate this answer.")
        assert text == "Test response"
        assert record.model_type == "evaluation"


class TestModelRouterUsageReset:
    """Test usage reset."""

    def test_reset_usage(self, mock_litellm):
        router = _make_router()
        router.chat("Hello")
        assert router.usage.call_count == 1
        summary = router.reset_usage()
        assert summary["calls"] == 1
        assert router.usage.call_count == 0


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestLoadModelsConfig:
    """Test YAML config loading."""

    def test_fallback_to_defaults(self, tmp_path):
        """When the config file doesn't exist, defaults are returned."""
        cfg = load_models_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.chat.primary.name == "vertex_ai/gemini-2.0-flash"
        assert cfg.embedding.primary.name == "vertex_ai/text-embedding-004"

    def test_loads_from_yaml(self, tmp_path):
        """Load a valid YAML config."""
        yaml_content = """\
chat:
  primary:
    name: "openai/gpt-4o"
    provider: "openai"
    max_tokens: 4096
    temperature: 0.5
    cost_per_1k_input: 0.005
    cost_per_1k_output: 0.015
  fallback: []
  max_retries: 3
  retry_base_delay: 2.0
  timeout: 90
embedding:
  primary:
    name: "vertex_ai/text-embedding-004"
    provider: "vertex_ai"
    max_tokens: 0
    temperature: 0.0
    cost_per_1k_input: 0.0
    cost_per_1k_output: 0.0
  fallback: []
  max_retries: 2
  retry_base_delay: 1.0
  timeout: 60
evaluation:
  primary:
    name: "vertex_ai/gemini-2.0-flash"
    provider: "vertex_ai"
    max_tokens: 2048
    temperature: 0.0
    cost_per_1k_input: 0.0
    cost_per_1k_output: 0.0
  fallback: []
  max_retries: 2
  retry_base_delay: 1.0
  timeout: 120
"""
        yaml_file = tmp_path / "models.yaml"
        yaml_file.write_text(yaml_content)
        cfg = load_models_config(str(yaml_file))
        assert cfg.chat.primary.name == "openai/gpt-4o"
        assert cfg.chat.primary.cost_per_1k_input == 0.005
        assert cfg.chat.max_retries == 3
