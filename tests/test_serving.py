"""Tests for serving layer components."""

from __future__ import annotations


def test_server_config_defaults():
    """Test ServerConfig loads defaults."""
    from serving.utils.config import ServerConfig

    config = ServerConfig()
    assert config.PORT == 8080
    assert config.MODEL_NAME == "gemini-2.0-flash"
    assert config.GCP_LOCATION == "us-central1"
    # Manifest defaults
    assert config.MANIFEST_ENABLED is True
    assert config.MANIFEST_APP_ID == "llmops-app"
    assert config.MANIFEST_REFRESH_INTERVAL == 120
    # Prompt registry defaults
    assert config.PROMPT_REGISTRY_ENABLED is True
    assert config.PROMPT_ACTIVE_VERSION == 1
    # Model router defaults
    assert config.MODELS_CONFIG_PATH == "confs/models.yaml"


def test_server_config_manifest_bucket_fallback():
    """Test manifest_bucket property falls back to GCS_BUCKET."""
    from serving.utils.config import ServerConfig

    config = ServerConfig(GCS_BUCKET="my-bucket")
    assert config.manifest_bucket == "my-bucket"

    config2 = ServerConfig(GCS_BUCKET="my-bucket", MANIFEST_BUCKET="manifest-bucket")
    assert config2.manifest_bucket == "manifest-bucket"


def test_guardrail_checker():
    """Test input/output guardrails."""
    from serving.callbacks import GuardrailChecker

    checker = GuardrailChecker(
        valid_topics=["hr", "policy"],
        invalid_topics=["politics", "religion"],
    )

    # Valid input
    allowed, reason = checker.check_input("What is the leave policy?")
    assert allowed

    # Invalid input
    allowed, reason = checker.check_input("Tell me about politics")
    assert not allowed

    # PII check in output
    allowed, reason = checker.check_output("Your SSN: 123-45-6789")
    assert not allowed


def test_prompt_functions():
    """Test prompt generation (without registry — fallback to builtins)."""
    from serving.prompt import get_system_prompt, get_query_rewriter_prompt, get_refusal_prompt

    prompt = get_system_prompt()
    assert "helpful" in prompt.lower()
    assert "knowledge base" in prompt.lower()

    rewriter = get_query_rewriter_prompt("leave policy")
    assert "leave policy" in rewriter

    refusal = get_refusal_prompt()
    assert "sorry" in refusal.lower() or "only help" in refusal.lower()


def test_prompt_functions_with_registry(tmp_path, monkeypatch):
    """Test prompt generation with a registry configured."""
    monkeypatch.setattr(
        "llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path
    )
    from llmops_pipeline.io.prompt_registry import PromptRegistry, PromptRegistryConfig, PromptVersion, save_prompt
    from serving.prompt import set_prompt_registry, get_system_prompt

    save_prompt(
        PromptVersion(version=1, system_prompt="Custom registry prompt."),
        app_id="test-app",
        bucket_name="__local__",
    )
    registry = PromptRegistry(
        config=PromptRegistryConfig(
            app_id="test-app", bucket_name="__local__", active_version=1
        )
    )
    set_prompt_registry(registry)
    prompt = get_system_prompt()
    assert prompt == "Custom registry prompt."

    # Clean up — reset to None so other tests aren't affected
    set_prompt_registry(None)
