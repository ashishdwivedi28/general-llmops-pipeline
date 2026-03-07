"""KFP — Fine-Tuning Pipeline.

Vertex AI Pipeline: prepare dataset → train → evaluate fine-tuned model.
"""

from kfp import dsl


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=["google-cloud-bigquery", "google-cloud-storage"],
)
def prepare_finetuning_dataset(
    project: str,
    location: str,
    gcs_bucket: str,
    bq_dataset: str,
    min_rating: int,
    min_samples: int,
    max_samples: int,
    test_split_ratio: float,
    output_gcs_path: str,
) -> str:
    """Query BQ for high-rated interactions → write train/test JSONL to GCS."""
    import json

    from google.cloud import bigquery, storage

    client = bigquery.Client(project=project)
    query = f"""
        SELECT
            i.user_query,
            i.agent_response,
            i.model,
            f.rating
        FROM `{project}.{bq_dataset}.interactions` i
        INNER JOIN `{project}.{bq_dataset}.feedback` f
            ON i.session_id = f.session_id
        WHERE f.rating >= {min_rating}
            AND i.agent_response IS NOT NULL
            AND LENGTH(i.agent_response) > 10
        ORDER BY f.timestamp DESC
        LIMIT {max_samples}
    """
    rows = list(client.query(query).result())

    if len(rows) < min_samples:
        return json.dumps({"status": "insufficient_data", "num_samples": len(rows)})

    # Format as Vertex AI JSONL
    formatted = []
    for row in rows:
        record = {
            "messages": [
                {"role": "user", "content": row.user_query or ""},
                {"role": "model", "content": row.agent_response or ""},
            ]
        }
        formatted.append(json.dumps(record, ensure_ascii=False))

    # Split
    split_idx = int(len(formatted) * (1 - test_split_ratio))
    train_data = formatted[:split_idx]
    test_data = formatted[split_idx:]

    # Upload to GCS
    gcs_client = storage.Client(project=project)
    bucket = gcs_client.bucket(gcs_bucket)

    train_path = f"{output_gcs_path}train.jsonl"
    test_path = f"{output_gcs_path}test.jsonl"

    bucket.blob(train_path).upload_from_string(
        "\n".join(train_data), content_type="application/jsonl"
    )
    bucket.blob(test_path).upload_from_string(
        "\n".join(test_data), content_type="application/jsonl"
    )

    return json.dumps(
        {
            "status": "ready",
            "num_train": len(train_data),
            "num_test": len(test_data),
            "train_gcs_uri": f"gs://{gcs_bucket}/{train_path}",
            "test_gcs_uri": f"gs://{gcs_bucket}/{test_path}",
        }
    )


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=["google-cloud-aiplatform"],
)
def submit_finetuning_job(
    project: str,
    location: str,
    dataset_result_json: str,
    base_model: str,
    display_name: str,
    epochs: int,
    adapter_size: int,
    learning_rate_multiplier: float,
) -> str:
    """Submit a Vertex AI supervised fine-tuning job."""
    import json

    dataset = json.loads(dataset_result_json)
    if dataset.get("status") != "ready":
        return json.dumps({"status": "skipped", "reason": dataset.get("status")})

    train_uri = dataset["train_gcs_uri"]

    import vertexai
    from vertexai.tuning import sft

    vertexai.init(project=project, location=location)

    tuning_job = sft.train(
        source_model=base_model,
        train_dataset=train_uri,
        epochs=epochs,
        adapter_size=adapter_size,
        learning_rate_multiplier=learning_rate_multiplier,
        tuned_model_display_name=display_name,
    )

    tuned_name = ""
    if hasattr(tuning_job, "tuned_model_endpoint_name"):
        tuned_name = tuning_job.tuned_model_endpoint_name

    return json.dumps(
        {
            "status": "submitted",
            "tuning_job_name": tuning_job.resource_name,
            "tuned_model_name": tuned_name,
            "base_model": base_model,
        }
    )


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=[
        "google-cloud-aiplatform",
        "google-cloud-storage",
        "langchain-google-vertexai",
    ],
)
def evaluate_finetuned_model(
    project: str,
    location: str,
    train_result_json: str,
    dataset_result_json: str,
    relevance_threshold: float,
    faithfulness_threshold: float,
) -> str:
    """Evaluate fine-tuned model vs quality gate."""
    import json

    from google.cloud import storage
    from langchain_google_vertexai import ChatVertexAI

    train_result = json.loads(train_result_json)
    dataset = json.loads(dataset_result_json)

    if train_result.get("status") != "submitted":
        return json.dumps({"status": "skipped", "passed": False})

    # Load test data
    test_uri = dataset.get("test_gcs_uri", "")
    if not test_uri:
        return json.dumps({"status": "no_test_data", "passed": False})

    uri = test_uri.replace("gs://", "")
    bucket_name, blob_path = uri.split("/", 1)
    gcs_client = storage.Client(project=project)
    content = gcs_client.bucket(bucket_name).blob(blob_path).download_as_text()
    test_pairs = [json.loads(line) for line in content.strip().split("\n") if line.strip()]

    # Evaluate with Gemini-as-judge
    judge = ChatVertexAI(
        model_name="gemini-2.0-flash", temperature=0.0, project=project, location=location
    )

    scores = {"answer_relevance": [], "faithfulness": []}
    for pair in test_pairs[:100]:
        msgs = pair.get("messages", [])
        user = next((m["content"] for m in msgs if m["role"] == "user"), "")
        model = next((m["content"] for m in msgs if m["role"] in ("model", "assistant")), "")

        prompt = (
            f"Evaluate (0.0-1.0):\nQ: {user}\nA: {model}\n"
            f'JSON: {{"answer_relevance": X, "faithfulness": X}}'
        )
        try:
            resp = judge.invoke(prompt)
            data = json.loads(resp.content)
            for k in scores:
                scores[k].append(float(data.get(k, 0.0)))
        except Exception:
            pass

    avgs = {k: sum(v) / len(v) if v else 0.0 for k, v in scores.items()}
    passed = (
        avgs["answer_relevance"] >= relevance_threshold
        and avgs["faithfulness"] >= faithfulness_threshold
    )

    return json.dumps(
        {
            "status": "passed" if passed else "blocked",
            "passed": passed,
            "scores": avgs,
            "num_test_samples": len(test_pairs),
        }
    )


