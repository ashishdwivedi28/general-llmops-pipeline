"""Agent callbacks — logging, guardrails, and memory management."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery

logger = logging.getLogger(__name__)


class InteractionLogger:
    """Logs agent interactions to Cloud Logging and optionally BigQuery.

    Structured logging with the following fields:
    - timestamp, session_id, user_query, agent_response, latency_ms, tool_calls
    """

    def __init__(self, project_id: str, bq_dataset: str = "", bq_table: str = ""):
        self.project_id = project_id
        self.bq_client = None
        self.bq_table_ref = ""

        if bq_dataset and bq_table:
            try:
                self.bq_client = bigquery.Client(project=project_id)
                self.bq_table_ref = f"{project_id}.{bq_dataset}.{bq_table}"
                logger.info("BigQuery logging enabled: %s", self.bq_table_ref)
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
    ) -> None:
        """Log a single interaction."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user_query": user_query,
            "agent_response": agent_response[:2000],  # Truncate long responses
            "latency_ms": latency_ms,
            "tool_calls": json.dumps(tool_calls or []),
            "metadata": json.dumps(metadata or {}),
        }

        # Structured Cloud Logging
        logger.info("INTERACTION: %s", json.dumps(record))

        # BigQuery (if configured)
        if self.bq_client and self.bq_table_ref:
            try:
                errors = self.bq_client.insert_rows_json(self.bq_table_ref, [record])
                if errors:
                    logger.warning("BigQuery insert errors: %s", errors)
            except Exception as e:
                logger.warning("BigQuery logging failed: %s", e)

    def log_feedback(
        self,
        session_id: str,
        interaction_id: str,
        rating: int,
        comment: str = "",
    ) -> None:
        """Log user feedback for an interaction."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "interaction_id": interaction_id,
            "rating": rating,
            "comment": comment,
        }
        logger.info("FEEDBACK: %s", json.dumps(record))


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
