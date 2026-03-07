"""Cost tracker — token counting, cost calculation, and BigQuery logging.

Tracks per-request LLM costs and writes aggregated records to BigQuery.

Features:
- Token counting for input and output (from LiteLLM response metadata)
- Cost calculation using model-specific pricing (from models.yaml or LiteLLM defaults)
- Per-request cost logging to BigQuery ``costs`` table
- Accumulated cost tracking per app_id / model for dashboard analytics

Design:
    The ``CostTracker`` is initialised once during server startup and attached
    to ``request.state`` or called explicitly after each LLM interaction.
    All BigQuery writes are fire-and-forget (non-blocking, non-fatal).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CostRecord(BaseModel):
    """A single cost record for one LLM call."""

    model_config = ConfigDict(strict=True, frozen=True)

    timestamp: str
    app_id: str = "default"
    user_id: str = "anonymous"
    session_id: str = ""
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    endpoint: str = ""  # e.g. "/chat", "/embed"


class CostSummary(BaseModel):
    """Aggregated cost summary."""

    model_config = ConfigDict(strict=True, frozen=True)

    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    by_model: dict[str, float] = {}
    by_app: dict[str, float] = {}


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Tracks per-request LLM costs and logs to BigQuery.

    Usage::

        tracker = CostTracker(project_id="my-project",
                              bq_dataset="llmops", bq_table="costs")
        tracker.record(CostRecord(...))
        summary = tracker.summary()
    """

    def __init__(
        self,
        project_id: str = "",
        bq_dataset: str = "",
        bq_table: str = "costs",
    ) -> None:
        self.project_id = project_id
        self.bq_dataset = bq_dataset
        self.bq_table = bq_table
        self.bq_client: Any = None
        self.bq_table_ref = ""

        self._records: list[CostRecord] = []

        if project_id and bq_dataset and bq_table:
            try:
                from google.cloud import bigquery

                self.bq_client = bigquery.Client(project=project_id)
                self.bq_table_ref = f"{project_id}.{bq_dataset}.{bq_table}"
                logger.info("CostTracker BigQuery enabled: %s", self.bq_table_ref)
            except Exception as exc:
                logger.warning("CostTracker BigQuery init failed: %s", exc)

    # --- recording ---

    def record(self, rec: CostRecord) -> None:
        """Record a cost entry and async-write to BigQuery."""
        self._records.append(rec)
        logger.info(
            "COST: model=%s tokens=%d cost=$%.6f latency=%.0fms",
            rec.model,
            rec.total_tokens,
            rec.cost_usd,
            rec.latency_ms,
        )
        self._write_bigquery(rec)

    def record_from_response(
        self,
        *,
        model: str,
        provider: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        app_id: str = "default",
        user_id: str = "anonymous",
        session_id: str = "",
        endpoint: str = "/chat",
    ) -> CostRecord:
        """Build a CostRecord from individual values and record it.

        Returns the created CostRecord.
        """
        rec = CostRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            app_id=app_id,
            user_id=user_id,
            session_id=session_id,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            endpoint=endpoint,
        )
        self.record(rec)
        return rec

    # --- querying ---

    def summary(self) -> CostSummary:
        """Return an aggregated summary of all recorded costs."""
        by_model: dict[str, float] = {}
        by_app: dict[str, float] = {}
        total_in = 0
        total_out = 0
        total_cost = 0.0
        for r in self._records:
            total_in += r.input_tokens
            total_out += r.output_tokens
            total_cost += r.cost_usd
            by_model[r.model] = by_model.get(r.model, 0.0) + r.cost_usd
            by_app[r.app_id] = by_app.get(r.app_id, 0.0) + r.cost_usd
        return CostSummary(
            total_requests=len(self._records),
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            total_tokens=total_in + total_out,
            total_cost_usd=round(total_cost, 8),
            by_model=by_model,
            by_app=by_app,
        )

    def reset(self) -> None:
        """Clear all in-memory cost records."""
        self._records.clear()

    # --- BigQuery persistence ---

    def _write_bigquery(self, rec: CostRecord) -> None:
        """Write a single cost record to BigQuery (fire-and-forget)."""
        if not self.bq_client or not self.bq_table_ref:
            return
        try:
            row = rec.model_dump()
            errors = self.bq_client.insert_rows_json(self.bq_table_ref, [row])
            if errors:
                logger.warning("CostTracker BQ insert errors: %s", errors)
        except Exception as exc:
            logger.warning("CostTracker BQ write failed: %s", exc)


# ---------------------------------------------------------------------------
# Utility: estimate cost from token counts
# ---------------------------------------------------------------------------


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_per_1k_input: float = 0.0,
    cost_per_1k_output: float = 0.0,
) -> float:
    """Estimate USD cost from token counts and pricing.

    If per-1k costs are 0 (free-tier / Vertex AI included), attempts to use
    LiteLLM's built-in cost database as a fallback.
    """
    if cost_per_1k_input > 0 or cost_per_1k_output > 0:
        return (input_tokens / 1000 * cost_per_1k_input) + (
            output_tokens / 1000 * cost_per_1k_output
        )
    # Fallback: try LiteLLM cost estimation
    try:
        import litellm

        return litellm.completion_cost(
            model=model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
    except Exception:
        return 0.0
