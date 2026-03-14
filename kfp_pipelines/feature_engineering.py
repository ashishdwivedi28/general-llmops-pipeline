"""KFP - Feature Engineering Pipeline.

Vertex AI Pipeline that runs CreateVectorDB -> IngestDocuments.
"""

from kfp import dsl


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=[
        "google-cloud-aiplatform",
        "google-cloud-storage",
        "langchain",
        "langchain-google-vertexai",
        "langchain-community",
        "loguru",
        "omegaconf",
        "pydantic",
        "pydantic-settings",
    ],
)
def create_vector_db(
    project: str,
    location: str,
    gcs_bucket: str,
    index_display_name: str,
    endpoint_display_name: str,
    embedding_model: str,
    embedding_dimensions: int,
) -> str:
    """Create Vertex AI Vector Search index + endpoint.

    Writes endpoint config to GCS (pipeline_outputs/vector_db_config.json)
    for the serving layer to auto-discover.
    """
    import json
    from datetime import datetime, timezone

    from google.cloud import aiplatform, storage

    aiplatform.init(project=project, location=location)

    # Check if index already exists
    existing = aiplatform.MatchingEngineIndex.list(filter=f'display_name="{index_display_name}"')
    if existing:
        index = existing[0]
    else:
        # Create index with proper algorithm config (required by Vertex AI API)
        from google.cloud.aiplatform.matching_engine import MatchingEngineIndexConfig

        algorithm_config = MatchingEngineIndexConfig.TreeAhConfig(
            leaf_node_embedding_count=500,
        )

        index = aiplatform.MatchingEngineIndex.create_tree_ah_index(
            display_name=index_display_name,
            dimensions=embedding_dimensions,
            algorithm_config=algorithm_config,
            approximate_neighbors_count=50,
            distance_measure_type="DOT_PRODUCT_DISTANCE",
            description="LLMOps vector index",
        )

    # Check if endpoint already exists
    existing_ep = aiplatform.MatchingEngineIndexEndpoint.list(
        filter=f'display_name="{endpoint_display_name}"'
    )
    if existing_ep:
        endpoint = existing_ep[0]
    else:
        endpoint = aiplatform.MatchingEngineIndexEndpoint.create(
            display_name=endpoint_display_name,
            public_endpoint_enabled=True,
        )
        endpoint.deploy_index(
            index=index,
            deployed_index_id=index_display_name.replace("-", "_"),
        )

    # Write config to GCS for serving layer auto-discovery
    deployed_index_id = index_display_name.replace("-", "_")
    config = {
        "index_resource_name": index.resource_name,
        "endpoint_resource_name": endpoint.resource_name,
        "deployed_index_id": deployed_index_id,
        "embedding_model": embedding_model,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if gcs_bucket:
        try:
            gcs_client = storage.Client(project=project)
            bucket_obj = gcs_client.bucket(gcs_bucket)
            blob = bucket_obj.blob("pipeline_outputs/vector_db_config.json")
            blob.upload_from_string(json.dumps(config, indent=2))
            print(
                f"Vector DB config written to gs://{gcs_bucket}/pipeline_outputs/vector_db_config.json"
            )
        except Exception as e:
            print(f"Warning: Could not write vector DB config to GCS: {e}")

    return json.dumps(config)


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=[
        "google-cloud-aiplatform",
        "google-cloud-storage",
        "langchain",
        "langchain-google-vertexai",
        "langchain-community",
        "loguru",
    ],
)
def ingest_documents(
    project: str,
    location: str,
    gcs_bucket: str,
    documents_gcs_path: str,
    embedding_model: str,
    embedding_dimensions: int,
    chunk_size: int,
    chunk_overlap: int,
    index_resource_name: str,
) -> str:
    """Load documents -> chunk -> embed -> upload to Vector Search."""
    import json
    import tempfile

    from google.cloud import aiplatform, storage
    from langchain_community.document_loaders import DirectoryLoader
    from langchain_google_vertexai import VertexAIEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    aiplatform.init(project=project, location=location)

    # Download documents from GCS to local temp dir
    client = storage.Client(project=project)
    bucket = client.bucket(gcs_bucket)
    blobs = list(bucket.list_blobs(prefix=documents_gcs_path))

    tmp_dir = tempfile.mkdtemp()
    for blob in blobs:
        if not blob.name.endswith("/"):
            local_path = f"{tmp_dir}/{blob.name.split('/')[-1]}"
            blob.download_to_filename(local_path)

    # Load and chunk
    loader = DirectoryLoader(tmp_dir, show_progress=True)
    docs = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = splitter.split_documents(docs)

    # Embed
    embedder = VertexAIEmbeddings(
        model_name=embedding_model,
        project=project,
        location=location,
    )
    texts = [c.page_content for c in chunks]
    embeddings = embedder.embed_documents(texts)

    # Write JSONL to GCS
    jsonl_path = f"embeddings/vectors_{len(chunks)}.jsonl"
    tmp_jsonl = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for i, (text, emb) in enumerate(zip(texts, embeddings)):
        record = {"id": str(i), "embedding": emb, "restricts": [], "text": text}
        tmp_jsonl.write(json.dumps(record) + "\n")
    tmp_jsonl.close()

    blob = bucket.blob(jsonl_path)
    blob.upload_from_filename(tmp_jsonl.name)

    return f"Ingested {len(chunks)} chunks from {len(docs)} documents"


@dsl.pipeline(
    name="feature-engineering-pipeline",
    description="Create vector DB and ingest documents for RAG.",
)
def feature_engineering_pipeline(
    project: str,
    location: str = "us-central1",
    gcs_bucket: str = "",
    documents_gcs_path: str = "documents/",
    index_display_name: str = "llmops-vector-index",
    endpoint_display_name: str = "llmops-vector-endpoint",
    embedding_model: str = "text-embedding-004",
    embedding_dimensions: int = 768,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
):
    """Feature Engineering Pipeline - runs on Vertex AI Pipelines."""
    # Step 1: Create Vector DB
    db_task = create_vector_db(
        project=project,
        location=location,
        gcs_bucket=gcs_bucket,
        index_display_name=index_display_name,
        endpoint_display_name=endpoint_display_name,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )

    # Step 2: Ingest Documents (depends on step 1)
    ingest_task = ingest_documents(
        project=project,
        location=location,
        gcs_bucket=gcs_bucket,
        documents_gcs_path=documents_gcs_path,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        index_resource_name=db_task.output,
    )
    ingest_task.after(db_task)
