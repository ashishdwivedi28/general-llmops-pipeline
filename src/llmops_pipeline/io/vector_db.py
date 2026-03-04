"""Vector database abstraction — Vertex AI Vector Search implementation.

Supports create, ingest, query operations against Vertex AI Matching Engine.
"""

from __future__ import annotations

import json
import logging
import tempfile
import typing as T

from google.cloud import aiplatform, storage
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


def _get_embeddings(model_name: str, project: str, location: str):
    """Get embedding model — tries new package first, falls back to deprecated."""
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(
            model=f"models/{model_name}",
            google_api_key=None,  # Uses ADC
        )
    except ImportError:
        from langchain_google_vertexai import VertexAIEmbeddings

        return VertexAIEmbeddings(
            model_name=model_name,
            project=project,
            location=location,
        )


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
        self._embeddings = _get_embeddings(embedding_model, project, location)
        aiplatform.init(project=project, location=location)

    def _seed_empty_embeddings(self, gcs_bucket: str, gcs_prefix: str = "embeddings") -> str:
        """Upload a single seed embedding to GCS so create_tree_ah_index has valid data.

        Vertex AI requires contents_delta_uri to point to a non-empty GCS path
        with at least one valid JSONL record.
        """
        seed_record = {
            "id": "seed_0",
            "embedding": [0.0] * self.embedding_dimensions,
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(seed_record) + "\n")
            tmp_path = f.name

        client = storage.Client(project=self.project)
        bucket = client.bucket(gcs_bucket)
        blob = bucket.blob(f"{gcs_prefix}/seed_embeddings.json")
        blob.upload_from_filename(tmp_path)
        gcs_uri = f"gs://{gcs_bucket}/{gcs_prefix}/"
        logger.info(f"Seed embeddings uploaded to {gcs_uri}")
        return gcs_uri

    def create_index(
        self,
        display_name: str,
        gcs_bucket: str,
        approximate_neighbors_count: int = 10,
    ) -> aiplatform.MatchingEngineIndex:
        """Create a new tree-AH index for approximate nearest-neighbour search.

        First checks if an index with this display_name already exists.
        If so, returns the existing one. Otherwise creates a new index.
        """
        logger.info(f"Creating Vertex AI Vector Search index: {display_name}")

        # Check if index already exists
        existing = aiplatform.MatchingEngineIndex.list(filter=f'display_name="{display_name}"')
        if existing:
            logger.info(f"Index already exists: {existing[0].resource_name}")
            return existing[0]

        # Seed empty embeddings so the GCS path is valid
        contents_uri = self._seed_empty_embeddings(gcs_bucket)

        index = aiplatform.MatchingEngineIndex.create_tree_ah_index(
            display_name=display_name,
            dimensions=self.embedding_dimensions,
            approximate_neighbors_count=approximate_neighbors_count,
            leaf_node_embedding_count=500,
            leaf_nodes_to_search_percent=7,
            distance_measure_type="COSINE_DISTANCE",
            contents_delta_uri=contents_uri,
        )
        logger.info(f"Index created: {index.resource_name}")
        return index

    def deploy_index(
        self,
        index: aiplatform.MatchingEngineIndex,
        endpoint_display_name: str,
        deployed_index_id: str = "deployed_index",
    ) -> aiplatform.MatchingEngineIndexEndpoint:
        """Deploy an index to an endpoint for online serving.

        Checks if endpoint already exists. If so, returns existing.
        """
        logger.info(f"Looking for existing endpoint: {endpoint_display_name}")

        # Check if endpoint already exists
        existing_ep = aiplatform.MatchingEngineIndexEndpoint.list(
            filter=f'display_name="{endpoint_display_name}"'
        )
        if existing_ep:
            logger.info(f"Endpoint already exists: {existing_ep[0].resource_name}")
            return existing_ep[0]

        logger.info(f"Creating index endpoint: {endpoint_display_name}")
        endpoint = aiplatform.MatchingEngineIndexEndpoint.create(
            display_name=endpoint_display_name,
            public_endpoint_enabled=True,
        )
        logger.info("Deploying index to endpoint...")
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
        import glob
        import os

        logger.info(f"Loading documents from: {document_path}")

        # Use simple file loading instead of DirectoryLoader
        # (avoids unstructured/spaCy dependency issues on Cloud Shell)
        documents = []
        if os.path.isdir(document_path):
            for filepath in glob.glob(os.path.join(document_path, "**/*"), recursive=True):
                if os.path.isfile(filepath) and not filepath.endswith(".gitkeep"):
                    try:
                        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read().strip()
                        if content:
                            documents.append(
                                {
                                    "page_content": content,
                                    "metadata": {"source": filepath},
                                }
                            )
                    except Exception as e:
                        logger.warning(f"Skipping {filepath}: {e}")
        else:
            logger.warning(f"Document path not found: {document_path}")

        logger.info(f"Loaded {len(documents)} documents")

        if not documents:
            logger.warning("No documents found — skipping ingestion")
            return {"num_documents": 0, "num_chunks": 0, "gcs_uri": ""}

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        # Split using raw text
        from langchain_core.documents import Document as LCDocument

        lc_docs = [
            LCDocument(page_content=d["page_content"], metadata=d["metadata"]) for d in documents
        ]
        chunks = splitter.split_documents(lc_docs)
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
            results.append(
                {
                    "id": neighbor.id,
                    "distance": neighbor.distance,
                }
            )
        return results
