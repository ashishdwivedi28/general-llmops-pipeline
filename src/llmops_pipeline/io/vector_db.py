"""Vector database abstraction — Vertex AI Vector Search implementation.

Supports create, ingest, query operations against Vertex AI Matching Engine.
"""

from __future__ import annotations

import json
import logging
import tempfile
import typing as T

from google.cloud import aiplatform, storage
from langchain_google_vertexai import VertexAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


class VertexVectorSearch:
    """Vertex AI Vector Search (Matching Engine) wrapper.

    Handles index creation, document ingestion (chunk → embed → upload), and querying.
    """

    def __init__(
        self,
        project: str,
        location: str = "us-central1",
        embedding_model: str = "text-embedding-004",
        embedding_dimensions: int = 768,
    ):
        self.project = project
        self.location = location
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions
        self._embeddings = VertexAIEmbeddings(
            model_name=embedding_model,
            project=project,
            location=location,
        )
        aiplatform.init(project=project, location=location)

    def create_index(
        self,
        display_name: str,
        gcs_bucket: str,
        approximate_neighbors_count: int = 10,
    ) -> aiplatform.MatchingEngineIndex:
        """Create a new tree-AH index for approximate nearest-neighbour search."""
        logger.info(f"Creating Vertex AI Vector Search index: {display_name}")
        index = aiplatform.MatchingEngineIndex.create_tree_ah_index(
            display_name=display_name,
            dimensions=self.embedding_dimensions,
            approximate_neighbors_count=approximate_neighbors_count,
            distance_measure_type="COSINE_DISTANCE",
            contents_delta_uri=f"gs://{gcs_bucket}/embeddings/",
        )
        logger.info(f"Index created: {index.resource_name}")
        return index

    def deploy_index(
        self,
        index: aiplatform.MatchingEngineIndex,
        endpoint_display_name: str,
        deployed_index_id: str = "deployed_index",
    ) -> aiplatform.MatchingEngineIndexEndpoint:
        """Deploy an index to an endpoint for online serving."""
        logger.info(f"Creating index endpoint: {endpoint_display_name}")
        endpoint = aiplatform.MatchingEngineIndexEndpoint.create(
            display_name=endpoint_display_name,
            public_endpoint_enabled=True,
        )
        logger.info(f"Deploying index to endpoint...")
        endpoint.deploy_index(
            index=index,
            deployed_index_id=deployed_index_id,
        )
        logger.info(f"Index deployed: {endpoint.resource_name}")
        return endpoint

    def ingest_documents(
        self,
        document_path: str,
        gcs_bucket: str,
        gcs_prefix: str = "embeddings",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> dict[str, T.Any]:
        """Load documents → chunk → embed → upload as JSONL to GCS.

        Returns dict with stats: num_documents, num_chunks, gcs_uri.
        """
        from langchain_community.document_loaders import DirectoryLoader

        logger.info(f"Loading documents from: {document_path}")
        loader = DirectoryLoader(document_path, show_progress=True)
        documents = loader.load()
        logger.info(f"Loaded {len(documents)} documents")

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        chunks = splitter.split_documents(documents)
        logger.info(f"Split into {len(chunks)} chunks")

        # Generate embeddings
        texts = [chunk.page_content for chunk in chunks]
        embeddings = self._embeddings.embed_documents(texts)

        # Write JSONL for Matching Engine
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            for i, (text, embedding) in enumerate(zip(texts, embeddings)):
                record = {
                    "id": str(i),
                    "embedding": embedding,
                    "restricts": [],
                    "crowding_tag": "",
                }
                f.write(json.dumps(record) + "\n")
            tmp_path = f.name

        # Upload to GCS
        client = storage.Client(project=self.project)
        bucket = client.bucket(gcs_bucket)
        blob = bucket.blob(f"{gcs_prefix}/embeddings.json")
        blob.upload_from_filename(tmp_path)
        gcs_uri = f"gs://{gcs_bucket}/{gcs_prefix}/embeddings.json"
        logger.info(f"Embeddings uploaded to {gcs_uri}")

        # Also upload chunk metadata for retrieval
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            for i, chunk in enumerate(chunks):
                record = {
                    "id": str(i),
                    "content": chunk.page_content,
                    "metadata": chunk.metadata,
                }
                f.write(json.dumps(record) + "\n")
            meta_path = f.name

        meta_blob = bucket.blob(f"{gcs_prefix}/chunks_metadata.json")
        meta_blob.upload_from_filename(meta_path)

        return {
            "num_documents": len(documents),
            "num_chunks": len(chunks),
            "gcs_uri": gcs_uri,
        }

    def query(
        self,
        query_text: str,
        index_endpoint: aiplatform.MatchingEngineIndexEndpoint,
        deployed_index_id: str = "deployed_index",
        top_k: int = 10,
    ) -> list[dict[str, T.Any]]:
        """Embed a query and search the deployed index."""
        query_embedding = self._embeddings.embed_query(query_text)
        response = index_endpoint.find_neighbors(
            deployed_index_id=deployed_index_id,
            queries=[query_embedding],
            num_neighbors=top_k,
        )
        results = []
        for neighbor in response[0]:
            results.append({
                "id": neighbor.id,
                "distance": neighbor.distance,
            })
        return results
