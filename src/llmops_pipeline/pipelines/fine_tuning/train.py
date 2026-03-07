"""Fine-tuning — Train (submit supervised tuning job to Vertex AI)."""

from __future__ import annotations

import typing as T

from llmops_pipeline.pipelines.base import Job, Locals


class TrainJob(Job, frozen=True):
    """Submit a Vertex AI supervised fine-tuning job.

    Uses Vertex AI's GenerativeModel.tune() or the tuning pipeline API to
    create a fine-tuned adapter/model.

    Config fields:
        project, location — GCP identifiers.
        base_model — model ID to fine-tune (e.g. gemini-2.0-flash).
        train_dataset_uri — GCS URI to the training JSONL.
        display_name — display name for the tuned model.
        epochs — number of training epochs.
        learning_rate_multiplier — LR scaling factor.
        adapter_size — LoRA adapter size.
    """

    KIND: T.Literal["TrainJob"] = "TrainJob"

    project: str = ""
    location: str = "us-central1"
    gcs_bucket: str = ""

    base_model: str = "gemini-2.0-flash"
    train_dataset_uri: str = ""
    display_name: str = "llmops-fine-tuned"
    epochs: int = 3
    learning_rate_multiplier: float = 1.0
    adapter_size: int = 4

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Fine-Tuning Training START ===")
        logger.info(
            "Base model: {}, Dataset: {}, Epochs: {}",
            self.base_model,
            self.train_dataset_uri,
            self.epochs,
        )

        if not self.train_dataset_uri:
            logger.warning("No training dataset URI provided — skipping training")
            return {"status": "no_dataset", "tuned_model_name": ""}

        try:
            import vertexai
            from vertexai.tuning import sft

            vertexai.init(project=self.project, location=self.location)

            # Submit supervised fine-tuning job
            tuning_job = sft.train(
                source_model=self.base_model,
                train_dataset=self.train_dataset_uri,
                epochs=self.epochs,
                adapter_size=self.adapter_size,
                learning_rate_multiplier=self.learning_rate_multiplier,
                tuned_model_display_name=self.display_name,
            )

            logger.info("Tuning job submitted: {}", tuning_job.resource_name)

            # The tuning job is async — log the resource name for tracking
            tuned_model_name = ""
            if hasattr(tuning_job, "tuned_model_endpoint_name"):
                tuned_model_name = tuning_job.tuned_model_endpoint_name
            elif hasattr(tuning_job, "experiment"):
                tuned_model_name = tuning_job.resource_name

            logger.info("=== Fine-Tuning Training SUBMITTED ===")
            return {
                "status": "submitted",
                "tuning_job_name": tuning_job.resource_name,
                "tuned_model_name": tuned_model_name,
                "base_model": self.base_model,
            }

        except Exception as exc:
            logger.warning("Fine-tuning submission failed: {}", exc)
            return {"status": f"failed: {exc}", "tuned_model_name": ""}
