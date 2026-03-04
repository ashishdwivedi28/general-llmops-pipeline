"""KFP — Master Pipeline (Pipeline 0).

The top-level orchestrator that chains:
  1. Feature Engineering Pipeline
  2. Deployment Pipeline
  3. Monitoring Pipeline
  4. (Conditional) Re-trigger Feature Engineering if degradation detected

This is the entry point for fully automated LLMOps.
"""

from kfp import dsl

from kfp_pipelines.deployment import (
    evaluate_model,
    promote_model,
    register_model,
)
from kfp_pipelines.feature_engineering import (
    create_vector_db,
    ingest_documents,
)
from kfp_pipelines.monitoring import (
    monitor_production_quality,
)


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=[],
)
def parse_monitoring_result(result_json: str) -> bool:
    """Parse monitoring JSON and return True if degraded."""
    import json

    data = json.loads(result_json)
    return data.get("degraded", False)


@dsl.pipeline(
    name="master-llmops-pipeline",
    description=(
        "End-to-end LLMOps Master Pipeline — chains Feature Engineering → "
        "Deployment → Monitoring with conditional re-trigger on quality degradation."
    ),
)
def master_pipeline(
    project: str,
    location: str = "us-central1",
    gcs_bucket: str = "",
    # Feature Engineering
    documents_gcs_path: str = "documents/",
    index_display_name: str = "llmops-vector-index",
    endpoint_display_name: str = "llmops-vector-endpoint",
    embedding_model: str = "text-embedding-004",
    embedding_dimensions: int = 768,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    # Deployment
    model_display_name: str = "llmops-rag-chatbot",
    config_yaml_path: str = "confs/rag_chain_config.yaml",
    serving_image: str = "us-docker.pkg.dev/vertex-ai/prediction/tf2-cpu.2-12:latest",
    eval_dataset_gcs: str = "",
    relevance_threshold: float = 0.70,
    faithfulness_threshold: float = 0.65,
    toxicity_threshold: float = 0.10,
    # Monitoring
    monitoring_window_days: int = 7,
    log_filter: str = 'resource.type="cloud_run_revision" AND jsonPayload.type="inference"',
):
    """Master LLMOps Pipeline — fully automated end-to-end.

    Phases run sequentially:
      Phase 1 (Feature Engineering) → Phase 2 (Deployment) → Phase 3 (Monitoring)
      Phase 4 triggers only if Phase 3 detects quality degradation.
    """

    # ---- Phase 1: Feature Engineering ----
    db_task = create_vector_db(
        project=project,
        location=location,
        gcs_bucket=gcs_bucket,
        index_display_name=index_display_name,
        endpoint_display_name=endpoint_display_name,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    ).set_display_name("phase1-create-vector-db")

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
    ).set_display_name("phase1-ingest-documents")
    ingest_task.after(db_task)

    # ---- Phase 2: Deployment (waits for Phase 1 completion) ----
    reg_task = register_model(
        project=project,
        location=location,
        gcs_bucket=gcs_bucket,
        model_display_name=model_display_name,
        config_yaml_path=config_yaml_path,
        serving_image=serving_image,
    ).set_display_name("phase2-register-model")
    reg_task.after(ingest_task)  # ← CRITICAL: Phase 2 waits for Phase 1

    eval_task = evaluate_model(
        project=project,
        location=location,
        model_display_name=model_display_name,
        eval_dataset_gcs=eval_dataset_gcs,
        relevance_threshold=relevance_threshold,
        faithfulness_threshold=faithfulness_threshold,
        toxicity_threshold=toxicity_threshold,
    ).set_display_name("phase2-evaluate-model")
    eval_task.after(reg_task)

    promote_task = promote_model(
        project=project,
        location=location,
        model_display_name=model_display_name,
        eval_result=eval_task.output,
    ).set_display_name("phase2-promote-model")
    promote_task.after(eval_task)

    # ---- Phase 3: Monitoring (waits for Phase 2 completion) ----
    monitor_task = monitor_production_quality(
        project=project,
        location=location,
        monitoring_window_days=monitoring_window_days,
        relevance_threshold=relevance_threshold,
        faithfulness_threshold=faithfulness_threshold,
        toxicity_threshold=toxicity_threshold,
        log_filter=log_filter,
    ).set_display_name("phase3-monitor-quality")
    monitor_task.after(promote_task)  # ← CRITICAL: Phase 3 waits for Phase 2

    degraded_check = parse_monitoring_result(
        result_json=monitor_task.output,
    ).set_display_name("phase3-check-degradation")
    degraded_check.after(monitor_task)

    # ---- Phase 4: Conditional Re-trigger (only if degraded) ----
    with dsl.Condition(degraded_check.output == True, name="phase-4-retrigger"):  # noqa: E712
        retrigger_db = create_vector_db(
            project=project,
            location=location,
            gcs_bucket=gcs_bucket,
            index_display_name=index_display_name,
            endpoint_display_name=endpoint_display_name,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        ).set_display_name("retrigger-create-vector-db")

        retrigger_ingest = ingest_documents(
            project=project,
            location=location,
            gcs_bucket=gcs_bucket,
            documents_gcs_path=documents_gcs_path,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            index_resource_name=retrigger_db.output,
        ).set_display_name("retrigger-ingest-documents")
        retrigger_ingest.after(retrigger_db)
