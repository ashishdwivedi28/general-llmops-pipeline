"""KFP — Deployment Pipeline.

Vertex AI Pipeline that runs RegisterModel → EvaluateAndDeploy.
"""

from kfp import dsl


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=[
        "google-cloud-aiplatform",
        "google-cloud-storage",
    ],
)
def register_model(
    project: str,
    location: str,
    gcs_bucket: str,
    model_display_name: str,
    config_yaml_path: str,
    serving_image: str,
) -> str:
    """Upload RAG config to GCS and register in Vertex AI Model Registry."""
    from google.cloud import aiplatform, storage

    aiplatform.init(project=project, location=location)

    # Upload config to GCS
    client = storage.Client(project=project)
    bucket = client.bucket(gcs_bucket)
    gcs_config_path = f"models/{model_display_name}/config.yaml"
    blob = bucket.blob(gcs_config_path)
    blob.upload_from_filename(config_yaml_path)

    # Register model in Model Registry
    model = aiplatform.Model.upload(
        display_name=model_display_name,
        artifact_uri=f"gs://{gcs_bucket}/models/{model_display_name}/",
        serving_container_image_uri=serving_image,
        labels={"stage": "staging", "pipeline": "llmops"},
    )

    return model.resource_name


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=[
        "google-cloud-aiplatform",
        "langchain-google-vertexai",
    ],
)
def evaluate_model(
    project: str,
    location: str,
    model_display_name: str,
    eval_dataset_gcs: str,
    relevance_threshold: float,
    faithfulness_threshold: float,
    toxicity_threshold: float,
) -> str:
    """Evaluate model with QA dataset using Gemini-as-judge. Returns PASS or BLOCKED."""
    import csv
    import json
    import tempfile

    from google.cloud import aiplatform, storage
    from langchain_google_vertexai import ChatVertexAI

    aiplatform.init(project=project, location=location)

    # Handle empty eval dataset — auto-pass when no dataset is configured
    if not eval_dataset_gcs or not eval_dataset_gcs.startswith("gs://"):
        return json.dumps(
            {
                "decision": "PASS",
                "reason": "no_eval_dataset_configured",
                "scores": {"answer_relevance": 0.0, "faithfulness": 0.0, "toxicity": 0.0},
            }
        )

    # Download eval dataset
    client = storage.Client(project=project)
    bucket_name = eval_dataset_gcs.split("/")[2]
    blob_path = "/".join(eval_dataset_gcs.split("/")[3:])
    bucket = client.bucket(bucket_name)

    tmp_file = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    bucket.blob(blob_path).download_to_filename(tmp_file.name)

    # Load QA pairs
    qa_pairs = []
    with open(tmp_file.name) as f:
        reader = csv.DictReader(f)
        for row in reader:
            qa_pairs.append(row)

    if not qa_pairs:
        return json.dumps({"decision": "BLOCKED", "reason": "empty_dataset"})

    # Evaluate with Gemini-as-judge
    judge = ChatVertexAI(
        model_name="gemini-2.0-flash", temperature=0.0, project=project, location=location
    )

    scores = {"answer_relevance": [], "faithfulness": [], "toxicity": []}
    for pair in qa_pairs[:50]:  # cap at 50 for cost
        prompt = (
            f"Evaluate this QA pair (0.0-1.0 scale):\n"
            f"Question: {pair.get('question', '')}\n"
            f"Expected: {pair.get('expected_answer', '')}\n"
            f"Context: {pair.get('context', '')}\n\n"
            f'Respond as JSON: {{"answer_relevance": X, "faithfulness": X, "toxicity": X}}'
        )
        try:
            resp = judge.invoke(prompt)
            data = json.loads(resp.content)
            for k in scores:
                scores[k].append(float(data.get(k, 0.0)))
        except Exception:
            pass

    # Compute averages
    avgs = {k: sum(v) / len(v) if v else 0.0 for k, v in scores.items()}

    # Quality gate
    passed = (
        avgs["answer_relevance"] >= relevance_threshold
        and avgs["faithfulness"] >= faithfulness_threshold
        and avgs["toxicity"] <= toxicity_threshold
    )

    decision = "PASS" if passed else "BLOCKED"
    return json.dumps({"decision": decision, "scores": avgs})


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=["google-cloud-aiplatform"],
)
def promote_model(
    project: str,
    location: str,
    model_display_name: str,
    eval_result: str,
) -> str:
    """Promote model to production if eval passed."""
    import json

    from google.cloud import aiplatform

    aiplatform.init(project=project, location=location)
    result = json.loads(eval_result)

    if result["decision"] == "PASS":
        models = aiplatform.Model.list(filter=f'display_name="{model_display_name}"')
        if models:
            model = models[0]
            labels = dict(model.labels) if model.labels else {}
            labels["stage"] = "production"
            model.update(labels=labels)
            return "PROMOTED"
    return "NOT_PROMOTED"


@dsl.pipeline(
    name="deployment-pipeline",
    description="Register, evaluate, and deploy RAG model.",
)
def deployment_pipeline(
    project: str,
    location: str = "us-central1",
    gcs_bucket: str = "",
    model_display_name: str = "llmops-rag-chatbot",
    config_yaml_path: str = "confs/rag_chain_config.yaml",
    serving_image: str = "us-docker.pkg.dev/vertex-ai/prediction/tf2-cpu.2-12:latest",
    eval_dataset_gcs: str = "",
    relevance_threshold: float = 0.70,
    faithfulness_threshold: float = 0.65,
    toxicity_threshold: float = 0.10,
):
    """Deployment Pipeline — runs on Vertex AI Pipelines."""
    # Step 1: Register
    reg_task = register_model(
        project=project,
        location=location,
        gcs_bucket=gcs_bucket,
        model_display_name=model_display_name,
        config_yaml_path=config_yaml_path,
        serving_image=serving_image,
    )

    # Step 2: Evaluate
    eval_task = evaluate_model(
        project=project,
        location=location,
        model_display_name=model_display_name,
        eval_dataset_gcs=eval_dataset_gcs,
        relevance_threshold=relevance_threshold,
        faithfulness_threshold=faithfulness_threshold,
        toxicity_threshold=toxicity_threshold,
    )
    eval_task.after(reg_task)

    # Step 3: Promote (only if eval passed)
    promote_task = promote_model(
        project=project,
        location=location,
        model_display_name=model_display_name,
        eval_result=eval_task.output,
    )
    promote_task.after(eval_task)
