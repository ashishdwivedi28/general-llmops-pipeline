"""Manager — Monitoring orchestrator.

Chains: PostDeployEvalJob (→ optional retrigger signal)
"""

from __future__ import annotations

import typing as T

from llmops_pipeline.pipelines.base import Job, Locals
from llmops_pipeline.pipelines.monitoring.post_deploy_eval import PostDeployEvalJob


class MonitoringJob(Job, frozen=True):
    """Orchestrates the monitoring pipeline.

    1. Runs post-deployment evaluation on production traces.
    2. Returns degradation signal (for master pipeline conditional re-trigger).

    Config: ``monitoring.yaml``
    """

    KIND: T.Literal["MonitoringJob"] = "MonitoringJob"

    # Shared
    project: str = ""
    location: str = "us-central1"

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

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Monitoring Pipeline START ===")

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
            result = runner.run()

        degraded = result.get("degraded", False)

        if degraded and self.alert_on_degradation:
            logger.warning("ALERT: Quality degradation detected!")
            # In production, this would send a PubSub message or Cloud Function trigger

        if degraded and self.retrigger_feature_engineering:
            logger.warning("RETRIGGER: Will signal master pipeline to re-run feature engineering")

        logger.info("=== Monitoring Pipeline COMPLETE — status: {} ===", result.get("status"))
        return {**result, "retrigger": degraded and self.retrigger_feature_engineering}
