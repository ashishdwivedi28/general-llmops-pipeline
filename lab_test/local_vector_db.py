"""Lab-compatible local vector DB using FAISS.

Replaces Vertex AI Vector Search (Matching Engine) which requires billing.
Uses FAISS for in-memory similarity search + GCS to persist vectors.

This is a DROP-IN replacement — same interface as VertexVectorSearch.
"""

from __future__ import annotations

import logging
import os
import pickle
import tempfile
import typing as T

logger = logging.getLogger(__name__)

try:
    import faiss

    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("FAISS not installed. Run: pip install faiss-cpu numpy")


class LocalFaissVectorDB:
    """Local FAISS vector store — GCP lab replacement for Vertex AI Vector Search.

    Workflow:
      1. Embed documents with Vertex AI Embeddings (text-embedding-004) — FREE in lab
      2. Store vectors in a local FAISS index
      3. Optionally persist index to GCS for re-use
      4. Query with nearest-neighbor search

    Drop-in replacement for VertexVectorSearch in lab environments.
    """

    def __init__(
        self,
        project: str,
        location: str = "us-central1",
        embedding_model: str = "text-embedding-004",
        embedding_dimensions: int = 768,
    ):
        if not FAISS_AVAILABLE:
            raise ImportError("Install lab deps: pip install faiss-cpu numpy")

        self.project = project
        self.location = location
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions

        # FAISS index (inner product ≈ cosine similarity for normalised vectors)
        self.index: T.Any = faiss.IndexFlatIP(embedding_dimensions)
        self.chunks: list[str] = []  # text of each chunk
        self.metadata: list[dict] = []  # source metadata per chunk

        # Lazy init embeddings
        self._embeddings = None

    def _get_embeddings(self):
        """Lazy-load Vertex AI Embeddings (free in lab)."""
        if self._embeddings is None:
            from langchain_google_vertexai import VertexAIEmbeddings

            self._embeddings = VertexAIEmbeddings(
                model_name=self.embedding_model,
                project=self.project,
                location=self.location,
            )
        return self._embeddings

    def ingest_documents(
        self,
        documents_path: str,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> dict:
        """Load documents → chunk → embed → insert into FAISS index.

        Args:
            documents_path: Local folder with PDF/TXT/DOCX files.
            chunk_size: Characters per chunk.
            chunk_overlap: Overlap between chunks.

        Returns:
            Stats dict.
        """
        import numpy as np
        from langchain_community.document_loaders import DirectoryLoader
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        logger.info("Loading documents from: %s", documents_path)
        loader = DirectoryLoader(documents_path, show_progress=True)
        docs = loader.load()
        logger.info("Loaded %d documents", len(docs))

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        chunks = splitter.split_documents(docs)
        logger.info("Split into %d chunks", len(chunks))

        if not chunks:
            logger.warning("No chunks created — is documents_path empty?")
            return {"num_documents": 0, "num_chunks": 0}

        # Embed (calls Vertex AI Embeddings API — free quota in lab)
        texts = [c.page_content for c in chunks]
        logger.info("Embedding %d chunks via %s...", len(texts), self.embedding_model)

        embedder = self._get_embeddings()
        batch_size = 5  # Small batches for lab quota
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_embs = embedder.embed_documents(batch)
            all_embeddings.extend(batch_embs)
            logger.info("Embedded %d / %d chunks", min(i + batch_size, len(texts)), len(texts))

        # Normalise for cosine similarity
        vectors = np.array(all_embeddings, dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / (norms + 1e-9)

        # Insert into FAISS
        self.index.add(vectors)
        self.chunks.extend(texts)
        self.metadata.extend([c.metadata for c in chunks])

        logger.info(
            "Inserted %d vectors into FAISS index (total: %d)",
            len(texts),
            self.index.ntotal,
        )
        return {"num_documents": len(docs), "num_chunks": len(chunks)}

    def query(self, query_text: str, top_k: int = 5) -> list[dict]:
        """Embed the query and return the top-k most similar chunks.

        Args:
            query_text: The user's search query.
            top_k: Number of results.

        Returns:
            List of dicts with 'text', 'score', 'metadata'.
        """
        import numpy as np

        if self.index.ntotal == 0:
            logger.warning("FAISS index is empty — ingest documents first")
            return []

        embedder = self._get_embeddings()
        q_vec = np.array(embedder.embed_query(query_text), dtype=np.float32)
        q_vec = q_vec / (np.linalg.norm(q_vec) + 1e-9)
        q_vec = q_vec.reshape(1, -1)

        scores, indices = self.index.search(q_vec, min(top_k, self.index.ntotal))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx != -1:
                results.append(
                    {
                        "text": self.chunks[idx],
                        "score": float(score),
                        "metadata": self.metadata[idx],
                    }
                )
        return results

    def save_to_gcs(self, gcs_bucket: str, prefix: str = "lab-faiss/") -> str:
        """Save FAISS index + chunk data to GCS for reuse across lab sessions."""
        from google.cloud import storage

        # Serialise FAISS index
        tmp_idx = tempfile.NamedTemporaryFile(suffix=".faiss", delete=False)
        faiss.write_index(self.index, tmp_idx.name)

        # Serialise chunks + metadata
        tmp_meta = tempfile.NamedTemporaryFile(suffix=".pkl", delete=False)
        pickle.dump({"chunks": self.chunks, "metadata": self.metadata}, tmp_meta)
        tmp_meta.close()

        client = storage.Client(project=self.project)
        bucket = client.bucket(gcs_bucket)
        bucket.blob(f"{prefix}index.faiss").upload_from_filename(tmp_idx.name)
        bucket.blob(f"{prefix}meta.pkl").upload_from_filename(tmp_meta.name)

        logger.info("Saved FAISS index to gs://%s/%s", gcs_bucket, prefix)
        return f"gs://{gcs_bucket}/{prefix}"

    def load_from_gcs(self, gcs_bucket: str, prefix: str = "lab-faiss/") -> bool:
        """Load FAISS index + chunk data from GCS."""
        from google.cloud import storage

        client = storage.Client(project=self.project)
        bucket = client.bucket(gcs_bucket)

        tmp_idx = tempfile.NamedTemporaryFile(suffix=".faiss", delete=False)
        tmp_meta = tempfile.NamedTemporaryFile(suffix=".pkl", delete=False)

        try:
            bucket.blob(f"{prefix}index.faiss").download_to_filename(tmp_idx.name)
            bucket.blob(f"{prefix}meta.pkl").download_to_filename(tmp_meta.name)
        except Exception as e:
            logger.warning("Could not load from GCS: %s", e)
            return False

        self.index = faiss.read_index(tmp_idx.name)
        with open(tmp_meta.name, "rb") as f:
            data = pickle.load(f)
        self.chunks = data["chunks"]
        self.metadata = data["metadata"]

        logger.info("Loaded FAISS index from GCS: %d vectors", self.index.ntotal)
        return True

    def save_local(self, path: str = "/tmp/lab_faiss") -> None:
        """Save FAISS index locally (no GCS needed)."""
        os.makedirs(path, exist_ok=True)
        faiss.write_index(self.index, os.path.join(path, "index.faiss"))
        with open(os.path.join(path, "meta.pkl"), "wb") as f:
            pickle.dump({"chunks": self.chunks, "metadata": self.metadata}, f)
        logger.info("Saved FAISS index locally to %s", path)

    def load_local(self, path: str = "/tmp/lab_faiss") -> bool:
        """Load FAISS index from local disk."""
        idx_path = os.path.join(path, "index.faiss")
        meta_path = os.path.join(path, "meta.pkl")
        if not os.path.exists(idx_path):
            return False
        self.index = faiss.read_index(idx_path)
        with open(meta_path, "rb") as f:
            data = pickle.load(f)
        self.chunks = data["chunks"]
        self.metadata = data["metadata"]
        logger.info("Loaded local FAISS index: %d vectors", self.index.ntotal)
        return True
