"""Feature Engineering — Create Vector DB index."""

from __future__ import annotations

import typing as T

from llmops_pipeline.io.vector_db import VertexVectorSearch
from llmops_pipeline.pipelines.base import Job, Locals


class CreateVectorDBJob(Job, frozen=True):
    """Create an empty Vertex AI Vector Search index and deploy to an endpoint.

    Config fields:
        embedding_model: Vertex AI embedding model name.
        embedding_dimensions: embedding vector size.
        index_display_name: display name for the index.
        index_endpoint_display_name: display name for the endpoint.
        gcs_bucket: GCS bucket for embedding storage.
        project: GCP project ID.
        location: GCP region.
    """

    KIND: T.Literal["CreateVectorDBJob"] = "CreateVectorDBJob"

    embedding_model: str = "text-embedding-004"
    embedding_dimensions: int = 768
    index_display_name: str = "llmops-vector-index"
    index_endpoint_display_name: str = "llmops-vector-endpoint"
    gcs_bucket: str = ""
    project: str = ""
    location: str = "us-central1"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("Creating Vertex AI Vector Search index: {}", self.index_display_name)

        vs = VertexVectorSearch(
            project=self.project,
            location=self.location,
            embedding_model=self.embedding_model,
            embedding_dimensions=self.embedding_dimensions,
        )

        index = vs.create_index(
            display_name=self.index_display_name,
            gcs_bucket=self.gcs_bucket,
        )

        endpoint = vs.deploy_index(
            index=index,
            endpoint_display_name=self.index_endpoint_display_name,
        )

        logger.info("Index endpoint deployed: {}", endpoint.resource_name)
        return {
            "index_name": index.resource_name,
            "endpoint_name": endpoint.resource_name,
        }
