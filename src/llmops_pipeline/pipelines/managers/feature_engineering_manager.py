"""Manager — Feature Engineering orchestrator.

Chains: CreateVectorDBJob → IngestDocumentsJob
"""

from __future__ import annotations

import typing as T

from llmops_pipeline.pipelines.base import Job, Locals
from llmops_pipeline.pipelines.feature_engineering.create_vector_db import CreateVectorDBJob
from llmops_pipeline.pipelines.feature_engineering.ingest_documents import IngestDocumentsJob


class FeatureEngineeringJob(Job, frozen=True):
    """Orchestrates the full feature engineering pipeline.

    1. Creates (or re-uses) a Vector Search index + endpoint.
    2. Ingests documents → chunks → embeds → uploads to the index.

    This is the top-level job dispatched from the ``feature_engineering.yaml``
    config via the discriminated-union ``KIND`` field.
    """

    KIND: T.Literal["FeatureEngineeringJob"] = "FeatureEngineeringJob"

    # Sub-job configs — passed through from YAML
    project: str = ""
    location: str = "us-central1"
    gcs_bucket: str = ""
    embedding_model: str = "text-embedding-004"
    embedding_dimensions: int = 768
    documents_path: str = "data/documents/"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    index_display_name: str = "llmops-vector-index"
    endpoint_display_name: str = "llmops-vector-endpoint"
    gcs_embeddings_path: str = ""  # gs://bucket/embeddings/

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("=== Feature Engineering Pipeline START ===")

        # Step 1: Create Vector DB
        logger.info("Step 1 / 2: Create Vector DB")
        create_job = CreateVectorDBJob(
            KIND="CreateVectorDBJob",
            logger_service=self.logger_service,
            vertex_ai_service=self.vertex_ai_service,
            project=self.project,
            location=self.location,
            index_display_name=self.index_display_name,
            endpoint_display_name=self.endpoint_display_name,
            embedding_model=self.embedding_model,
            embedding_dimensions=self.embedding_dimensions,
        )
        with create_job as runner:
            db_result = runner.run()

        logger.info("Vector DB ready: index={}, endpoint={}",
                     db_result.get("index_name"), db_result.get("endpoint_name"))

        # Step 2: Ingest Documents
        logger.info("Step 2 / 2: Ingest Documents")
        ingest_job = IngestDocumentsJob(
            KIND="IngestDocumentsJob",
            logger_service=self.logger_service,
            vertex_ai_service=self.vertex_ai_service,
            project=self.project,
            location=self.location,
            documents_path=self.documents_path,
            gcs_bucket=self.gcs_bucket,
            gcs_embeddings_path=self.gcs_embeddings_path,
            embedding_model=self.embedding_model,
            embedding_dimensions=self.embedding_dimensions,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        with ingest_job as runner:
            ingest_result = runner.run()

        logger.info("=== Feature Engineering Pipeline COMPLETE ===")
        return {**db_result, **ingest_result}
