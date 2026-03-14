"""LLMOps Admin Dashboard — Streamlit application.

Provides a visual interface for:
  - Pipeline status and manifest viewer
  - Cost analytics and tracking
  - Monitoring scores and degradation alerts
  - Model routing configuration
  - Prompt version management
  - Feedback analytics

Launch:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

# Page config
st.set_page_config(
    page_title="LLMOps Dashboard",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SERVING_URL = os.getenv("SERVING_URL", "http://localhost:8080")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
BQ_DATASET = os.getenv("BQ_DATASET", "llmops")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("LLMOps Admin")
page = st.sidebar.radio(
    "Navigate",
    [
        "Overview",
        "Pipeline Manifest",
        "Cost Analytics",
        "Monitoring Scores",
        "Model Configuration",
        "Feedback Analytics",
    ],
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def fetch_json(path: str) -> dict | None:
    """Fetch JSON from the serving endpoint."""
    try:
        import requests

        resp = requests.get(f"{SERVING_URL}{path}", timeout=10)
        return resp.json() if resp.ok else None
    except Exception:
        return None


def query_bigquery(query: str) -> list[dict]:
    """Run a BigQuery query and return results."""
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=GCP_PROJECT_ID)
        rows = list(client.query(query).result())
        return [dict(row) for row in rows]
    except Exception as exc:
        st.error(f"BigQuery query failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
if page == "Overview":
    st.title("LLMOps Pipeline Overview")

    col1, col2, col3 = st.columns(3)

    # Health check
    health = fetch_json("/health")
    with col1:
        if health and health.get("status") == "healthy":
            st.metric("Service Health", "Healthy", delta="OK")
        else:
            st.metric("Service Health", "Unhealthy", delta="ERROR")

    # Readiness
    ready = fetch_json("/ready")
    with col2:
        if ready and ready.get("status") == "ready":
            st.metric("Readiness", "Ready", delta="OK")
        else:
            st.metric("Readiness", "Not Ready", delta="INIT")

    # Costs
    costs = fetch_json("/costs")
    with col3:
        if costs:
            st.metric("Total Cost (USD)", f"${costs.get('total_cost_usd', 0):.4f}")
        else:
            st.metric("Total Cost", "N/A")

    st.divider()
    st.subheader("Quick Links")
    st.markdown(f"""
    - **Serving URL:** `{SERVING_URL}`
    - **GCP Project:** `{GCP_PROJECT_ID}`
    - **BQ Dataset:** `{BQ_DATASET}`
    """)

elif page == "Pipeline Manifest":
    st.title("Pipeline Artifact Manifest")

    manifest = fetch_json("/manifest")
    if manifest and "manifest" in manifest:
        data = manifest["manifest"]
        st.json(data)

        # Section breakdown
        sections = [
            "feature_engineering",
            "deployment",
            "monitoring",
            "fine_tuning",
            "remediation",
        ]
        for section in sections:
            if section in data:
                with st.expander(f"Section: {section}", expanded=False):
                    st.json(data[section])
    else:
        st.warning("Could not fetch manifest. Is the serving layer running?")

elif page == "Cost Analytics":
    st.title("Cost Analytics")

    costs = fetch_json("/costs")
    if costs:
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Cost (USD)", f"${costs.get('total_cost_usd', 0):.4f}")
            st.metric("Total Requests", costs.get("total_requests", 0))
            st.metric("Total Tokens", costs.get("total_tokens", 0))

        with col2:
            by_model = costs.get("by_model", {})
            if by_model:
                st.subheader("Cost by Model")
                for model, model_cost in by_model.items():
                    st.write(f"**{model}:** ${model_cost:.4f}")

    # BQ cost trends
    if GCP_PROJECT_ID:
        st.subheader("Cost Trend (Last 7 Days)")
        rows = query_bigquery(f"""
            SELECT
                DATE(timestamp) as date,
                SUM(cost_usd) as daily_cost,
                SUM(total_tokens) as daily_tokens,
                COUNT(*) as requests
            FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.costs`
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
            GROUP BY date
            ORDER BY date
        """)
        if rows:
            import pandas as pd

            df = pd.DataFrame(rows)
            st.line_chart(df.set_index("date")["daily_cost"])

elif page == "Monitoring Scores":
    st.title("Monitoring Scores")

    manifest = fetch_json("/manifest")
    if manifest and "manifest" in manifest:
        monitoring = manifest["manifest"].get("monitoring", {})
        if monitoring:
            scores = monitoring.get("monitoring_scores", {})
            degraded = monitoring.get("degraded", False)

            if degraded:
                st.error("QUALITY DEGRADATION DETECTED")
            else:
                st.success("Quality is healthy")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Answer Relevance", f"{scores.get('answer_relevance', 0):.3f}")
            with col2:
                st.metric("Faithfulness", f"{scores.get('faithfulness', 0):.3f}")
            with col3:
                st.metric("Toxicity", f"{scores.get('toxicity', 0):.3f}")

            st.metric("Traces Evaluated", monitoring.get("num_traces_evaluated", 0))

        # Show remediation if available
        remediation = manifest["manifest"].get("remediation", {})
        if remediation:
            with st.expander("Last Remediation", expanded=False):
                st.json(remediation)
    else:
        st.warning("No monitoring data available")

    # BQ evaluation history
    if GCP_PROJECT_ID:
        st.subheader("Evaluation History")
        rows = query_bigquery(f"""
            SELECT timestamp, metric, score, model, quality_gate
            FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.evaluations`
            ORDER BY timestamp DESC
            LIMIT 50
        """)
        if rows:
            import pandas as pd

            df = pd.DataFrame(rows)
            st.dataframe(df)

elif page == "Model Configuration":
    st.title("Model Configuration")

    st.subheader("Current Models Config")
    try:
        import yaml

        config_path = "confs/models.yaml"
        if os.path.exists(config_path):
            with open(config_path) as f:
                models_config = yaml.safe_load(f)
            st.json(models_config)
        else:
            st.warning(f"Models config not found at {config_path}")
    except Exception as exc:
        st.error(f"Failed to load models config: {exc}")

    st.subheader("Active Model Routing")
    st.info(
        "Model routing is managed via `confs/models.yaml`. "
        "Change the config and redeploy to update routing."
    )

elif page == "Feedback Analytics":
    st.title("Feedback Analytics")

    if GCP_PROJECT_ID:
        # Rating distribution
        st.subheader("Rating Distribution")
        rows = query_bigquery(f"""
            SELECT rating, COUNT(*) as count
            FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.feedback`
            GROUP BY rating
            ORDER BY rating
        """)
        if rows:
            import pandas as pd

            df = pd.DataFrame(rows)
            st.bar_chart(df.set_index("rating")["count"])

        # Recent feedback
        st.subheader("Recent Feedback")
        rows = query_bigquery(f"""
            SELECT timestamp, session_id, rating, comment, model
            FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.feedback`
            ORDER BY timestamp DESC
            LIMIT 20
        """)
        if rows:
            import pandas as pd

            df = pd.DataFrame(rows)
            st.dataframe(df)
    else:
        st.warning("Set GCP_PROJECT_ID to enable BigQuery analytics")

# Footer
st.sidebar.divider()
st.sidebar.caption(f"Dashboard v1.0 | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
