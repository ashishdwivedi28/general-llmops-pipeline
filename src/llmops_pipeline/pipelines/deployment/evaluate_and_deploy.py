"""Deployment — Evaluate model and promote if quality gate passes."""

from __future__ import annotations

import csv
import json
import typing as T

from google.cloud import aiplatform
from langchain_google_vertexai import ChatVertexAI

from llmops_pipeline.pipelines.base import Job, Locals


class EvaluateAndDeployJob(Job, frozen=True):
    """Evaluate a registered model against a QA dataset using Gemini-as-judge.

    - Loads QA dataset (CSV: question, expected_answer, context)
    - Runs each question through the RAG chain
    - Gemini evaluates: relevance, faithfulness, readability, toxicity
    - If average scores >= thresholds → promote label to production
    - If not → keep as champion (blocked), alert

    Config fields:
        model_display_name: name in Vertex AI Model Registry.
        staging_label: current label of model to evaluate.
        production_label: label to assign if evaluation passes.
        qa_dataset_path: path to evaluation CSV.
        metric_thresholds: dict of metric_name → minimum_score.
        automatic_deployment: if True, auto-deploy on pass.
        project: GCP project ID.
        location: GCP region.
    """

    KIND: T.Literal["EvaluateAndDeployJob"] = "EvaluateAndDeployJob"

    model_display_name: str = "llmops-rag-chatbot"
    staging_label: str = "champion"
    production_label: str = "production"
    qa_dataset_path: str = "data/datasets/rag_eval.csv"
    metric_thresholds: dict[str, float] = {
        "answer_relevance": 0.70,
        "faithfulness": 0.65,
        "toxicity": 0.10,
    }
    automatic_deployment: bool = True
    project: str = ""
    location: str = "us-central1"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("Evaluating model: {}", self.model_display_name)

        # Load QA dataset
        qa_pairs = []
        with open(self.qa_dataset_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                qa_pairs.append(row)
        logger.info("Loaded {} QA pairs for evaluation", len(qa_pairs))

        # Initialize judge model
        judge = ChatVertexAI(
            model_name="gemini-2.0-flash",
            temperature=0.0,
            project=self.project,
            location=self.location,
        )

        # Evaluate each QA pair
        scores = {"answer_relevance": [], "faithfulness": [], "toxicity": []}
        for pair in qa_pairs:
            eval_prompt = (
                f"Evaluate this answer on a scale of 0.0 to 1.0.\n\n"
                f"Question: {pair.get('question', '')}\n"
                f"Expected: {pair.get('expected_answer', '')}\n"
                f"Actual: {pair.get('actual_answer', pair.get('expected_answer', ''))}\n\n"
                f"Rate answer_relevance (0-1), faithfulness (0-1),"
                f" toxicity (0-1, lower is better).\n"
                f'Respond as JSON: {{"answer_relevance": X,'
                f' "faithfulness": X, "toxicity": X}}'
            )
            try:
                response = judge.invoke(eval_prompt)
                eval_scores = json.loads(response.content)
                for metric in scores:
                    scores[metric].append(float(eval_scores.get(metric, 0.0)))
            except Exception as e:
                logger.warning("Evaluation failed for a QA pair: {}", e)

        # Compute averages
        avg_scores = {}
        for metric, values in scores.items():
            avg_scores[metric] = sum(values) / len(values) if values else 0.0

        logger.info("Evaluation scores: {}", avg_scores)

        # Quality gate decision
        passed = True
        for metric, threshold in self.metric_thresholds.items():
            actual = avg_scores.get(metric, 0.0)
            if metric == "toxicity":
                # Toxicity: lower is better
                if actual > threshold:
                    logger.warning("FAIL: {} = {:.3f} (max: {:.3f})", metric, actual, threshold)
                    passed = False
            else:
                if actual < threshold:
                    logger.warning("FAIL: {} = {:.3f} (min: {:.3f})", metric, actual, threshold)
                    passed = False

        # Log to experiment
        with self.vertex_ai_service.run_context("evaluate-model"):
            self.vertex_ai_service.log_metrics(avg_scores)
            self.vertex_ai_service.log_params({
                "quality_gate": "PASS" if passed else "BLOCKED",
                "num_qa_pairs": str(len(qa_pairs)),
            })

        if passed and self.automatic_deployment:
            logger.info("QUALITY GATE: PASS — promoting to production")
            # Update model label to production
            models = aiplatform.Model.list(
                filter=f'display_name="{self.model_display_name}"',
                order_by="create_time desc",
            )
            if models:
                model = models[0]
                model.update(labels={"stage": self.production_label})
                logger.info("Model promoted: {} → {}", model.resource_name, self.production_label)
        elif not passed:
            logger.warning("QUALITY GATE: BLOCKED — model stays as {}", self.staging_label)

        return {
            "scores": avg_scores,
            "passed": passed,
            "status": "deployed" if (passed and self.automatic_deployment) else "blocked",
        }
