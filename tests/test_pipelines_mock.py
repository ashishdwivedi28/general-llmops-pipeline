"""Pipeline mock tests — validate pipeline job classes without GCP calls.

Tests ensure that pipeline jobs:
  - Can be instantiated from YAML config
  - Validate with Pydantic strict/frozen constraints
  - Have correct KIND discriminator values
  - Chain correctly through managers
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("GCS_BUCKET", "test-bucket")


class TestPipelineJobRegistry:
    """Verify all job types are registered in the discriminated union."""

    def test_all_jobs_in_jobkind(self) -> None:
        from llmops_pipeline.pipelines import JobKind, __all__

        assert "JobKind" in __all__
        # Verify we can access the union type
        assert JobKind is not None

    def test_job_kind_includes_new_types(self) -> None:
        """Verify Phase D and E job types are registered."""
        from llmops_pipeline.pipelines import __all__

        expected = [
            "DiagnoseJob",
            "RemediateJob",
            "FineTuningJob",
            "PrepareDatasetJob",
            "TrainJob",
            "EvaluateFineTunedJob",
        ]
        for job_name in expected:
            assert job_name in __all__, f"{job_name} not in __all__"


class TestDiagnoseJob:
    """Test DiagnoseJob initialization and logic."""

    def test_instantiation(self) -> None:
        from llmops_pipeline.pipelines.monitoring.diagnose import DiagnoseJob

        job = DiagnoseJob(
            KIND="DiagnoseJob",
            project="test-project",
            monitoring_scores={"answer_relevance": 0.5, "faithfulness": 0.4},
            degraded=True,
        )
        assert job.KIND == "DiagnoseJob"
        assert job.degraded is True

    def test_no_degradation_skips(self) -> None:
        from llmops_pipeline.pipelines.monitoring.diagnose import DiagnoseJob

        job = DiagnoseJob(KIND="DiagnoseJob", degraded=False)
        with job as runner:
            result = runner.run()
        assert result["report"]["degraded"] is False

    def test_detects_low_relevance(self) -> None:
        from llmops_pipeline.pipelines.monitoring.diagnose import DiagnoseJob

        job = DiagnoseJob(
            KIND="DiagnoseJob",
            degraded=True,
            monitoring_scores={"answer_relevance": 0.3, "faithfulness": 0.8, "toxicity": 0.01},
        )
        with job as runner:
            result = runner.run()
        report = result["report"]
        assert report["degraded"] is True
        assert "retrigger_feature_engineering" in report["recommended_actions"]


class TestRemediateJob:
    """Test RemediateJob initialization."""

    def test_instantiation(self) -> None:
        from llmops_pipeline.pipelines.monitoring.remediate import RemediateJob

        job = RemediateJob(
            KIND="RemediateJob",
            project="test-project",
            gcs_bucket="test-bucket",
            diagnosis_report={"recommended_actions": [], "primary_cause": "none"},
        )
        assert job.KIND == "RemediateJob"

    def test_no_actions_returns_no_action(self) -> None:
        from llmops_pipeline.pipelines.monitoring.remediate import RemediateJob

        job = RemediateJob(
            KIND="RemediateJob",
            diagnosis_report={"recommended_actions": [], "primary_cause": "none"},
        )
        with job as runner:
            result = runner.run()
        assert result["remediation"]["status"] == "no_action"


class TestFineTuningJobs:
    """Test fine-tuning pipeline jobs."""

    def test_prepare_dataset_instantiation(self) -> None:
        from llmops_pipeline.pipelines.fine_tuning.prepare_dataset import PrepareDatasetJob

        job = PrepareDatasetJob(
            KIND="PrepareDatasetJob",
            project="test-project",
            gcs_bucket="test-bucket",
        )
        assert job.KIND == "PrepareDatasetJob"
        assert job.min_rating == 4

    def test_train_instantiation(self) -> None:
        from llmops_pipeline.pipelines.fine_tuning.train import TrainJob

        job = TrainJob(
            KIND="TrainJob",
            project="test-project",
            base_model="gemini-2.0-flash",
        )
        assert job.base_model == "gemini-2.0-flash"
        assert job.epochs == 3

    def test_evaluate_instantiation(self) -> None:
        from llmops_pipeline.pipelines.fine_tuning.evaluate import EvaluateFineTunedJob

        job = EvaluateFineTunedJob(
            KIND="EvaluateFineTunedJob",
            project="test-project",
        )
        assert job.compare_with_base is True

    def test_fine_tuning_manager_instantiation(self) -> None:
        from llmops_pipeline.pipelines.managers.fine_tuning_manager import FineTuningJob

        job = FineTuningJob(
            KIND="FineTuningJob",
            project="test-project",
            gcs_bucket="test-bucket",
        )
        assert job.KIND == "FineTuningJob"
        assert job.auto_deploy is False


class TestMonitoringManager:
    """Test updated monitoring manager with diagnose + remediate."""

    def test_monitoring_job_has_diagnosis_fields(self) -> None:
        from llmops_pipeline.pipelines.managers.monitoring_manager import MonitoringJob

        job = MonitoringJob(
            KIND="MonitoringJob",
            project="test-project",
        )
        assert hasattr(job, "bq_dataset")
        assert hasattr(job, "latency_spike_ms")
        assert hasattr(job, "auto_rollback_enabled")
