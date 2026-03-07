"""Manager — Monitoring orchestrator.

Chains: PostDeployEvalJob → DiagnoseJob → RemediateJob → Write manifest section.
"""

from __future__ import annotations

import typing as T

from llmops_pipeline.pipelines.base import Job, Locals
from llmops_pipeline.pipelines.monitoring.diagnose import DiagnoseJob
from llmops_pipeline.pipelines.monitoring.post_deploy_eval import PostDeployEvalJob
from llmops_pipeline.pipelines.monitoring.remediate import RemediateJob


class MonitoringJob(Job, frozen=True):
    """Orchestrates the monitoring pipeline.

    1. Runs post-deployment evaluation on production traces.
    2. Diagnoses root cause if quality degraded.
    3. Dispatches automated remediation.
    4. Writes the ``monitoring`` section of the artifact manifest.
    5. Returns degradation signal (for master pipeline conditional re-trigger).

    Config: ``monitoring.yaml``
    """

    KIND: T.Literal["MonitoringJob"] = "MonitoringJob"

    # Shared
    project: str = ""
    location: str = "us-central1"
    gcs_bucket: str = ""

    # Monitoring config
    model_display_name: str = "llmops-rag-chatbot"
    monitoring_window_days: int = 7
    metric_thresholds: dict[str, float] = {
        "answer_relevance": 0.70,
        "faithfulness": 0.65,
        "toxicity": 0.10,
    }
    log_filter: str = 'resource.type="cloud_run_revision" AND jsonPayload.type="inference"'
    alert_on_degradation: bool = True
    retrigger_feature_engineering: bool = True

    # Diagnosis
    bq_dataset: str = "llmops"
    latency_spike_ms: float = 5000.0
    error_rate_threshold: float = 0.05

    # Remediation
    auto_rollback_enabled: bool = True
    auto_retrigger_enabled: bool = True

    # Manifest
    app_id: str = "llmops-app"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Monitoring Pipeline START ===")

        # ---- Step 1: Evaluate ----
        eval_job = PostDeployEvalJob(
            KIND="PostDeployEvalJob",
            logger_service=self.logger_service,
            vertex_ai_service=self.vertex_ai_service,
            project=self.project,
            location=self.location,
            model_display_name=self.model_display_name,
            monitoring_window_days=self.monitoring_window_days,
            metric_thresholds=self.metric_thresholds,
            log_filter=self.log_filter,
        )
        with eval_job as runner:
            eval_result = runner.run()

        degraded = eval_result.get("degraded", False)

        # Write monitoring manifest section
        self._write_manifest(eval_result)

        # ---- Step 2: Diagnose (only if degraded) ----
        diagnosis_report: dict = {}
        if degraded:
            logger.warning("Quality degradation detected — running diagnosis")
            diag_job = DiagnoseJob(
                KIND="DiagnoseJob",
                logger_service=self.logger_service,
                vertex_ai_service=self.vertex_ai_service,
                project=self.project,
                location=self.location,
                bq_dataset=self.bq_dataset,
                monitoring_scores=eval_result.get("scores", {}),
                num_traces=eval_result.get("num_traces", 0),
                degraded=True,
                metric_thresholds=self.metric_thresholds,
                latency_spike_ms=self.latency_spike_ms,
                error_rate_threshold=self.error_rate_threshold,
            )
            with diag_job as diag_runner:
                diag_result = diag_runner.run()
            diagnosis_report = diag_result.get("report", {})
            logger.info(
                "Diagnosis primary_cause={}",
                diagnosis_report.get("primary_cause", "unknown"),
            )

            # ---- Step 3: Remediate ----
            logger.info("Running automated remediation")
            remed_job = RemediateJob(
                KIND="RemediateJob",
                logger_service=self.logger_service,
                vertex_ai_service=self.vertex_ai_service,
                project=self.project,
                location=self.location,
                gcs_bucket=self.gcs_bucket,
                diagnosis_report=diagnosis_report,
                auto_rollback_enabled=self.auto_rollback_enabled,
                auto_retrigger_enabled=self.auto_retrigger_enabled,
                app_id=self.app_id,
            )
            with remed_job as remed_runner:
                remed_result = remed_runner.run()
            logger.info("Remediation results: {}", remed_result.get("remediation", {}))
        else:
            logger.info("Quality is healthy — no diagnosis or remediation needed")

        should_retrigger = degraded and self.retrigger_feature_engineering

        logger.info(
            "=== Monitoring Pipeline COMPLETE — status: {}, retrigger: {} ===",
            eval_result.get("status"),
            should_retrigger,
        )
        return {
            **eval_result,
            "diagnosis": diagnosis_report,
            "retrigger": should_retrigger,
        }

    def _write_manifest(self, result: dict) -> None:
        """Write the monitoring section of the artifact manifest."""
        logger = self.logger_service.logger()
        try:
            from llmops_pipeline.io.manifest import update_section

            scores = result.get("scores", result.get("monitoring_scores", {}))
            update_section(
                app_id=self.app_id,
                section="monitoring",
                data={
                    "monitoring_scores": scores,
                    "num_traces_evaluated": result.get(
                        "num_traces", result.get("num_traces_evaluated", 0)
                    ),
                    "degraded": result.get("degraded", False),
                    "status": result.get("status", "unknown"),
                },
                bucket_name=self.gcs_bucket,
                project=self.project,
            )
            logger.info("Manifest monitoring section updated for app '{}'", self.app_id)
        except Exception as exc:
            logger.warning("Failed to update manifest: {} (non-fatal)", exc)
