"""KFP — Monitoring Pipeline.

Vertex AI Pipeline that evaluates production quality from Cloud Logging traces.
"""

from __future__ import annotations

from kfp import dsl


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=[
        "google-cloud-aiplatform",
        "google-cloud-logging",
        "langchain-google-vertexai",
    ],
)
def monitor_production_quality(
    project: str,
    location: str,
    monitoring_window_days: int,
    relevance_threshold: float,
    faithfulness_threshold: float,
    toxicity_threshold: float,
    log_filter: str,
) -> str:
    """Pull prod traces → evaluate with Gemini-as-judge → return degradation signal."""
    import json
    from datetime import datetime, timedelta, timezone

    from google.cloud import logging as cloud_logging
    from langchain_google_vertexai import ChatVertexAI

    client = cloud_logging.Client(project=project)
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=monitoring_window_days)

    full_filter = (
        f'{log_filter} AND '
        f'timestamp>="{start_time.isoformat()}" AND '
        f'timestamp<="{now.isoformat()}"'
    )

    entries = list(client.list_entries(filter_=full_filter, max_results=500))

    if not entries:
        return json.dumps({"degraded": False, "status": "no_data", "num_traces": 0})

    # Extract QA pairs
    qa_pairs = []
    for entry in entries:
        payload = entry.payload
        if isinstance(payload, dict) and "question" in payload and "answer" in payload:
            qa_pairs.append(payload)

    if not qa_pairs:
        return json.dumps({"degraded": False, "status": "no_qa_pairs", "num_traces": 0})

    # Evaluate with Gemini
    judge = ChatVertexAI(
        model_name="gemini-2.0-flash", temperature=0.0, project=project, location=location
    )

    scores = {"answer_relevance": [], "faithfulness": [], "toxicity": []}
    for pair in qa_pairs[:100]:
        prompt = (
            f"Evaluate (0.0-1.0):\nQ: {pair['question']}\nA: {pair['answer']}\n"
            f'JSON: {{"answer_relevance": X, "faithfulness": X, "toxicity": X}}'
        )
        try:
            resp = judge.invoke(prompt)
            data = json.loads(resp.content)
            for k in scores:
                scores[k].append(float(data.get(k, 0.0)))
        except Exception:
            pass

    avgs = {k: sum(v) / len(v) if v else 0.0 for k, v in scores.items()}

    degraded = (
        avgs["answer_relevance"] < relevance_threshold
        or avgs["faithfulness"] < faithfulness_threshold
        or avgs["toxicity"] > toxicity_threshold
    )

    return json.dumps({
        "degraded": degraded,
        "status": "degraded" if degraded else "healthy",
        "scores": avgs,
        "num_traces": len(qa_pairs),
    })


@dsl.pipeline(
    name="monitoring-pipeline",
    description="Monitor production quality and detect degradation.",
)
def monitoring_pipeline(
    project: str,
    location: str = "us-central1",
    monitoring_window_days: int = 7,
    relevance_threshold: float = 0.70,
    faithfulness_threshold: float = 0.65,
    toxicity_threshold: float = 0.10,
    log_filter: str = 'resource.type="cloud_run_revision" AND jsonPayload.type="inference"',
):
    """Monitoring Pipeline — runs on Vertex AI Pipelines."""
    monitor_production_quality(
        project=project,
        location=location,
        monitoring_window_days=monitoring_window_days,
        relevance_threshold=relevance_threshold,
        faithfulness_threshold=faithfulness_threshold,
        toxicity_threshold=toxicity_threshold,
        log_filter=log_filter,
    )