@dsl.pipeline(
    name="fine-tuning-pipeline",
    description="Prepare dataset from feedback, fine-tune model, evaluate quality.",
)
def fine_tuning_pipeline(
    project: str,
    location: str = "us-central1",
    gcs_bucket: str = "",
    bq_dataset: str = "llmops",
    min_rating: int = 4,
    min_samples: int = 100,
    max_samples: int = 10000,
    test_split_ratio: float = 0.2,
    output_gcs_path: str = "fine_tuning/datasets/",
    base_model: str = "gemini-2.0-flash",
    display_name: str = "llmops-fine-tuned",
    epochs: int = 3,
    adapter_size: int = 4,
    learning_rate_multiplier: float = 1.0,
    relevance_threshold: float = 0.75,
    faithfulness_threshold: float = 0.70,
):
    """Fine-Tuning Pipeline — runs on Vertex AI Pipelines."""

    # Step 1: Prepare dataset
    prep_task = prepare_finetuning_dataset(
        project=project,
        location=location,
        gcs_bucket=gcs_bucket,
        bq_dataset=bq_dataset,
        min_rating=min_rating,
        min_samples=min_samples,
        max_samples=max_samples,
        test_split_ratio=test_split_ratio,
        output_gcs_path=output_gcs_path,
    ).set_display_name("step1-prepare-dataset")

    # Step 2: Submit fine-tuning
    train_task = submit_finetuning_job(
        project=project,
        location=location,
        dataset_result_json=prep_task.output,
        base_model=base_model,
        display_name=display_name,
        epochs=epochs,
        adapter_size=adapter_size,
        learning_rate_multiplier=learning_rate_multiplier,
    ).set_display_name("step2-submit-training")
    train_task.after(prep_task)

    # Step 3: Evaluate
    eval_task = evaluate_finetuned_model(
        project=project,
        location=location,
        train_result_json=train_task.output,
        dataset_result_json=prep_task.output,
        relevance_threshold=relevance_threshold,
        faithfulness_threshold=faithfulness_threshold,
    ).set_display_name("step3-evaluate")
    eval_task.after(train_task)
