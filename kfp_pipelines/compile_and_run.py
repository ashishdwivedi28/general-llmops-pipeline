"""Compile KFP pipelines to YAML and optionally submit to Vertex AI Pipelines.

Usage:
    python -m kfp_pipelines.compile_and_run --compile-only
    python -m kfp_pipelines.compile_and_run --project my-project --bucket my-bucket
"""

from __future__ import annotations

import argparse
import os

from google.cloud import aiplatform
from kfp import compiler

from kfp_pipelines.feature_engineering import feature_engineering_pipeline
from kfp_pipelines.deployment import deployment_pipeline
from kfp_pipelines.monitoring import monitoring_pipeline
from kfp_pipelines.master import master_pipeline

PIPELINE_REGISTRY = {
    "feature_engineering": feature_engineering_pipeline,
    "deployment": deployment_pipeline,
    "monitoring": monitoring_pipeline,
    "master": master_pipeline,
}


def compile_pipelines(output_dir: str = "compiled_pipelines") -> dict[str, str]:
    """Compile all pipelines to YAML."""
    os.makedirs(output_dir, exist_ok=True)
    paths = {}
    for name, pipeline_fn in PIPELINE_REGISTRY.items():
        output_path = os.path.join(output_dir, f"{name}_pipeline.yaml")
        compiler.Compiler().compile(
            pipeline_func=pipeline_fn,
            package_path=output_path,
        )
        paths[name] = output_path
        print(f"Compiled: {name} → {output_path}")
    return paths


def submit_pipeline(
    pipeline_yaml: str,
    project: str,
    location: str,
    gcs_bucket: str,
    display_name: str,
    parameter_values: dict | None = None,
) -> str:
    """Submit a compiled pipeline to Vertex AI Pipelines."""
    aiplatform.init(project=project, location=location)

    job = aiplatform.PipelineJob(
        display_name=display_name,
        template_path=pipeline_yaml,
        pipeline_root=f"gs://{gcs_bucket}/pipeline_root",
        parameter_values=parameter_values or {},
        enable_caching=True,
    )

    job.submit()
    print(f"Pipeline submitted: {display_name} → {job.resource_name}")
    return job.resource_name


def main():
    parser = argparse.ArgumentParser(description="Compile / submit LLMOps pipelines")
    parser.add_argument("--compile-only", action="store_true", help="Only compile, don't submit")
    parser.add_argument("--pipeline", choices=list(PIPELINE_REGISTRY.keys()), default="master")
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT_ID", ""))
    parser.add_argument("--location", default=os.getenv("GCP_LOCATION", "us-central1"))
    parser.add_argument("--bucket", default=os.getenv("GCS_BUCKET", ""))
    parser.add_argument("--output-dir", default="compiled_pipelines")
    args = parser.parse_args()

    # Always compile
    paths = compile_pipelines(args.output_dir)

    if args.compile_only:
        print("Compile-only mode — done.")
        return

    if not args.project or not args.bucket:
        print("ERROR: --project and --bucket required for submission")
        return

    submit_pipeline(
        pipeline_yaml=paths[args.pipeline],
        project=args.project,
        location=args.location,
        gcs_bucket=args.bucket,
        display_name=f"llmops-{args.pipeline}",
        parameter_values={
            "project": args.project,
            "location": args.location,
            "gcs_bucket": args.bucket,
        },
    )


if __name__ == "__main__":
    main()
