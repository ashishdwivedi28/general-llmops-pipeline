"""Tests for serving layer components."""

from __future__ import annotations

import pytest


def test_server_config_defaults():
    """Test ServerConfig loads defaults."""
    from serving.utils.config import ServerConfig

    config = ServerConfig()
    assert config.PORT == 8080
    assert config.MODEL_NAME == "gemini-2.0-flash"
    assert config.GCP_LOCATION == "us-central1"


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
    """Test prompt generation."""
    from serving.prompt import get_system_prompt, get_query_rewriter_prompt

    prompt = get_system_prompt()
    assert "helpful" in prompt.lower()
    assert "knowledge base" in prompt.lower()

    rewriter = get_query_rewriter_prompt("leave policy")
    assert "leave policy" in rewriter
