"""Test fixtures for LLMOps Pipeline tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_config() -> dict:
    """Sample configuration for testing."""
    return {
        "job": {
            "KIND": "FeatureEngineeringJob",
            "project": "test-project",
            "location": "us-central1",
            "gcs_bucket": "test-bucket",
            "app_id": "test-app",
            "embedding_model": "text-embedding-004",
            "embedding_dimensions": 768,
            "documents_path": "data/documents/",
            "chunk_size": 1000,
            "chunk_overlap": 200,
        }
    }


@pytest.fixture
def sample_deployment_config() -> dict:
    """Sample deployment configuration."""
    return {
        "job": {
            "KIND": "DeploymentJob",
            "project": "test-project",
            "location": "us-central1",
            "gcs_bucket": "test-bucket",
            "app_id": "test-app",
            "model_display_name": "test-model",
            "active_model": "gemini-2.0-flash",
            "active_prompt_version": "v1",
            "eval_dataset_csv": "data/datasets/rag_eval.csv",
            "metric_thresholds": {
                "answer_relevance": 0.70,
                "faithfulness": 0.65,
                "toxicity": 0.10,
            },
        }
    }


@pytest.fixture
def sample_monitoring_config() -> dict:
    """Sample monitoring configuration."""
    return {
        "job": {
            "KIND": "MonitoringJob",
            "project": "test-project",
            "location": "us-central1",
            "gcs_bucket": "test-bucket",
            "app_id": "test-app",
            "monitoring_window_days": 7,
            "metric_thresholds": {
                "answer_relevance": 0.70,
                "faithfulness": 0.65,
                "toxicity": 0.10,
            },
        }
    }
