"""Manager — Fine-tuning orchestrator.

Chains: PrepareDatasetJob → TrainJob → EvaluateFineTunedJob → manifest.
"""

from __future__ import annotations

import json
import typing as T
from datetime import datetime, timezone

from llmops_pipeline.pipelines.base import Job, Locals
from llmops_pipeline.pipelines.fine_tuning.evaluate import EvaluateFineTunedJob
from llmops_pipeline.pipelines.fine_tuning.prepare_dataset import PrepareDatasetJob
from llmops_pipeline.pipelines.fine_tuning.train import TrainJob


class FineTuningJob(Job, frozen=True):
    """Orchestrates the fine-tuning pipeline.

    1. Prepares a high-quality dataset from production feedback.
    2. Submits a Vertex AI supervised fine-tuning job.
    3. Evaluates the fine-tuned model against the base.
    4. Writes results to the artifact manifest.

    Config: ``fine_tuning.yaml``
    """

    KIND: T.Literal["FineTuningJob"] = "FineTuningJob"

    # Shared
    project: str = ""
    location: str = "us-central1"
    gcs_bucket: str = ""
    app_id: str = "llmops-app"

    # Dataset config
    bq_dataset: str = "llmops"
    bq_interactions_table: str = "interactions"
    bq_feedback_table: str = "feedback"
    min_rating: int = 4
    min_samples: int = 100
    max_samples: int = 10000
    test_split_ratio: float = 0.2
    output_gcs_path: str = "fine_tuning/datasets/"

    # Training config
    base_model: str = "gemini-2.0-flash"
    display_name: str = "llmops-fine-tuned"
    epochs: int = 3
    learning_rate_multiplier: float = 1.0
    adapter_size: int = 4

    # Evaluation config
    quality_gate: dict[str, float] = {
        "answer_relevance": 0.75,
        "faithfulness": 0.70,
    }
    compare_with_base: bool = True

    # Deployment config
    auto_deploy: bool = False

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Fine-Tuning Pipeline START ===")

        # ---- Step 1: Prepare Dataset ----
        prep_job = PrepareDatasetJob(
            KIND="PrepareDatasetJob",
            logger_service=self.logger_service,
            vertex_ai_service=self.vertex_ai_service,
            project=self.project,
            location=self.location,
            gcs_bucket=self.gcs_bucket,
            bq_dataset=self.bq_dataset,
            bq_interactions_table=self.bq_interactions_table,
            bq_feedback_table=self.bq_feedback_table,
            min_rating=self.min_rating,
            min_samples=self.min_samples,
            max_samples=self.max_samples,
            test_split_ratio=self.test_split_ratio,
            output_gcs_path=self.output_gcs_path,
        )
        with prep_job as runner:
            prep_result = runner.run()

        if prep_result.get("status") != "ready":
            logger.warning("Dataset preparation failed: {}", prep_result.get("status"))
            self._write_manifest(prep_result, "dataset_insufficient")
            return {**prep_result, "pipeline_status": "aborted_no_data"}

        # ---- Step 2: Train ----
        train_job = TrainJob(
            KIND="TrainJob",
            logger_service=self.logger_service,
            vertex_ai_service=self.vertex_ai_service,
            project=self.project,
            location=self.location,
            gcs_bucket=self.gcs_bucket,
            base_model=self.base_model,
            train_dataset_uri=prep_result["train_gcs_uri"],
            display_name=self.display_name,
            epochs=self.epochs,
            learning_rate_multiplier=self.learning_rate_multiplier,
            adapter_size=self.adapter_size,
        )
        with train_job as runner:
            train_result = runner.run()

        if train_result.get("status") != "submitted":
            logger.warning("Training failed: {}", train_result.get("status"))
            self._write_manifest(train_result, "training_failed")
            return {**train_result, "pipeline_status": "training_failed"}

        # ---- Step 3: Evaluate ----
        eval_job = EvaluateFineTunedJob(
            KIND="EvaluateFineTunedJob",
            logger_service=self.logger_service,
            vertex_ai_service=self.vertex_ai_service,
            project=self.project,
            location=self.location,
            gcs_bucket=self.gcs_bucket,
            tuned_model_name=train_result.get("tuned_model_name", ""),
            base_model=self.base_model,
            test_dataset_uri=prep_result["test_gcs_uri"],
            quality_gate=self.quality_gate,
            compare_with_base=self.compare_with_base,
        )
        with eval_job as runner:
            eval_result = runner.run()

        # ---- Write manifest ----
        status = "passed" if eval_result.get("passed") else "blocked"
        self._write_manifest(
            {
                "dataset": prep_result,
                "training": train_result,
                "evaluation": eval_result,
            },
            status,
        )

        logger.info(
            "=== Fine-Tuning Pipeline COMPLETE — quality_gate={} ===",
            status,
        )
        return {
            "pipeline_status": status,
            "dataset": prep_result,
            "training": train_result,
            "evaluation": eval_result,
        }

    def _write_manifest(self, data: dict, status: str) -> None:
        """Write fine-tuning section to the artifact manifest."""
        logger = self.logger_service.logger()
        try:
            from llmops_pipeline.io.manifest import update_section

            update_section(
                app_id=self.app_id,
                section="monitoring",
                data={
                    "last_diagnosis": f"fine_tuning_{status}",
                    "remediation_action": json.dumps(data, default=str)[:500],
                },
                bucket_name=self.gcs_bucket,
                project=self.project,
            )
            logger.info("Manifest fine_tuning section updated")
        except Exception as exc:
            logger.warning("Failed to update manifest: {} (non-fatal)", exc)
