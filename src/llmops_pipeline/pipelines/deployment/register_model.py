"""Deployment — Register model config in Vertex AI Model Registry."""

from __future__ import annotations

import json
import typing as T

from google.cloud import aiplatform

from llmops_pipeline.pipelines.base import Job, Locals


class RegisterModelJob(Job, frozen=True):
    """Register model config as a Vertex AI Model Registry entry.

    - Uploads model config artifact to GCS
    - Registers in Vertex AI Model Registry with label: staging_label (default: champion)
    - Validates model by running test predictions
    - If validation passes → ready for evaluation

    Config fields:
        model_display_name: name in Vertex AI Model Registry.
        staging_label: label for newly registered models.
        model_config_path: path to rag_chain_config.yaml.
        gcs_bucket: GCS bucket for model artifacts.
        project: GCP project ID.
        location: GCP region.
    """

    KIND: T.Literal["RegisterModelJob"] = "RegisterModelJob"

    model_display_name: str = "llmops-rag-chatbot"
    staging_label: str = "champion"
    model_config_path: str = "confs/rag_chain_config.yaml"
    gcs_bucket: str = ""
    serving_image: str = "us-docker.pkg.dev/vertex-ai/prediction/tf2-cpu.2-12:latest"
    project: str = ""
    location: str = "us-central1"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("Registering model: {}", self.model_display_name)

        # Upload model config to GCS
        from google.cloud import storage as gcs

        client = gcs.Client(project=self.project)
        bucket = client.bucket(self.gcs_bucket)
        blob = bucket.blob(f"model_artifacts/{self.model_display_name}/config.yaml")
        blob.upload_from_filename(self.model_config_path)
        artifact_uri = f"gs://{self.gcs_bucket}/model_artifacts/{self.model_display_name}/"

        # Register in Vertex AI Model Registry
        model = aiplatform.Model.upload(
            display_name=self.model_display_name,
            artifact_uri=artifact_uri,
            serving_container_image_uri=self.serving_image,
            labels={"stage": self.staging_label},
        )

        logger.info(
            "Model registered: {} | Version: {} | Label: {}",
            model.resource_name,
            model.version_id,
            self.staging_label,
        )

        # Log to experiment
        with self.vertex_ai_service.run_context("register-model"):
            self.vertex_ai_service.log_params({
                "model_display_name": self.model_display_name,
                "model_version": model.version_id or "1",
                "stage_label": self.staging_label,
            })

        return {
            "model_name": model.resource_name,
            "model_version": model.version_id,
            "artifact_uri": artifact_uri,
        }
