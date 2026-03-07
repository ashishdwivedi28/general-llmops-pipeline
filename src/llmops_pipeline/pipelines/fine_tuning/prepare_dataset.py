"""Fine-tuning — Prepare training dataset from production feedback.

Reads BigQuery interactions + feedback tables, joins on session_id,
filters for high-quality pairs (rating >= threshold), and writes
train/test JSONL files to GCS.
"""

from __future__ import annotations

import json
import typing as T
from datetime import datetime, timezone

from llmops_pipeline.pipelines.base import Job, Locals


class PrepareDatasetJob(Job, frozen=True):
    """Build a fine-tuning dataset from production interactions + feedback.

    Steps:
      1. Query BigQuery for rated interactions.
      2. Filter by minimum rating.
      3. Format as Vertex AI JSONL (messages format).
      4. Split into train/test.
      5. Upload to GCS.

    Config fields:
        project, location, gcs_bucket — GCP identifiers.
        bq_dataset — BigQuery dataset name.
        min_rating — minimum feedback rating to include (1-5).
        min_samples — minimum samples to proceed with fine-tuning.
        max_samples — maximum samples for cost control.
        test_split_ratio — fraction for test set.
        output_gcs_path — GCS prefix for output files.
    """

    KIND: T.Literal["PrepareDatasetJob"] = "PrepareDatasetJob"

    project: str = ""
    location: str = "us-central1"
    gcs_bucket: str = ""

    bq_dataset: str = "llmops"
    bq_interactions_table: str = "interactions"
    bq_feedback_table: str = "feedback"
    min_rating: int = 4
    min_samples: int = 100
    max_samples: int = 10000
    test_split_ratio: float = 0.2
    output_gcs_path: str = "fine_tuning/datasets/"
    format: str = "jsonl"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Prepare Fine-Tuning Dataset START ===")

        # Query BigQuery
        pairs = self._query_rated_interactions()

        if len(pairs) < self.min_samples:
            logger.warning(
                "Insufficient samples: {} < {} minimum — skipping fine-tuning",
                len(pairs),
                self.min_samples,
            )
            return {"status": "insufficient_data", "num_samples": len(pairs)}

        # Cap samples
        if len(pairs) > self.max_samples:
            pairs = pairs[: self.max_samples]
            logger.info("Capped to {} samples", self.max_samples)

        # Format as Vertex AI JSONL
        formatted = self._format_jsonl(pairs)

        # Split train/test
        split_idx = int(len(formatted) * (1 - self.test_split_ratio))
        train_data = formatted[:split_idx]
        test_data = formatted[split_idx:]

        logger.info("Train: {} samples, Test: {} samples", len(train_data), len(test_data))

        # Upload to GCS
        train_path = f"{self.output_gcs_path}train.jsonl"
        test_path = f"{self.output_gcs_path}test.jsonl"
        self._upload_jsonl(train_data, train_path)
        self._upload_jsonl(test_data, test_path)

        logger.info("=== Prepare Fine-Tuning Dataset COMPLETE ===")
        return {
            "status": "ready",
            "num_train": len(train_data),
            "num_test": len(test_data),
            "train_gcs_uri": f"gs://{self.gcs_bucket}/{train_path}",
            "test_gcs_uri": f"gs://{self.gcs_bucket}/{test_path}",
        }

    def _query_rated_interactions(self) -> list[dict]:
        """Query BQ for interactions with high-rated feedback."""
        logger = self.logger_service.logger()
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=self.project)
            query = f"""
                SELECT
                    i.user_query,
                    i.agent_response,
                    i.model,
                    f.rating
                FROM `{self.project}.{self.bq_dataset}.{self.bq_interactions_table}` i
                INNER JOIN `{self.project}.{self.bq_dataset}.{self.bq_feedback_table}` f
                    ON i.session_id = f.session_id
                WHERE f.rating >= {self.min_rating}
                    AND i.agent_response IS NOT NULL
                    AND LENGTH(i.agent_response) > 10
                ORDER BY f.timestamp DESC
                LIMIT {self.max_samples}
            """
            rows = list(client.query(query).result())
            logger.info("Queried {} rated interactions from BigQuery", len(rows))
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.warning("BigQuery query failed: {}", exc)
            return []

    def _format_jsonl(self, pairs: list[dict]) -> list[str]:
        """Convert QA pairs to Vertex AI fine-tuning JSONL."""
        lines = []
        for pair in pairs:
            record = {
                "messages": [
                    {"role": "user", "content": pair.get("user_query", "")},
                    {"role": "model", "content": pair.get("agent_response", "")},
                ]
            }
            lines.append(json.dumps(record, ensure_ascii=False))
        return lines

    def _upload_jsonl(self, lines: list[str], gcs_path: str) -> None:
        """Upload JSONL data to GCS."""
        logger = self.logger_service.logger()
        try:
            from google.cloud import storage

            client = storage.Client(project=self.project)
            bucket = client.bucket(self.gcs_bucket)
            blob = bucket.blob(gcs_path)
            blob.upload_from_string("\n".join(lines), content_type="application/jsonl")
            logger.info("Uploaded {} lines to gs://{}/{}", len(lines), self.gcs_bucket, gcs_path)
        except Exception as exc:
            logger.warning("GCS upload failed: {}", exc)
            raise
