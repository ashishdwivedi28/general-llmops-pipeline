"""Monitoring — Post-deployment evaluation using production traces."""

from __future__ import annotations

import json
import typing as T
from datetime import datetime, timedelta, timezone

from google.cloud import logging as cloud_logging
from langchain_google_vertexai import ChatVertexAI

from llmops_pipeline.pipelines.base import Job, Locals


class PostDeployEvalJob(Job, frozen=True):
    """Pull production traces from Cloud Logging → evaluate with Gemini-as-judge.

    - Fetches inference logs from the last N days
    - Extracts question/answer pairs from structured logs
    - Evaluates relevance, faithfulness, toxicity via Gemini
    - Logs metrics to Vertex AI Experiments
    - If degradation detected → returns alert signal

    Config fields:
        model_display_name: model being monitored.
        monitoring_window_days: how many days of traces to pull.
        metric_thresholds: minimum acceptable scores.
        log_filter: Cloud Logging filter for inference logs.
        project: GCP project ID.
        location: GCP region.
    """

    KIND: T.Literal["PostDeployEvalJob"] = "PostDeployEvalJob"

    model_display_name: str = "llmops-rag-chatbot"
    monitoring_window_days: int = 7
    metric_thresholds: dict[str, float] = {
        "answer_relevance": 0.70,
        "faithfulness": 0.65,
        "toxicity": 0.10,
    }
    log_filter: str = 'resource.type="cloud_run_revision" AND jsonPayload.type="inference"'
    project: str = ""
    location: str = "us-central1"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info(
            "Monitoring evaluation for: {} (last {} days)",
            self.model_display_name,
            self.monitoring_window_days,
        )

        # Pull traces from Cloud Logging
        client = cloud_logging.Client(project=self.project)
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(days=self.monitoring_window_days)

        full_filter = (
            f'{self.log_filter} AND '
            f'timestamp>="{start_time.isoformat()}" AND '
            f'timestamp<="{now.isoformat()}"'
        )

        entries = list(client.list_entries(filter_=full_filter, max_results=500))
        logger.info("Pulled {} inference traces from Cloud Logging", len(entries))

        if not entries:
            logger.warning("No inference traces found — skipping evaluation")
            return {"status": "no_data", "degraded": False}

        # Extract QA pairs from structured logs
        qa_pairs = []
        for entry in entries:
            payload = entry.payload
            if isinstance(payload, dict) and "question" in payload and "answer" in payload:
                qa_pairs.append({
                    "question": payload["question"],
                    "answer": payload["answer"],
                    "context": payload.get("context", ""),
                })

        logger.info("Extracted {} QA pairs from traces", len(qa_pairs))

        if not qa_pairs:
            logger.warning("No valid QA pairs in traces — skipping evaluation")
            return {"status": "no_qa_pairs", "degraded": False}

        # Evaluate with Gemini-as-judge
        judge = ChatVertexAI(
            model_name="gemini-2.0-flash",
            temperature=0.0,
            project=self.project,
            location=self.location,
        )

        scores: dict[str, list[float]] = {
            "answer_relevance": [],
            "faithfulness": [],
            "toxicity": [],
        }
        for pair in qa_pairs:
            eval_prompt = (
                f"Evaluate this production response on a scale of 0.0 to 1.0.\n\n"
                f"Question: {pair['question']}\n"
                f"Answer: {pair['answer']}\n"
                f"Context used: {pair.get('context', 'N/A')}\n\n"
                f"Rate: answer_relevance (0-1), faithfulness (0-1), toxicity (0-1 lower=better).\n"
                f'Respond as JSON: {{"answer_relevance": X, "faithfulness": X, "toxicity": X}}'
            )
            try:
                response = judge.invoke(eval_prompt)
                eval_result = json.loads(response.content)
                for metric in scores:
                    scores[metric].append(float(eval_result.get(metric, 0.0)))
            except Exception as e:
                logger.warning("Evaluation failed for a trace: {}", e)

        # Compute averages
        avg_scores = {}
        for metric, values in scores.items():
            avg_scores[metric] = sum(values) / len(values) if values else 0.0

        logger.info("Monitoring scores: {}", avg_scores)

        # Check for degradation
        degraded = False
        for metric, threshold in self.metric_thresholds.items():
            actual = avg_scores.get(metric, 0.0)
            if metric == "toxicity":
                if actual > threshold:
                    degraded = True
                    logger.warning("DEGRADED: {} = {:.3f} (max: {:.3f})", metric, actual, threshold)
            else:
                if actual < threshold:
                    degraded = True
                    logger.warning("DEGRADED: {} = {:.3f} (min: {:.3f})", metric, actual, threshold)

        # Log to experiment
        with self.vertex_ai_service.run_context("monitoring-eval"):
            self.vertex_ai_service.log_metrics(avg_scores)
            self.vertex_ai_service.log_params({
                "monitoring_window_days": str(self.monitoring_window_days),
                "num_traces_evaluated": str(len(qa_pairs)),
                "degraded": str(degraded),
            })

        if degraded:
            logger.warning("QUALITY DEGRADATION DETECTED — alerting and triggering re-pipeline")
        else:
            logger.info("Quality is healthy — no action needed")

        return {
            "scores": avg_scores,
            "degraded": degraded,
            "num_traces": len(qa_pairs),
            "status": "degraded" if degraded else "healthy",
        }
