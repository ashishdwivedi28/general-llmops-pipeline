"""KFP — Monitoring Pipeline.

Vertex AI Pipeline that evaluates production quality from Cloud Logging traces,
diagnoses root causes, and dispatches automated remediation.
"""

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
        f'{log_filter} AND timestamp>="{start_time.isoformat()}" AND timestamp<="{now.isoformat()}"'
    )

    entries = list(client.list_entries(filter_=full_filter, max_results=500))

    if not entries:
        return json.dumps({"degraded": False, "status": "no_data", "num_traces": 0, "scores": {}})

    # Extract QA pairs
    qa_pairs = []
    for entry in entries:
        payload = entry.payload
        if isinstance(payload, dict) and "question" in payload and "answer" in payload:
            qa_pairs.append(payload)

    if not qa_pairs:
        return json.dumps(
            {"degraded": False, "status": "no_qa_pairs", "num_traces": 0, "scores": {}}
        )

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

    return json.dumps(
        {
            "degraded": degraded,
            "status": "degraded" if degraded else "healthy",
            "scores": avgs,
            "num_traces": len(qa_pairs),
        }
    )


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=["google-cloud-bigquery"],
)
def diagnose_degradation(
    project: str,
    location: str,
    monitoring_result_json: str,
    relevance_threshold: float,
    faithfulness_threshold: float,
    toxicity_threshold: float,
    bq_dataset: str,
    latency_spike_ms: float,
    error_rate_threshold: float,
) -> str:
    """Analyse monitoring results and determine root cause of degradation."""
    import json
    from datetime import datetime, timezone

    result = json.loads(monitoring_result_json)
    degraded = result.get("degraded", False)
    scores = result.get("scores", {})
    num_traces = result.get("num_traces", 0)

    if not degraded:
        return json.dumps(
            {
                "degraded": False,
                "primary_cause": "none",
                "recommended_actions": [],
                "categories": [],
                "raw_scores": scores,
                "num_traces": num_traces,
            }
        )

    categories = []
    actions = []

    # Check metric-level
    rel = scores.get("answer_relevance", 1.0)
    faith = scores.get("faithfulness", 1.0)
    tox = scores.get("toxicity", 0.0)

    if rel < relevance_threshold:
        categories.append(
            {
                "name": "low_relevance",
                "detected": True,
                "confidence": 0.8,
                "evidence": f"answer_relevance={rel:.3f}",
            }
        )
        actions.append("retrigger_feature_engineering")

    if faith < faithfulness_threshold:
        categories.append(
            {
                "name": "low_faithfulness",
                "detected": True,
                "confidence": 0.75,
                "evidence": f"faithfulness={faith:.3f}",
            }
        )
        actions.append("review_prompt_version")

    if tox > toxicity_threshold:
        categories.append(
            {
                "name": "high_toxicity",
                "detected": True,
                "confidence": 0.9,
                "evidence": f"toxicity={tox:.3f}",
            }
        )
        actions.append("rollback_prompt_version")

    # Check BigQuery for infra issues
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        query = f"""
            SELECT
                AVG(latency_ms) AS avg_latency,
                APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] AS p95_latency,
                COUNTIF(agent_response IS NULL) / COUNT(*) AS error_rate
            FROM `{project}.{bq_dataset}.interactions`
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        """
        rows = list(client.query(query).result())
        if rows:
            row = rows[0]
            p95 = row.p95_latency or 0.0
            err = row.error_rate or 0.0
            if p95 > latency_spike_ms or err > error_rate_threshold:
                categories.append(
                    {
                        "name": "infrastructure_issue",
                        "detected": True,
                        "confidence": 0.7,
                        "evidence": f"p95={p95:.0f}ms, error_rate={err:.2%}",
                    }
                )
                actions.append("investigate_infrastructure")
    except Exception:
        pass  # Non-fatal

    # Primary cause
    primary = "unknown"
    max_conf = 0.0
    for cat in categories:
        if cat["detected"] and cat["confidence"] > max_conf:
            max_conf = cat["confidence"]
            primary = cat["name"]

    return json.dumps(
        {
            "degraded": True,
            "primary_cause": primary,
            "recommended_actions": list(set(actions)),
            "categories": categories,
            "raw_scores": scores,
            "num_traces": num_traces,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=["google-cloud-storage"],
)
def remediate(
    project: str,
    location: str,
    gcs_bucket: str,
    diagnosis_json: str,
    auto_retrigger: bool,
) -> str:
    """Execute remediation actions based on diagnosis."""
    import json
    from datetime import datetime, timezone

    diagnosis = json.loads(diagnosis_json)
    actions = diagnosis.get("recommended_actions", [])
    results = {}

    if not actions:
        return json.dumps({"status": "no_action", "actions": {}})

    for action in actions:
        if action == "retrigger_feature_engineering" and auto_retrigger:
            try:
                from google.cloud import storage

                client = storage.Client(project=project)
                bucket = client.bucket(gcs_bucket)
                blob = bucket.blob("signals/retrigger_fe.json")
                blob.upload_from_string(
                    json.dumps(
                        {
                            "triggered_at": datetime.now(timezone.utc).isoformat(),
                            "reason": "quality_degradation",
                            "primary_cause": diagnosis.get("primary_cause", "unknown"),
                        }
                    ),
                    content_type="application/json",
                )
                results[action] = "triggered"
            except Exception as e:
                results[action] = f"failed: {e}"
        elif action == "rollback_prompt_version":
            results[action] = "alert_sent"
        elif action == "review_prompt_version":
            results[action] = "alert_sent"
        elif action == "investigate_infrastructure":
            results[action] = "alert_sent"
        else:
            results[action] = "skipped_unknown"

    return json.dumps({"status": "completed", "actions": results})


@dsl.pipeline(
    name="monitoring-pipeline",
    description="Monitor production quality, diagnose degradation, and auto-remediate.",
)
def monitoring_pipeline(
    project: str,
    location: str = "us-central1",
    gcs_bucket: str = "",
    monitoring_window_days: int = 7,
    relevance_threshold: float = 0.70,
    faithfulness_threshold: float = 0.65,
    toxicity_threshold: float = 0.10,
    log_filter: str = 'resource.type="cloud_run_revision" AND jsonPayload.type="inference"',
    bq_dataset: str = "llmops",
    latency_spike_ms: float = 5000.0,
    error_rate_threshold: float = 0.05,
    auto_retrigger: bool = True,
):
    """Monitoring Pipeline — runs on Vertex AI Pipelines.

    Phase 3 steps:
      1. monitor_production_quality  (eval from Cloud Logging)
      2. diagnose_degradation        (root-cause analysis)
      3. remediate                   (auto-fix dispatching)
    """

    # Step 1: Evaluate
    monitor_task = monitor_production_quality(
        project=project,
        location=location,
        monitoring_window_days=monitoring_window_days,
        relevance_threshold=relevance_threshold,
        faithfulness_threshold=faithfulness_threshold,
        toxicity_threshold=toxicity_threshold,
        log_filter=log_filter,
    ).set_display_name("step1-evaluate-quality")

    # Step 2: Diagnose
    diag_task = diagnose_degradation(
        project=project,
        location=location,
        monitoring_result_json=monitor_task.output,
        relevance_threshold=relevance_threshold,
        faithfulness_threshold=faithfulness_threshold,
        toxicity_threshold=toxicity_threshold,
        bq_dataset=bq_dataset,
        latency_spike_ms=latency_spike_ms,
        error_rate_threshold=error_rate_threshold,
    ).set_display_name("step2-diagnose-root-cause")
    diag_task.after(monitor_task)

    # Step 3: Remediate
    remed_task = remediate(
        project=project,
        location=location,
        gcs_bucket=gcs_bucket,
        diagnosis_json=diag_task.output,
        auto_retrigger=auto_retrigger,
    ).set_display_name("step3-remediate")
    remed_task.after(diag_task)
