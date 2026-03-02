"""Feature Engineering — Ingest documents into Vector DB."""

from __future__ import annotations

import typing as T

from llmops_pipeline.io.vector_db import VertexVectorSearch
from llmops_pipeline.pipelines.base import Job, Locals


class IngestDocumentsJob(Job, frozen=True):
    """Load documents → chunk → embed → upload to GCS for Matching Engine.

    Config fields:
        embedding_model: Vertex AI embedding model name.
        embedding_dimensions: embedding vector size.
        document_path: local or GCS path to documents folder.
        gcs_bucket: GCS bucket for embeddings.
        chunk_size: characters per chunk.
        chunk_overlap: overlap between chunks.
        project: GCP project ID.
        location: GCP region.
    """

    KIND: T.Literal["IngestDocumentsJob"] = "IngestDocumentsJob"

    embedding_model: str = "text-embedding-004"
    embedding_dimensions: int = 768
    document_path: str = "data/documents/"
    gcs_bucket: str = ""
    chunk_size: int = 1000
    chunk_overlap: int = 200
    project: str = ""
    location: str = "us-central1"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("Ingesting documents from: {}", self.document_path)

        vs = VertexVectorSearch(
            project=self.project,
            location=self.location,
            embedding_model=self.embedding_model,
            embedding_dimensions=self.embedding_dimensions,
        )

        stats = vs.ingest_documents(
            document_path=self.document_path,
            gcs_bucket=self.gcs_bucket,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )

        # Log metrics to Vertex AI Experiments
        with self.vertex_ai_service.run_context("ingest-documents"):
            self.vertex_ai_service.log_metrics({
                "num_documents": float(stats["num_documents"]),
                "num_chunks": float(stats["num_chunks"]),
            })
            self.vertex_ai_service.log_params({
                "embedding_model": self.embedding_model,
                "chunk_size": str(self.chunk_size),
                "chunk_overlap": str(self.chunk_overlap),
            })

        logger.info("Ingestion complete: {} docs → {} chunks", stats["num_documents"], stats["num_chunks"])
        return stats
