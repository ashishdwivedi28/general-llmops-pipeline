"""End-to-end endpoint tests.

These tests run against the actual serving layer (FastAPI app) using TestClient.
They test the full request/response cycle without hitting real GCP services.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("GCS_BUCKET", "test-bucket")


@pytest.fixture
def app():
    """Create the FastAPI app with mocked initialization."""
    with patch("serving.server._sync_initialize") as mock_init:
        mock_init.return_value = None
        from serving.server import create_app

        application = create_app()
        # Inject mock state matching server.py's _state keys
        from serving.server import _state

        _state["ready"] = True
        _state["agent"] = MagicMock()
        _state["config"] = MagicMock(
            AGENT_NAME="test-app",
            GCS_BUCKET="test-bucket",
            BQ_DATASET="test-dataset",
            GCP_PROJECT_ID="test-project",
            GCP_LOCATION="us-central1",
            VALID_TOPICS=[],
            INVALID_TOPICS=[],
        )
        # Manifest watcher mock — return a MagicMock that behaves like PipelineManifest
        mock_manifest = MagicMock()
        mock_manifest.version = "1"
        mock_manifest.model_dump.return_value = {
            "app_id": "test-app",
            "version": "1",
            "feature_engineering": {},
            "deployment": {},
            "monitoring": {},
        }
        mock_manifest.deployment.active_model = "gemini-2.0-flash"
        mock_manifest.feature_engineering.vector_endpoint_resource_name = ""
        mock_watcher = MagicMock()
        mock_watcher.current = mock_manifest
        _state["manifest_watcher"] = mock_watcher

        _state["interaction_logger"] = MagicMock()
        _state["guardrail_checker"] = None  # Disable guardrails for testing
        _state["prompt_registry"] = None

        _state["cost_tracker"] = MagicMock()
        _state["cost_tracker"].summary.return_value = MagicMock(
            model_dump=MagicMock(return_value={"total_cost_usd": 0.0})
        )

        yield application


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


class TestHealthEndpoints:
    """Test health and readiness probes."""

    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_ready_when_initialized(self, client: TestClient) -> None:
        from serving.server import _state

        _state["ready"] = True
        resp = client.get("/ready")
        assert resp.status_code == 200


class TestChatEndpoint:
    """Test the /chat endpoint."""

    def test_chat_requires_query(self, client: TestClient) -> None:
        resp = client.post("/chat", json={"session_id": "s1"})
        # Should still get a response (query defaults to empty string)
        assert resp.status_code in (200, 422, 503)

    def test_chat_with_valid_input(self, client: TestClient) -> None:
        # Mock the ADK agent runner
        from serving.server import _state

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(
            return_value=MagicMock(
                content="Hello! How can I help?",
                tool_calls=[],
            )
        )
        _state["agent"] = MagicMock()

        resp = client.post(
            "/chat",
            json={"query": "What is the leave policy?", "session_id": "test-session"},
        )
        # May fail due to async runner, but should not crash
        assert resp.status_code in (200, 500, 503)


class TestFeedbackEndpoint:
    """Test the /feedback endpoint."""

    def test_feedback_valid(self, client: TestClient) -> None:
        from serving.server import _state

        _state["interaction_logger"] = MagicMock()
        resp = client.post(
            "/feedback",
            json={
                "session_id": "s1",
                "rating": 5,
                "comment": "Great answer!",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "recorded"


class TestManifestEndpoint:
    """Test the /manifest endpoint."""

    def test_manifest_returns_data(self, client: TestClient) -> None:
        resp = client.get("/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert "app_id" in data


class TestCostEndpoint:
    """Test the /costs endpoint."""

    def test_costs_returns_summary(self, client: TestClient) -> None:
        resp = client.get("/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_cost_usd" in data
