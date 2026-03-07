"""Monitoring — Automated remediation dispatcher.

Consumes a ``DiagnosisReport`` and executes the appropriate fix:
  - retrigger_feature_engineering  → re-runs vector DB pipeline
  - review_prompt_version          → logs alert + bumps to next prompt version
  - rollback_prompt_version        → reverts to previous stable prompt
  - investigate_infrastructure     → logs detailed alert (human review)

Each action is idempotent.  The remediation result is written to the manifest.
"""

from __future__ import annotations

import typing as T
from datetime import datetime, timezone

from llmops_pipeline.pipelines.base import Job, Locals


class RemediateJob(Job, frozen=True):
    """Dispatch remediation actions based on the diagnosis report.

    Config fields:
        project, location, gcs_bucket — GCP identifiers.
        diagnosis_report — dict from DiagnoseJob (injected by manager).
        auto_rollback_enabled — if True, prompt rollback runs automatically.
        auto_retrigger_enabled — if True, FE pipeline retrigger runs automatically.
        notification_channel — where to send alerts (Cloud Logging for now).
        app_id — for manifest writes.
    """

    KIND: T.Literal["RemediateJob"] = "RemediateJob"

    project: str = ""
    location: str = "us-central1"
    gcs_bucket: str = ""

    diagnosis_report: dict[str, T.Any] = {}

    auto_rollback_enabled: bool = True
    auto_retrigger_enabled: bool = True
    notification_channel: str = "cloud_logging"
    app_id: str = "llmops-app"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Remediation START ===")

        actions = self.diagnosis_report.get("recommended_actions", [])
        primary_cause = self.diagnosis_report.get("primary_cause", "unknown")
        results: dict[str, str] = {}

        if not actions:
            logger.info("No remediation actions needed")
            results["status"] = "no_action"
            return {"remediation": results}

        for action in actions:
            try:
                outcome = self._dispatch(action)
                results[action] = outcome
                logger.info("Action '{}' → {}", action, outcome)
            except Exception as exc:
                results[action] = f"failed: {exc}"
                logger.warning("Action '{}' failed: {}", action, exc)

        # Write remediation to manifest
        self._write_manifest(primary_cause, results)

        logger.info("=== Remediation COMPLETE ===")
        return {"remediation": results, "primary_cause": primary_cause}

    def _dispatch(self, action: str) -> str:
        """Route an action string to the appropriate handler."""
        logger = self.logger_service.logger()

        if action == "retrigger_feature_engineering":
            return self._retrigger_fe()
        elif action == "review_prompt_version":
            return self._review_prompt()
        elif action == "rollback_prompt_version":
            return self._rollback_prompt()
        elif action == "investigate_infrastructure":
            return self._alert_infra()
        else:
            logger.warning("Unknown remediation action: {}", action)
            return "skipped_unknown"

    # ---- Action handlers -------------------------------------------------------

    def _retrigger_fe(self) -> str:
        """Signal for feature-engineering re-run.

        In a KFP context this is handled by the master pipeline conditional
        branch.  Here we write a signal file to GCS so Cloud Functions or
        Scheduler can pick it up.
        """
        logger = self.logger_service.logger()
        if not self.auto_retrigger_enabled:
            logger.info("Auto-retrigger disabled — logging alert only")
            return "alert_only"

        try:
            from google.cloud import storage

            client = storage.Client(project=self.project)
            bucket = client.bucket(self.gcs_bucket)
            blob = bucket.blob("signals/retrigger_fe.json")
            blob.upload_from_string(
                f'{{"triggered_at": "{datetime.now(timezone.utc).isoformat()}", '
                f'"reason": "quality_degradation"}}',
                content_type="application/json",
            )
            logger.info("Wrote retrigger signal to gs://{}/signals/retrigger_fe.json", self.gcs_bucket)
            return "triggered"
        except Exception as exc:
            logger.warning("Failed to write retrigger signal: {}", exc)
            return f"failed: {exc}"

    def _review_prompt(self) -> str:
        """Log an alert recommending prompt review."""
        logger = self.logger_service.logger()
        logger.warning(
            "PROMPT REVIEW NEEDED: Faithfulness below threshold. "
            "Check prompt versions in confs/rag_chain_config.yaml or GCS prompt registry."
        )
        return "alert_sent"

    def _rollback_prompt(self) -> str:
        """Roll back to the previous stable prompt version via PromptRegistry."""
        logger = self.logger_service.logger()
        if not self.auto_rollback_enabled:
            logger.info("Auto-rollback disabled — logging alert only")
            return "alert_only"

        try:
            from llmops_pipeline.io.prompt_registry import (
                PromptRegistry,
                PromptRegistryConfig,
            )

            registry = PromptRegistry(
                config=PromptRegistryConfig(
                    app_id=self.app_id,
                    bucket_name=self.gcs_bucket,
                    project=self.project,
                )
            )
            # List versions and revert to the one before current
            versions = registry.list_versions()
            if len(versions) < 2:
                logger.warning("Only one prompt version — cannot rollback")
                return "no_previous_version"

            previous = versions[-2]  # second-to-last
            logger.info("Rolling back system_prompt to version {}", previous)
            # Update the registry config to point to the previous version
            registry.config = PromptRegistryConfig(
                app_id=self.app_id,
                bucket_name=self.gcs_bucket,
                project=self.project,
                active_version=previous,
            )
            registry.invalidate_cache()
            return f"rolled_back_to_{previous}"
        except Exception as exc:
            logger.warning("Prompt rollback failed: {}", exc)
            return f"failed: {exc}"

    def _alert_infra(self) -> str:
        """Alert about infrastructure issues."""
        logger = self.logger_service.logger()
        evidence = ""
        for cat in self.diagnosis_report.get("categories", []):
            if isinstance(cat, dict) and cat.get("name") == "infrastructure_issue":
                evidence = cat.get("evidence", "")
                break

        logger.warning(
            "INFRASTRUCTURE ALERT: Latency or error-rate anomaly detected. "
            "Evidence: {}. Requires manual investigation.",
            evidence,
        )
        return "alert_sent"

    # ---- Manifest ---------------------------------------------------------------

    def _write_manifest(self, primary_cause: str, results: dict[str, str]) -> None:
        """Write the remediation section to the artifact manifest."""
        logger = self.logger_service.logger()
        try:
            from llmops_pipeline.io.manifest import update_section

            update_section(
                app_id=self.app_id,
                section="monitoring",
                data={
                    "last_diagnosis": primary_cause,
                    "remediation_action": str(results),
                },
                bucket_name=self.gcs_bucket,
                project=self.project,
            )
            logger.info("Manifest remediation section updated for app '{}'", self.app_id)
        except Exception as exc:
            logger.warning("Failed to update manifest: {} (non-fatal)", exc)
