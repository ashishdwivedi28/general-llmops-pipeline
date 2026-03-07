"""Evaluation tests — test the evaluation-related utilities.

Tests task detection, model routing integration, and prompt registry logic.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("GCP_PROJECT_ID", "test-project")


class TestTaskDetection:
    """Test the task detection module."""

    @pytest.fixture
    def app_config_path(self, tmp_path: Path) -> Path:
        """Create a temporary app config YAML."""
        config = {
            "app_id": "test-app",
            "task_detection": {
                "enabled": True,
                "method": "keyword",
                "default_task": "general_qa",
                "tasks": {
                    "general_qa": {
                        "description": "General questions",
                        "keywords": ["policy", "leave", "benefits"],
                        "tools": ["search_docs"],
                        "prompt_template": "system_prompt",
                        "model_tier": "primary",
                    },
                    "onboarding": {
                        "description": "Onboarding queries",
                        "keywords": ["onboarding", "new hire", "first day"],
                        "tools": ["search_docs", "search_onboarding"],
                        "prompt_template": "onboarding_prompt",
                        "model_tier": "primary",
                    },
                    "chitchat": {
                        "description": "Small talk",
                        "keywords": [],
                        "tools": [],
                        "prompt_template": "chitchat_prompt",
                        "model_tier": "fallback",
                    },
                },
            },
        }
        path = tmp_path / "test_app.yaml"
        path.write_text(yaml.dump(config))
        return path

    def test_keyword_detection_general_qa(self, app_config_path: Path) -> None:
        from serving.task_detection import TaskDetector

        detector = TaskDetector(app_config_path, detection_method="keyword")
        result = detector.detect("What is the leave policy?")
        assert result["task_id"] == "general_qa"
        assert result["method"] == "keyword"
        assert "search_docs" in result["tools"]

    def test_keyword_detection_onboarding(self, app_config_path: Path) -> None:
        from serving.task_detection import TaskDetector

        detector = TaskDetector(app_config_path, detection_method="keyword")
        result = detector.detect("Tell me about the onboarding process")
        assert result["task_id"] == "onboarding"
        assert "search_onboarding" in result["tools"]

    def test_default_task_when_no_match(self, app_config_path: Path) -> None:
        from serving.task_detection import TaskDetector

        detector = TaskDetector(app_config_path, detection_method="keyword")
        result = detector.detect("What's the weather today?")
        assert result["task_id"] == "general_qa"
        assert result["method"] == "default"

    def test_app_id_property(self, app_config_path: Path) -> None:
        from serving.task_detection import TaskDetector

        detector = TaskDetector(app_config_path)
        assert detector.app_id == "test-app"

    def test_tasks_property(self, app_config_path: Path) -> None:
        from serving.task_detection import TaskDetector

        detector = TaskDetector(app_config_path)
        assert "general_qa" in detector.tasks
        assert "onboarding" in detector.tasks
        assert "chitchat" in detector.tasks

    def test_missing_config_file(self, tmp_path: Path) -> None:
        from serving.task_detection import TaskDetector

        detector = TaskDetector(tmp_path / "nonexistent.yaml")
        result = detector.detect("anything")
        assert result["task_id"] == "general_qa"
        assert result["method"] == "default"

    def test_llm_classifier_fallback(self, app_config_path: Path) -> None:
        from serving.task_detection import TaskDetector

        mock_classifier = lambda q: "onboarding"
        detector = TaskDetector(
            app_config_path,
            detection_method="llm",
            llm_classifier=mock_classifier,
        )
        result = detector.detect("Tell me about starting here")
        assert result["task_id"] == "onboarding"
        assert result["method"] == "llm"

    def test_keyword_and_llm_prefers_keyword(self, app_config_path: Path) -> None:
        from serving.task_detection import TaskDetector

        mock_classifier = lambda q: "chitchat"
        detector = TaskDetector(
            app_config_path,
            detection_method="keyword_and_llm",
            llm_classifier=mock_classifier,
        )
        result = detector.detect("What are the benefits?")
        assert result["task_id"] == "general_qa"
        assert result["method"] == "keyword"


class TestCanaryDeployment:
    """Test canary deployment utilities."""

    def test_smoke_test_result_structure(self) -> None:
        from serving.canary import SmokeTestResult

        result: SmokeTestResult = {
            "passed": True,
            "checks": [{"endpoint": "/health", "passed": True}],
            "duration_ms": 100.0,
        }
        assert result["passed"] is True

    def test_canary_manager_instantiation(self) -> None:
        from serving.canary import CanaryManager

        mgr = CanaryManager(
            project="test",
            region="us-central1",
            service_name="test-svc",
            canary_steps=[10, 50, 100],
        )
        assert mgr._steps == [10, 50, 100]
