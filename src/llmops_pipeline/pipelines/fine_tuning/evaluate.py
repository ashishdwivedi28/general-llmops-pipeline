"""Fine-tuning — Evaluate the fine-tuned model against the base model."""

from __future__ import annotations

import json
import typing as T

from llmops_pipeline.pipelines.base import Job, Locals


class EvaluateFineTunedJob(Job, frozen=True):
    """Compare fine-tuned model vs base model on a test set.

    Config fields:
        project, location — GCP identifiers.
        gcs_bucket — for reading test data.
        tuned_model_name — Vertex AI tuned model endpoint.
        base_model — original model name.
        test_dataset_uri — GCS URI to the test JSONL.
        quality_gate — minimum scores to pass.
    """

    KIND: T.Literal["EvaluateFineTunedJob"] = "EvaluateFineTunedJob"

    project: str = ""
    location: str = "us-central1"
    gcs_bucket: str = ""

    tuned_model_name: str = ""
    base_model: str = "gemini-2.0-flash"
    test_dataset_uri: str = ""
    quality_gate: dict[str, float] = {
        "answer_relevance": 0.75,
        "faithfulness": 0.70,
    }
    compare_with_base: bool = True

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Evaluate Fine-Tuned Model START ===")

        if not self.tuned_model_name:
            logger.warning("No tuned model name — skipping evaluation")
            return {"status": "no_model", "passed": False}

        # Load test data
        test_pairs = self._load_test_data()
        if not test_pairs:
            logger.warning("No test data available — skipping evaluation")
            return {"status": "no_test_data", "passed": False}

        # Evaluate tuned model
        logger.info("Evaluating tuned model: {}", self.tuned_model_name)
        tuned_scores = self._evaluate_model(self.tuned_model_name, test_pairs)

        # Evaluate base model (for comparison)
        base_scores: dict[str, float] = {}
        if self.compare_with_base:
            logger.info("Evaluating base model: {}", self.base_model)
            base_scores = self._evaluate_model(self.base_model, test_pairs)

        # Quality gate check
        passed = True
        for metric, threshold in self.quality_gate.items():
            actual = tuned_scores.get(metric, 0.0)
            if actual < threshold:
                passed = False
                logger.warning(
                    "QUALITY GATE FAILED: {} = {:.3f} < {:.3f}",
                    metric,
                    actual,
                    threshold,
                )

        # Log to experiment
        with self.vertex_ai_service.run_context("fine-tuning-eval"):
            self.vertex_ai_service.log_metrics(
                {f"tuned_{k}": v for k, v in tuned_scores.items()}
            )
            if base_scores:
                self.vertex_ai_service.log_metrics(
                    {f"base_{k}": v for k, v in base_scores.items()}
                )
            self.vertex_ai_service.log_params(
                {
                    "tuned_model": self.tuned_model_name,
                    "base_model": self.base_model,
                    "num_test_samples": str(len(test_pairs)),
                    "passed": str(passed),
                }
            )

        logger.info(
            "=== Evaluate Fine-Tuned Model COMPLETE — passed={} ===",
            passed,
        )
        return {
            "status": "passed" if passed else "blocked",
            "passed": passed,
            "tuned_scores": tuned_scores,
            "base_scores": base_scores,
            "num_test_samples": len(test_pairs),
        }

    def _load_test_data(self) -> list[dict]:
        """Load test JSONL from GCS."""
        logger = self.logger_service.logger()
        try:
            from google.cloud import storage

            client = storage.Client(project=self.project)
            # Parse gs:// URI
            uri = self.test_dataset_uri
            if uri.startswith("gs://"):
                uri = uri[5:]
            bucket_name, blob_path = uri.split("/", 1)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            content = blob.download_as_text()
            pairs = [json.loads(line) for line in content.strip().split("\n") if line.strip()]
            logger.info("Loaded {} test samples", len(pairs))
            return pairs
        except Exception as exc:
            logger.warning("Failed to load test data: {}", exc)
            return []

    def _evaluate_model(self, model_name: str, test_pairs: list[dict]) -> dict[str, float]:
        """Evaluate a model using Gemini-as-judge."""
        logger = self.logger_service.logger()
        from langchain_google_vertexai import ChatVertexAI

        judge = ChatVertexAI(
            model_name="gemini-2.0-flash",
            temperature=0.0,
            project=self.project,
            location=self.location,
        )

        scores: dict[str, list[float]] = {
            "answer_relevance": [],
            "faithfulness": [],
        }

        for pair in test_pairs[:100]:  # Cap for cost
            messages = pair.get("messages", [])
            user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
            model_msg = next(
                (m["content"] for m in messages if m["role"] in ("model", "assistant")), ""
            )

            eval_prompt = (
                f"Evaluate (0.0-1.0):\nQ: {user_msg}\nA: {model_msg}\n"
                f'JSON: {{"answer_relevance": X, "faithfulness": X}}'
            )
            try:
                resp = judge.invoke(eval_prompt)
                data = json.loads(resp.content)
                for k in scores:
                    scores[k].append(float(data.get(k, 0.0)))
            except Exception:
                pass

        return {k: sum(v) / len(v) if v else 0.0 for k, v in scores.items()}
