"""Tests for the Pipeline Artifact Manifest — the online/offline bridge.

These tests validate local-file-based manifest operations (no GCS required).
GCS operations are tested via mock.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llmops_pipeline.io.manifest import (
    DeploymentManifest,
    FeatureEngineeringManifest,
    ManifestWatcher,
    MonitoringManifest,
    PipelineManifest,
    read_manifest,
    update_section,
    write_manifest,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_local_manifests(tmp_path: Path, monkeypatch):
    """Redirect local manifest storage to a temp dir and clean up after."""
    import llmops_pipeline.io.manifest as mod

    monkeypatch.setattr(mod, "_LOCAL_DIR", tmp_path / ".manifests")
    yield


@pytest.fixture
def sample_manifest() -> PipelineManifest:
    return PipelineManifest(
        app_id="test-app",
        version="1",
        created_at="2026-03-06T00:00:00",
        feature_engineering=FeatureEngineeringManifest(
            vector_index_resource_name="projects/p/locations/l/indexes/123",
            vector_endpoint_resource_name="projects/p/locations/l/indexEndpoints/456",
            deployed_index_id="deployed_index",
            embedding_model="text-embedding-004",
            embedding_dimensions=768,
            num_documents=10,
            num_chunks=50,
        ),
        deployment=DeploymentManifest(
            model_resource_name="projects/p/locations/l/models/m1",
            active_model="gemini-2.0-flash",
            active_prompt_version="v2",
            eval_scores={"answer_relevance": 0.85, "faithfulness": 0.90},
            quality_gate_passed=True,
        ),
    )


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestManifestModels:
    """Test Pydantic model validation for all manifest sections."""

    def test_default_pipeline_manifest(self):
        m = PipelineManifest()
        assert m.app_id == "llmops-app"
        assert m.version == ""
        assert m.feature_engineering.vector_index_resource_name == ""
        assert m.deployment.active_model == "gemini-2.0-flash"
        assert m.monitoring.status == "healthy"

    def test_feature_engineering_manifest(self):
        fe = FeatureEngineeringManifest(
            vector_index_resource_name="idx",
            vector_endpoint_resource_name="ep",
            num_documents=5,
            num_chunks=20,
        )
        assert fe.num_documents == 5
        assert fe.embedding_model == "text-embedding-004"  # default

    def test_deployment_manifest(self):
        d = DeploymentManifest(
            active_model="gemini-2.5-pro",
            eval_scores={"relevance": 0.92},
            quality_gate_passed=True,
        )
        assert d.active_model == "gemini-2.5-pro"
        assert d.eval_scores["relevance"] == 0.92

    def test_monitoring_manifest(self):
        m = MonitoringManifest(degraded=True, status="degraded")
        assert m.degraded is True

    def test_strict_rejects_extra_fields(self):
        with pytest.raises(Exception):
            FeatureEngineeringManifest(unknown_field="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Local file I/O
# ---------------------------------------------------------------------------


class TestLocalManifestIO:
    """Test write/read cycle using local file fallback."""

    def test_write_and_read_local(self, sample_manifest: PipelineManifest):
        """Write a manifest locally, read it back, and verify round-trip."""
        path = write_manifest(sample_manifest, bucket_name="__local__")
        assert Path(path).exists()

        loaded = read_manifest("test-app", bucket_name="__local__")
        assert loaded.app_id == "test-app"
        assert loaded.feature_engineering.num_documents == 10
        assert loaded.deployment.active_model == "gemini-2.0-flash"

    def test_read_missing_manifest_returns_default(self):
        """Reading a non-existent manifest returns a default."""
        loaded = read_manifest("nonexistent-app", bucket_name="__local__")
        assert loaded.app_id == "nonexistent-app"
        assert loaded.version == "0"

    def test_write_empty_bucket_falls_back_to_local(self, sample_manifest: PipelineManifest):
        """Empty bucket_name triggers local fallback."""
        path = write_manifest(sample_manifest, bucket_name="")
        assert Path(path).exists()


# ---------------------------------------------------------------------------
# Section updates
# ---------------------------------------------------------------------------


class TestUpdateSection:
    """Test the read-modify-write section update pattern."""

    def test_update_feature_engineering_section(self):
        """Update FE section and verify version bump."""
        m1 = update_section(
            "section-test-app",
            "feature_engineering",
            {
                "vector_index_resource_name": "idx-1",
                "vector_endpoint_resource_name": "ep-1",
                "num_documents": 42,
            },
            bucket_name="__local__",
        )
        assert m1.version == "1"
        assert m1.feature_engineering.vector_index_resource_name == "idx-1"
        assert m1.feature_engineering.num_documents == 42
        assert m1.feature_engineering.last_run != ""

    def test_update_deployment_section(self):
        m = update_section(
            "deploy-test",
            "deployment",
            {
                "active_model": "gemini-2.5-pro",
                "eval_scores": {"relevance": 0.95},
                "quality_gate_passed": True,
            },
            bucket_name="__local__",
        )
        assert m.deployment.active_model == "gemini-2.5-pro"
        assert m.deployment.eval_scores["relevance"] == 0.95

    def test_update_monitoring_section(self):
        m = update_section(
            "mon-test",
            "monitoring",
            {"degraded": True, "status": "degraded", "num_traces_evaluated": 100},
            bucket_name="__local__",
        )
        assert m.monitoring.degraded is True
        assert m.monitoring.num_traces_evaluated == 100

    def test_sequential_updates_increment_version(self):
        for i in range(3):
            m = update_section(
                "ver-test",
                "feature_engineering",
                {"num_documents": i},
                bucket_name="__local__",
            )
        assert m.version == "3"
        assert m.feature_engineering.num_documents == 2  # last value

    def test_invalid_section_raises(self):
        with pytest.raises(ValueError, match="Invalid section"):
            update_section(
                "bad-section",
                "invalid_section",
                {},
                bucket_name="__local__",
            )

    def test_update_preserves_other_sections(self):
        """Updating one section does not clobber other sections."""
        update_section(
            "preserve-test",
            "feature_engineering",
            {"num_documents": 100},
            bucket_name="__local__",
        )
        update_section(
            "preserve-test",
            "deployment",
            {"active_model": "gpt-4o"},
            bucket_name="__local__",
        )
        m = read_manifest("preserve-test", bucket_name="__local__")
        assert m.feature_engineering.num_documents == 100
        assert m.deployment.active_model == "gpt-4o"


# ---------------------------------------------------------------------------
# GCS mock tests
# ---------------------------------------------------------------------------


class TestGCSManifest:
    """Test GCS-based operations using mocked storage client."""

    @patch("llmops_pipeline.io.manifest.storage")
    def test_write_manifest_gcs(self, mock_storage, sample_manifest: PipelineManifest):
        mock_client = MagicMock()
        mock_storage.Client.return_value = mock_client
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        uri = write_manifest(sample_manifest, bucket_name="my-bucket", project="proj")
        assert uri == "gs://my-bucket/manifests/test-app/latest.json"
        mock_blob.upload_from_string.assert_called_once()

    @patch("llmops_pipeline.io.manifest.storage")
    def test_read_manifest_gcs(self, mock_storage, sample_manifest: PipelineManifest):
        mock_client = MagicMock()
        mock_storage.Client.return_value = mock_client
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = sample_manifest.model_dump_json()

        loaded = read_manifest("test-app", bucket_name="my-bucket", project="proj")
        assert loaded.app_id == "test-app"
        assert loaded.feature_engineering.num_documents == 10

    @patch("llmops_pipeline.io.manifest.storage")
    def test_read_missing_gcs_manifest(self, mock_storage):
        mock_client = MagicMock()
        mock_storage.Client.return_value = mock_client
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_blob.exists.return_value = False

        loaded = read_manifest("new-app", bucket_name="my-bucket")
        assert loaded.app_id == "new-app"
        assert loaded.version == "0"

    @patch("llmops_pipeline.io.manifest.storage")
    def test_write_gcs_failure_falls_back_to_local(self, mock_storage, sample_manifest):
        mock_storage.Client.side_effect = Exception("GCS unavailable")

        path = write_manifest(sample_manifest, bucket_name="my-bucket")
        # Should fall back to local file
        assert Path(path).exists()


# ---------------------------------------------------------------------------
# ManifestWatcher
# ---------------------------------------------------------------------------


class TestManifestWatcher:
    """Test the ManifestWatcher used by the serving layer."""

    def test_current_returns_default_on_first_access(self):
        watcher = ManifestWatcher("watcher-test", bucket_name="__local__")
        m = watcher.current
        assert m.app_id == "watcher-test"

    def test_refresh_updates_current(self):
        # Pre-write a manifest
        update_section(
            "watcher-refresh",
            "deployment",
            {"active_model": "claude-4"},
            bucket_name="__local__",
        )

        watcher = ManifestWatcher("watcher-refresh", bucket_name="__local__")
        m = watcher.refresh()
        assert m.deployment.active_model == "claude-4"
        assert watcher.current.deployment.active_model == "claude-4"

    def test_stop_sets_running_false(self):
        watcher = ManifestWatcher("stop-test", bucket_name="__local__")
        watcher._running = True
        watcher.stop()
        assert watcher._running is False
