"""Pipeline jobs — discriminated union registry.

Every job type is registered here as part of the ``JobKind`` union type.
``settings.MainSettings`` uses ``Field(discriminator="KIND")`` to automatically
dispatch YAML configs to the correct concrete Job class.
"""

from __future__ import annotations

import typing as T

# --- Leaf jobs ------------------------------------------------------------------
from llmops_pipeline.pipelines.feature_engineering.create_vector_db import CreateVectorDBJob
from llmops_pipeline.pipelines.feature_engineering.ingest_documents import IngestDocumentsJob
from llmops_pipeline.pipelines.deployment.register_model import RegisterModelJob
from llmops_pipeline.pipelines.deployment.evaluate_and_deploy import EvaluateAndDeployJob
from llmops_pipeline.pipelines.monitoring.generate_dataset import GenerateDatasetJob
from llmops_pipeline.pipelines.monitoring.post_deploy_eval import PostDeployEvalJob
from llmops_pipeline.pipelines.monitoring.diagnose import DiagnoseJob
from llmops_pipeline.pipelines.monitoring.remediate import RemediateJob
from llmops_pipeline.pipelines.fine_tuning.prepare_dataset import PrepareDatasetJob
from llmops_pipeline.pipelines.fine_tuning.train import TrainJob
from llmops_pipeline.pipelines.fine_tuning.evaluate import EvaluateFineTunedJob

# --- Manager / orchestrator jobs ------------------------------------------------
from llmops_pipeline.pipelines.managers.feature_engineering_manager import FeatureEngineeringJob
from llmops_pipeline.pipelines.managers.deployment_manager import DeploymentJob
from llmops_pipeline.pipelines.managers.monitoring_manager import MonitoringJob
from llmops_pipeline.pipelines.managers.fine_tuning_manager import FineTuningJob

# --- Discriminated union --------------------------------------------------------

JobKind = T.Annotated[
    T.Union[
        # Manager jobs (top-level YAML entry points)
        FeatureEngineeringJob,
        DeploymentJob,
        MonitoringJob,
        FineTuningJob,
        # Leaf jobs (can also be invoked directly)
        CreateVectorDBJob,
        IngestDocumentsJob,
        RegisterModelJob,
        EvaluateAndDeployJob,
        GenerateDatasetJob,
        PostDeployEvalJob,
        DiagnoseJob,
        RemediateJob,
        PrepareDatasetJob,
        TrainJob,
        EvaluateFineTunedJob,
    ],
    T.Annotated[str, "discriminator"],
]

__all__ = [
    "JobKind",
    # Managers
    "FeatureEngineeringJob",
    "DeploymentJob",
    "MonitoringJob",
    "FineTuningJob",
    # Leaf jobs
    "CreateVectorDBJob",
    "IngestDocumentsJob",
    "RegisterModelJob",
    "EvaluateAndDeployJob",
    "GenerateDatasetJob",
    "PostDeployEvalJob",
    "DiagnoseJob",
    "RemediateJob",
    "PrepareDatasetJob",
    "TrainJob",
    "EvaluateFineTunedJob",
]
