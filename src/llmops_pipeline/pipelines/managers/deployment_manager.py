"""Manager — Deployment orchestrator.

Chains: RegisterModelJob → EvaluateAndDeployJob
"""

from __future__ import annotations

import typing as T

from llmops_pipeline.pipelines.base import Job, Locals
from llmops_pipeline.pipelines.deployment.register_model import RegisterModelJob
from llmops_pipeline.pipelines.deployment.evaluate_and_deploy import EvaluateAndDeployJob


class DeploymentJob(Job, frozen=True):
    """Orchestrates the full deployment pipeline.

    1. Registers the RAG config as a model in Vertex AI Model Registry.
    2. Evaluates the model with QA dataset using Gemini-as-judge.
    3. Auto-promotes to production if quality gates pass.

    Config: ``deployment.yaml``
    """

    KIND: T.Literal["DeploymentJob"] = "DeploymentJob"

    # Shared
    project: str = ""
    location: str = "us-central1"
    gcs_bucket: str = ""

    # Registration
    model_display_name: str = "llmops-rag-chatbot"
    serving_image: str = "us-docker.pkg.dev/vertex-ai/prediction/tf2-cpu.2-12:latest"
    config_yaml_path: str = "confs/rag_chain_config.yaml"

    # Evaluation
    eval_dataset_csv: str = "data/datasets/rag_eval.csv"
    metric_thresholds: dict[str, float] = {
        "answer_relevance": 0.70,
        "faithfulness": 0.65,
        "toxicity": 0.10,
    }
    automatic_deployment: bool = True

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Deployment Pipeline START ===")

        # Step 1: Register Model
        logger.info("Step 1 / 2: Register Model")
        reg_job = RegisterModelJob(
            KIND="RegisterModelJob",
            logger_service=self.logger_service,
            vertex_ai_service=self.vertex_ai_service,
            project=self.project,
            location=self.location,
            gcs_bucket=self.gcs_bucket,
            model_display_name=self.model_display_name,
            serving_image=self.serving_image,
            config_yaml_path=self.config_yaml_path,
        )
        with reg_job as runner:
            reg_result = runner.run()

        model_resource = reg_result.get("model_resource_name", "")
        logger.info("Model registered: {}", model_resource)

        # Step 2: Evaluate + Deploy
        logger.info("Step 2 / 2: Evaluate & Deploy")
        eval_job = EvaluateAndDeployJob(
            KIND="EvaluateAndDeployJob",
            logger_service=self.logger_service,
            vertex_ai_service=self.vertex_ai_service,
            project=self.project,
            location=self.location,
            model_display_name=self.model_display_name,
            eval_dataset_csv=self.eval_dataset_csv,
            metric_thresholds=self.metric_thresholds,
            automatic_deployment=self.automatic_deployment,
        )
        with eval_job as runner:
            deploy_result = runner.run()

        decision = deploy_result.get("decision", "UNKNOWN")
        logger.info("=== Deployment Pipeline COMPLETE — Decision: {} ===", decision)
        return {**reg_result, **deploy_result}
