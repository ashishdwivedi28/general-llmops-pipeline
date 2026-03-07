"""Monitoring — Root-cause diagnosis for production degradation.

Analyses recent telemetry to determine *why* quality degraded:
  - Data drift (embedding distance shift)
  - Model regression (score trend)
  - Prompt drift (prompt version mismatch)
  - Infrastructure issues (latency spike, error rate)

Returns a ``DiagnosisReport`` that the remediation step consumes.
"""

from __future__ import annotations

import json
import typing as T
from datetime import datetime, timezone

import pydantic as pdt

from llmops_pipeline.pipelines.base import Job, Locals


class DiagnosisCategory(pdt.BaseModel, frozen=True):
    """Single root-cause signal."""

    name: str
    detected: bool = False
    confidence: float = 0.0
    evidence: str = ""


class DiagnosisReport(pdt.BaseModel, frozen=True):
    """Full diagnosis output consumed by remediate step."""

    timestamp: str = ""
    degraded: bool = False
    primary_cause: str = "unknown"
    categories: list[DiagnosisCategory] = []
    recommended_actions: list[str] = []
    raw_scores: dict[str, float] = {}
    num_traces: int = 0


class DiagnoseJob(Job, frozen=True):
    """Runs root-cause analysis on monitoring signals.

    Reads the monitoring evaluation result (scores, num_traces) that was
    produced by ``PostDeployEvalJob`` and inspects Cloud Logging / BigQuery
    for anomalies.

    Config fields:
        project, location  — GCP identifiers.
        monitoring_scores   — output of post_deploy_eval (injected by manager).
        metric_thresholds   — per-metric acceptable bounds.
        latency_spike_ms    — if p95 latency exceeds this, flag infra issue.
        error_rate_threshold — if >X% of traces are errors, flag infra issue.
        bq_dataset          — BigQuery dataset for querying cost/interaction tables.
    """

    KIND: T.Literal["DiagnoseJob"] = "DiagnoseJob"

    project: str = ""
    location: str = "us-central1"
    bq_dataset: str = "llmops"

    # Input from upstream evaluation
    monitoring_scores: dict[str, float] = {}
    num_traces: int = 0
    degraded: bool = False

    # Thresholds
    metric_thresholds: dict[str, float] = {
        "answer_relevance": 0.70,
        "faithfulness": 0.65,
        "toxicity": 0.10,
    }
    latency_spike_ms: float = 5000.0
    error_rate_threshold: float = 0.05

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Diagnosis START ===")

        if not self.degraded:
            logger.info("No degradation — skipping diagnosis")
            report = DiagnosisReport(
                timestamp=datetime.now(timezone.utc).isoformat(),
                degraded=False,
                primary_cause="none",
                raw_scores=self.monitoring_scores,
                num_traces=self.num_traces,
            )
            return {"report": report.model_dump()}

        categories: list[DiagnosisCategory] = []
        actions: list[str] = []

        # ---- 1. Check metric-level degradation ----
        relevance = self.monitoring_scores.get("answer_relevance", 1.0)
        faithfulness = self.monitoring_scores.get("faithfulness", 1.0)
        toxicity = self.monitoring_scores.get("toxicity", 0.0)

        if relevance < self.metric_thresholds.get("answer_relevance", 0.7):
            categories.append(
                DiagnosisCategory(
                    name="low_relevance",
                    detected=True,
                    confidence=0.8,
                    evidence=f"answer_relevance={relevance:.3f} < threshold",
                )
            )
            actions.append("retrigger_feature_engineering")  # stale embeddings

        if faithfulness < self.metric_thresholds.get("faithfulness", 0.65):
            categories.append(
                DiagnosisCategory(
                    name="low_faithfulness",
                    detected=True,
                    confidence=0.75,
                    evidence=f"faithfulness={faithfulness:.3f} < threshold",
                )
            )
            actions.append("review_prompt_version")

        if toxicity > self.metric_thresholds.get("toxicity", 0.1):
            categories.append(
                DiagnosisCategory(
                    name="high_toxicity",
                    detected=True,
                    confidence=0.9,
                    evidence=f"toxicity={toxicity:.3f} > threshold",
                )
            )
            actions.append("rollback_prompt_version")

        # ---- 2. Check infrastructure signals via BigQuery ----
        infra_issues = self._check_infrastructure()
        if infra_issues:
            categories.append(infra_issues)
            actions.append("investigate_infrastructure")

        # ---- 3. Determine primary cause ----
        primary = "unknown"
        max_conf = 0.0
        for cat in categories:
            if cat.detected and cat.confidence > max_conf:
                max_conf = cat.confidence
                primary = cat.name

        report = DiagnosisReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            degraded=True,
            primary_cause=primary,
            categories=categories,
            recommended_actions=list(set(actions)),
            raw_scores=self.monitoring_scores,
            num_traces=self.num_traces,
        )

        logger.info(
            "Diagnosis complete — primary_cause={}, actions={}",
            primary,
            report.recommended_actions,
        )
        logger.info("=== Diagnosis END ===")

        return {"report": report.model_dump()}

    def _check_infrastructure(self) -> DiagnosisCategory | None:
        """Query BigQuery for latency and error rate anomalies."""
        logger = self.logger_service.logger()
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=self.project)
            query = f"""
                SELECT
                    AVG(latency_ms) AS avg_latency,
                    APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] AS p95_latency,
                    COUNTIF(agent_response IS NULL) / COUNT(*) AS error_rate
                FROM `{self.project}.{self.bq_dataset}.interactions`
                WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
            """
            rows = list(client.query(query).result())

            if rows:
                row = rows[0]
                p95 = row.p95_latency or 0.0
                error_rate = row.error_rate or 0.0

                if p95 > self.latency_spike_ms or error_rate > self.error_rate_threshold:
                    return DiagnosisCategory(
                        name="infrastructure_issue",
                        detected=True,
                        confidence=0.7,
                        evidence=(
                            f"p95_latency={p95:.0f}ms "
                            f"(limit={self.latency_spike_ms}ms), "
                            f"error_rate={error_rate:.2%} "
                            f"(limit={self.error_rate_threshold:.2%})"
                        ),
                    )
        except Exception as exc:
            logger.warning("Infrastructure check failed (non-fatal): {}", exc)

        return None
