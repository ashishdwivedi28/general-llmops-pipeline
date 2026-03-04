"""Tests for configuration parsing and settings."""

from __future__ import annotations

import tempfile
import os

import pytest

# These tests need google-cloud-* packages to import pipeline modules
gcp = pytest.importorskip("google.cloud.aiplatform", reason="google-cloud-aiplatform not installed")


def test_parse_yaml_config():
    """Test OmegaConf YAML parsing."""
    from llmops_pipeline.io.configs import parse_file, to_object

    # Write a temp YAML file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("job:\n  KIND: FeatureEngineeringJob\n  project: test\n")
        tmp_path = f.name

    try:
        cfg = parse_file(tmp_path)
        obj = to_object(cfg)
        assert obj["job"]["KIND"] == "FeatureEngineeringJob"
        assert obj["job"]["project"] == "test"
    finally:
        os.unlink(tmp_path)


def test_merge_configs():
    """Test merging multiple configs."""
    from llmops_pipeline.io.configs import parse_string, merge_configs, to_object

    cfg1 = parse_string("job:\n  KIND: FeatureEngineeringJob\n  project: base")
    cfg2 = parse_string("job:\n  project: override\n  location: us-central1")

    merged = merge_configs([cfg1, cfg2])
    obj = to_object(merged)
    assert obj["job"]["project"] == "override"
    assert obj["job"]["KIND"] == "FeatureEngineeringJob"
    assert obj["job"]["location"] == "us-central1"


def test_main_settings_discriminated_union(sample_config):
    """Test that MainSettings correctly dispatches to FeatureEngineeringJob."""
    from llmops_pipeline.settings import MainSettings
    from llmops_pipeline.pipelines.managers.feature_engineering_manager import FeatureEngineeringJob

    settings = MainSettings(**sample_config)
    assert isinstance(settings.job, FeatureEngineeringJob)
    assert settings.job.KIND == "FeatureEngineeringJob"
    assert settings.job.project == "test-project"


def test_deployment_settings(sample_deployment_config):
    """Test deployment config dispatching."""
    from llmops_pipeline.settings import MainSettings
    from llmops_pipeline.pipelines.managers.deployment_manager import DeploymentJob

    settings = MainSettings(**sample_deployment_config)
    assert isinstance(settings.job, DeploymentJob)
    assert settings.job.metric_thresholds["answer_relevance"] == 0.70


def test_monitoring_settings(sample_monitoring_config):
    """Test monitoring config dispatching."""
    from llmops_pipeline.settings import MainSettings
    from llmops_pipeline.pipelines.managers.monitoring_manager import MonitoringJob

    settings = MainSettings(**sample_monitoring_config)
    assert isinstance(settings.job, MonitoringJob)
    assert settings.job.monitoring_window_days == 7
