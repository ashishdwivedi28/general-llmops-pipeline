"""Tests for the Prompt Registry (prompt_registry.py).

Tests cover:
- PromptVersion model validation
- Local file read/write round-trip
- Version listing
- Template variable resolution
- A/B traffic splitting
- PromptRegistry facade (caching, active version)
- GCS operations (mocked)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llmops_pipeline.io.prompt_registry import (
    PromptRegistry,
    PromptRegistryConfig,
    PromptVersion,
    _default_prompt,
    load_prompt,
    list_prompt_versions,
    resolve_variables,
    save_prompt,
    select_prompt_version_ab,
)


# ---------------------------------------------------------------------------
# PromptVersion model tests
# ---------------------------------------------------------------------------


class TestPromptVersionModel:
    """Test Pydantic model for prompt versions."""

    def test_defaults(self):
        p = PromptVersion()
        assert p.version == 1
        assert p.system_prompt == ""
        assert p.tool_instructions == {}
        assert p.variables == {}

    def test_full_construction(self):
        p = PromptVersion(
            version=3,
            created_at="2026-03-06T00:00:00Z",
            description="Added citations",
            system_prompt="You are a helpful assistant.",
            query_rewriter_prompt="Rewrite: {query}",
            refusal_prompt="I cannot help with that.",
            tool_instructions={"rag_search": "Use for HR questions."},
            variables={"company_name": "Acme Corp"},
            eval_scores={"relevance": 0.92},
        )
        assert p.version == 3
        assert p.tool_instructions["rag_search"] == "Use for HR questions."
        assert p.eval_scores["relevance"] == 0.92

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            PromptVersion(version=1, unknown_field="bad")


class TestDefaultPrompt:
    """Test the built-in default prompt."""

    def test_contains_key_phrases(self):
        p = _default_prompt(1)
        assert p.version == 1
        assert "helpful" in p.system_prompt.lower()
        assert "knowledge base" in p.system_prompt.lower()
        assert "{query}" in p.query_rewriter_prompt
        assert p.refusal_prompt != ""


# ---------------------------------------------------------------------------
# Local file I/O tests
# ---------------------------------------------------------------------------


class TestLocalPromptIO:
    """Test local file read/write for prompts."""

    def test_save_and_load_local(self, tmp_path, monkeypatch):
        """Round-trip save + load via local files."""
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        prompt = PromptVersion(
            version=2,
            description="Test prompt",
            system_prompt="You are a test assistant.",
            query_rewriter_prompt="Rewrite: {query}",
            refusal_prompt="Cannot help.",
        )

        path = save_prompt(prompt, app_id="test-app", bucket_name="__local__")
        assert "v2.yaml" in path

        loaded = load_prompt(2, app_id="test-app", bucket_name="__local__")
        assert loaded.version == 2
        assert loaded.system_prompt == "You are a test assistant."
        assert loaded.description == "Test prompt"

    def test_load_nonexistent_returns_default(self, tmp_path, monkeypatch):
        """Loading a missing version returns the default prompt."""
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        loaded = load_prompt(99, app_id="test-app", bucket_name="__local__")
        assert loaded.version == 99
        assert "helpful" in loaded.system_prompt.lower()

    def test_save_multiple_versions(self, tmp_path, monkeypatch):
        """Save multiple versions and verify they coexist."""
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        for v in [1, 2, 3]:
            save_prompt(
                PromptVersion(version=v, system_prompt=f"Version {v}"),
                app_id="multi-app",
                bucket_name="__local__",
            )

        for v in [1, 2, 3]:
            loaded = load_prompt(v, app_id="multi-app", bucket_name="__local__")
            assert loaded.version == v
            assert loaded.system_prompt == f"Version {v}"


# ---------------------------------------------------------------------------
# Version listing tests
# ---------------------------------------------------------------------------


class TestListVersions:
    """Test listing available prompt versions."""

    def test_list_local_versions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        for v in [3, 1, 5]:
            save_prompt(
                PromptVersion(version=v),
                app_id="list-app",
                bucket_name="__local__",
            )

        versions = list_prompt_versions(app_id="list-app", bucket_name="__local__")
        assert versions == [1, 3, 5]

    def test_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)
        versions = list_prompt_versions(app_id="empty-app", bucket_name="__local__")
        assert versions == []


# ---------------------------------------------------------------------------
# Template variable resolution tests
# ---------------------------------------------------------------------------


class TestResolveVariables:
    """Test ${VAR} substitution in prompts."""

    def test_substitutes_from_dict(self):
        text = "Hello ${name}, welcome to ${company}."
        result = resolve_variables(text, {"name": "Alice", "company": "Acme"})
        assert result == "Hello Alice, welcome to Acme."

    def test_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR_XYZ", "from_env")
        text = "Value: ${TEST_VAR_XYZ}"
        result = resolve_variables(text, {})
        assert result == "Value: from_env"

    def test_unresolved_stays(self):
        text = "Unknown: ${DOES_NOT_EXIST_12345}"
        result = resolve_variables(text, {})
        assert result == "Unknown: ${DOES_NOT_EXIST_12345}"

    def test_no_variables(self):
        text = "Plain text without variables."
        result = resolve_variables(text, {})
        assert result == text

    def test_dict_overrides_env(self, monkeypatch):
        monkeypatch.setenv("MYVAR", "env_value")
        text = "${MYVAR}"
        result = resolve_variables(text, {"MYVAR": "dict_value"})
        assert result == "dict_value"


# ---------------------------------------------------------------------------
# A/B traffic splitting tests
# ---------------------------------------------------------------------------


class TestABTrafficSplit:
    """Test prompt version selection with A/B weights."""

    def test_empty_split_returns_active(self):
        version = select_prompt_version_ab({}, active_version=5)
        assert version == 5

    def test_single_version_always_selected(self):
        for _ in range(20):
            version = select_prompt_version_ab({"v3": 1.0}, active_version=1)
            assert version == 3

    def test_split_returns_valid_versions(self):
        split = {"v1": 0.5, "v2": 0.3, "v3": 0.2}
        seen = set()
        for _ in range(200):
            version = select_prompt_version_ab(split, active_version=1)
            seen.add(version)
        # All versions should appear with enough samples
        assert seen == {1, 2, 3}

    def test_invalid_keys_ignored(self):
        version = select_prompt_version_ab({"bad_key": 1.0}, active_version=7)
        # No valid version keys → should return active_version
        assert version == 7


# ---------------------------------------------------------------------------
# PromptRegistry facade tests
# ---------------------------------------------------------------------------


class TestPromptRegistry:
    """Test the high-level PromptRegistry facade."""

    def test_get_active_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        # Save a prompt
        save_prompt(
            PromptVersion(version=2, system_prompt="Registry prompt v2."),
            app_id="facade-app",
            bucket_name="__local__",
        )

        registry = PromptRegistry(
            config=PromptRegistryConfig(
                app_id="facade-app",
                bucket_name="__local__",
                active_version=2,
            )
        )

        prompt = registry.get_active_prompt()
        assert prompt.version == 2
        assert prompt.system_prompt == "Registry prompt v2."

    def test_get_system_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        save_prompt(
            PromptVersion(
                version=1,
                system_prompt="Hello ${name}!",
                variables={"name": "World"},
            ),
            app_id="sys-app",
            bucket_name="__local__",
        )

        registry = PromptRegistry(
            config=PromptRegistryConfig(app_id="sys-app", bucket_name="__local__", active_version=1)
        )

        sp = registry.get_system_prompt()
        assert sp == "Hello World!"

    def test_caching(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        save_prompt(
            PromptVersion(version=1, system_prompt="Cached"),
            app_id="cache-app",
            bucket_name="__local__",
        )

        registry = PromptRegistry(
            config=PromptRegistryConfig(
                app_id="cache-app", bucket_name="__local__", active_version=1
            )
        )

        # First call loads from disk
        p1 = registry.get_prompt(1)
        # Second call should use cache (same object)
        p2 = registry.get_prompt(1)
        assert p1 is p2

        # After invalidation, a new object is returned
        registry.invalidate_cache()
        p3 = registry.get_prompt(1)
        assert p3 is not p1
        assert p3.system_prompt == "Cached"

    def test_list_versions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        for v in [1, 2, 3]:
            save_prompt(
                PromptVersion(version=v),
                app_id="list-reg-app",
                bucket_name="__local__",
            )

        registry = PromptRegistry(
            config=PromptRegistryConfig(app_id="list-reg-app", bucket_name="__local__")
        )
        assert registry.list_versions() == [1, 2, 3]

    def test_load_all_versions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        for v in [1, 2]:
            save_prompt(
                PromptVersion(version=v, system_prompt=f"v{v}"),
                app_id="all-app",
                bucket_name="__local__",
            )

        registry = PromptRegistry(
            config=PromptRegistryConfig(app_id="all-app", bucket_name="__local__")
        )
        all_prompts = registry.load_all_versions()
        assert len(all_prompts) == 2
        assert all_prompts[0].version == 1
        assert all_prompts[1].version == 2

    def test_get_tool_instructions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llmops_pipeline.io.prompt_registry._LOCAL_DIR", tmp_path)

        save_prompt(
            PromptVersion(
                version=1,
                tool_instructions={"rag_search": "Search ${domain} docs."},
                variables={"domain": "HR"},
            ),
            app_id="tool-app",
            bucket_name="__local__",
        )

        registry = PromptRegistry(
            config=PromptRegistryConfig(
                app_id="tool-app", bucket_name="__local__", active_version=1
            )
        )
        instructions = registry.get_tool_instructions()
        assert instructions["rag_search"] == "Search HR docs."


# ---------------------------------------------------------------------------
# GCS mocked tests
# ---------------------------------------------------------------------------


class TestGCSPromptOperations:
    """Test GCS-backed prompt operations with mocked storage client."""

    def test_save_to_gcs(self):
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        with patch("llmops_pipeline.io.prompt_registry.storage.Client", return_value=mock_client):
            prompt = PromptVersion(version=5, system_prompt="GCS prompt")
            uri = save_prompt(
                prompt,
                app_id="gcs-app",
                bucket_name="my-bucket",
                project="my-project",
            )
            assert uri == "gs://my-bucket/prompts/gcs-app/v5.yaml"
            mock_blob.upload_from_string.assert_called_once()

    def test_load_from_gcs(self):
        import yaml

        prompt_data = PromptVersion(
            version=3,
            system_prompt="From GCS",
        ).model_dump(mode="json")

        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = yaml.dump(prompt_data)

        with patch("llmops_pipeline.io.prompt_registry.storage.Client", return_value=mock_client):
            loaded = load_prompt(
                3,
                app_id="gcs-app",
                bucket_name="my-bucket",
                project="my-project",
            )
            assert loaded.version == 3
            assert loaded.system_prompt == "From GCS"

    def test_load_nonexistent_gcs_returns_default(self):
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob
        mock_blob.exists.return_value = False

        with patch("llmops_pipeline.io.prompt_registry.storage.Client", return_value=mock_client):
            loaded = load_prompt(
                99,
                app_id="gcs-app",
                bucket_name="my-bucket",
            )
            assert loaded.version == 99
            assert "helpful" in loaded.system_prompt.lower()
