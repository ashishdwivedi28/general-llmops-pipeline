"""Agent callbacks — logging, guardrails, and memory management.

Provides:
- **InteractionLogger** — Structured Cloud Logging + BigQuery for chat interactions
  and user feedback.  Records model, prompt version, token counts, and cost for
  each request so the monitoring pipeline and dashboard can consume them.
- **GuardrailChecker** — Simple input/output guardrails (topic filter, PII detection).

BigQuery tables (created by Terraform):
- ``{dataset}.interactions`` — every chat Q&A pair
- ``{dataset}.feedback`` — user thumbs-up/down
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class InteractionLogger:
    """Logs agent interactions to Cloud Logging and optionally BigQuery.

    Structured logging with the following fields:
    - timestamp, session_id, user_query, agent_response, latency_ms, tool_calls
    - model, prompt_version, input_tokens, output_tokens, cost_usd (Phase C additions)

    Two BigQuery tables are supported:
    - ``interactions`` — one row per Q&A turn
    - ``feedback`` — one row per user rating
    """

    def __init__(
        self,
        project_id: str,
        bq_dataset: str = "",
        bq_interactions_table: str = "interactions",
        bq_feedback_table: str = "feedback",
    ) -> None:
        self.project_id = project_id
        self.bq_client: Any = None
        self.bq_interactions_ref = ""
        self.bq_feedback_ref = ""

        if bq_dataset:
            try:
                from google.cloud import bigquery

                self.bq_client = bigquery.Client(project=project_id)
                self.bq_interactions_ref = f"{project_id}.{bq_dataset}.{bq_interactions_table}"
                self.bq_feedback_ref = f"{project_id}.{bq_dataset}.{bq_feedback_table}"
                logger.info(
                    "BigQuery logging enabled: interactions=%s, feedback=%s",
                    self.bq_interactions_ref,
                    self.bq_feedback_ref,
                )
            except Exception as e:
                logger.warning("BigQuery client init failed: %s", e)

    def log_interaction(
        self,
        session_id: str,
        user_query: str,
        agent_response: str,
        latency_ms: float = 0.0,
        tool_calls: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        model: str = "",
        prompt_version: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Log a single interaction to Cloud Logging and BigQuery."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user_query": user_query,
            "agent_response": agent_response[:2000],  # Truncate long responses
            "latency_ms": latency_ms,
            "tool_calls": json.dumps(tool_calls or []),
            "metadata": json.dumps(metadata or {}),
            "model": model,
            "prompt_version": prompt_version,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        }

        # Structured Cloud Logging (consumed by monitoring pipeline)
        logger.info("INTERACTION: %s", json.dumps(record))

        # BigQuery — interactions table
        if self.bq_client and self.bq_interactions_ref:
            try:
                errors = self.bq_client.insert_rows_json(self.bq_interactions_ref, [record])
                if errors:
                    logger.warning("BigQuery insert errors (interactions): %s", errors)
            except Exception as e:
                logger.warning("BigQuery interactions logging failed: %s", e)

    def log_feedback(
        self,
        session_id: str,
        interaction_id: str,
        rating: int,
        comment: str = "",
        *,
        model: str = "",
        prompt_version: str = "",
    ) -> None:
        """Log user feedback for an interaction."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "interaction_id": interaction_id,
            "rating": rating,
            "comment": comment,
            "model": model,
            "prompt_version": prompt_version,
        }
        # Structured Cloud Logging
        logger.info("FEEDBACK: %s", json.dumps(record))

        # BigQuery — feedback table
        if self.bq_client and self.bq_feedback_ref:
            try:
                errors = self.bq_client.insert_rows_json(self.bq_feedback_ref, [record])
                if errors:
                    logger.warning("BigQuery insert errors (feedback): %s", errors)
            except Exception as e:
                logger.warning("BigQuery feedback logging failed: %s", e)


class GuardrailChecker:
    """Pre/post-processing guardrails for the agent."""

    def __init__(self, valid_topics: list[str] = None, invalid_topics: list[str] = None):
        self.valid_topics = [t.lower() for t in (valid_topics or [])]
        self.invalid_topics = [t.lower() for t in (invalid_topics or [])]

    def check_input(self, query: str) -> tuple[bool, str]:
        """Check if the input query passes guardrails.

        Returns:
            Tuple of (is_allowed, reason).
        """
        query_lower = query.lower()

        # Check invalid topics
        for topic in self.invalid_topics:
            if topic in query_lower:
                return False, f"Query touches restricted topic: {topic}"

        return True, "ok"

    def check_output(self, response: str) -> tuple[bool, str]:
        """Check if the agent response passes output guardrails.

        Returns:
            Tuple of (is_allowed, reason).
        """
        # Basic PII check (can be extended with Cloud DLP)
        pii_patterns = ["ssn:", "social security", "credit card"]
        response_lower = response.lower()
        for pattern in pii_patterns:
            if pattern in response_lower:
                return False, f"Response may contain PII: {pattern}"

        return True, "ok"
