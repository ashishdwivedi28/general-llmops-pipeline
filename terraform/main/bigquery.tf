# ==============================================================================
# BigQuery — Interaction, Feedback, Evaluation, and Cost tables
# ==============================================================================
# These tables power the monitoring pipeline, dashboard, and cost analytics.
#
# Tables:
#   interactions — every chat Q&A pair (consumed by monitoring pipeline)
#   feedback     — user thumbs-up/down (consumed by fine-tuning pipeline)
#   evaluations  — pipeline eval scores (offline + online)
#   costs        — per-request LLM cost tracking
# ==============================================================================

resource "google_bigquery_dataset" "llmops" {
  dataset_id    = "llmops"
  friendly_name = "LLMOps Pipeline Data"
  description   = "Interaction logs, feedback, evaluations, and cost tracking for LLMOps."
  location      = var.region
  project       = var.project_id

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  # NOTE: default_table_expiration_ms is intentionally omitted.
  # Individual tables set deletion_protection = false for dev flexibility.
  # For production, set per-table expiration on the Terraform table resources.

  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }
  access {
    role          = "WRITER"
    user_by_email = var.agent_service_account_email
  }
  # SECURITY NOTE: projectReaders is intentionally removed.
  # This dataset contains user interaction logs, feedback, and cost data.
  # Grant explicit access only to users/SAs that need it.
  # To grant analyst access: add a google_bigquery_dataset_iam_member resource.
}

# --- interactions table -------------------------------------------------------

resource "google_bigquery_table" "interactions" {
  dataset_id          = google_bigquery_dataset.llmops.dataset_id
  table_id            = "interactions"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  labels = {
    environment = var.environment
  }

  schema = <<EOF
[
  {"name": "timestamp",       "type": "TIMESTAMP", "mode": "REQUIRED", "description": "UTC timestamp of the interaction"},
  {"name": "session_id",      "type": "STRING",    "mode": "REQUIRED", "description": "User session ID"},
  {"name": "user_query",      "type": "STRING",    "mode": "REQUIRED", "description": "User's input query"},
  {"name": "agent_response",  "type": "STRING",    "mode": "NULLABLE", "description": "Agent's response (truncated to 2000 chars)"},
  {"name": "latency_ms",      "type": "FLOAT",     "mode": "NULLABLE", "description": "Response latency in milliseconds"},
  {"name": "tool_calls",      "type": "STRING",    "mode": "NULLABLE", "description": "JSON array of tool names invoked"},
  {"name": "metadata",        "type": "STRING",    "mode": "NULLABLE", "description": "JSON metadata blob"},
  {"name": "model",           "type": "STRING",    "mode": "NULLABLE", "description": "LLM model used for this request"},
  {"name": "prompt_version",  "type": "STRING",    "mode": "NULLABLE", "description": "Prompt version (e.g. v1, v3)"},
  {"name": "input_tokens",    "type": "INTEGER",   "mode": "NULLABLE", "description": "Input token count"},
  {"name": "output_tokens",   "type": "INTEGER",   "mode": "NULLABLE", "description": "Output token count"},
  {"name": "cost_usd",        "type": "FLOAT",     "mode": "NULLABLE", "description": "Estimated cost in USD"}
]
EOF
}

# --- feedback table -----------------------------------------------------------

resource "google_bigquery_table" "feedback" {
  dataset_id          = google_bigquery_dataset.llmops.dataset_id
  table_id            = "feedback"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  labels = {
    environment = var.environment
  }

  schema = <<EOF
[
  {"name": "timestamp",       "type": "TIMESTAMP", "mode": "REQUIRED", "description": "UTC timestamp of the feedback"},
  {"name": "session_id",      "type": "STRING",    "mode": "REQUIRED", "description": "Session ID"},
  {"name": "interaction_id",  "type": "STRING",    "mode": "NULLABLE", "description": "ID of the interaction being rated"},
  {"name": "rating",          "type": "INTEGER",   "mode": "REQUIRED", "description": "Rating (1=bad, 5=great)"},
  {"name": "comment",         "type": "STRING",    "mode": "NULLABLE", "description": "Optional text comment"},
  {"name": "model",           "type": "STRING",    "mode": "NULLABLE", "description": "Model that generated the rated response"},
  {"name": "prompt_version",  "type": "STRING",    "mode": "NULLABLE", "description": "Prompt version used"}
]
EOF
}

# --- evaluations table --------------------------------------------------------

resource "google_bigquery_table" "evaluations" {
  dataset_id          = google_bigquery_dataset.llmops.dataset_id
  table_id            = "evaluations"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  labels = {
    environment = var.environment
  }

  schema = <<EOF
[
  {"name": "timestamp",        "type": "TIMESTAMP", "mode": "REQUIRED", "description": "When evaluation was run"},
  {"name": "pipeline_run_id",  "type": "STRING",    "mode": "NULLABLE", "description": "Vertex AI Pipeline run ID"},
  {"name": "eval_type",        "type": "STRING",    "mode": "NULLABLE", "description": "offline or online"},
  {"name": "metric",           "type": "STRING",    "mode": "REQUIRED", "description": "Metric name (e.g. answer_relevance)"},
  {"name": "score",            "type": "FLOAT",     "mode": "REQUIRED", "description": "Metric score"},
  {"name": "model",            "type": "STRING",    "mode": "NULLABLE", "description": "Model evaluated"},
  {"name": "prompt_version",   "type": "STRING",    "mode": "NULLABLE", "description": "Prompt version evaluated"},
  {"name": "num_samples",      "type": "INTEGER",   "mode": "NULLABLE", "description": "Number of samples evaluated"},
  {"name": "quality_gate",     "type": "STRING",    "mode": "NULLABLE", "description": "PASS or BLOCKED"}
]
EOF
}

# --- costs table --------------------------------------------------------------

resource "google_bigquery_table" "costs" {
  dataset_id          = google_bigquery_dataset.llmops.dataset_id
  table_id            = "costs"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  labels = {
    environment = var.environment
  }

  schema = <<EOF
[
  {"name": "timestamp",      "type": "TIMESTAMP", "mode": "REQUIRED", "description": "UTC timestamp"},
  {"name": "app_id",         "type": "STRING",    "mode": "NULLABLE", "description": "Application identifier"},
  {"name": "user_id",        "type": "STRING",    "mode": "NULLABLE", "description": "User identifier"},
  {"name": "session_id",     "type": "STRING",    "mode": "NULLABLE", "description": "Session ID"},
  {"name": "model",          "type": "STRING",    "mode": "REQUIRED", "description": "LLM model name"},
  {"name": "provider",       "type": "STRING",    "mode": "NULLABLE", "description": "Model provider (vertex_ai, openai, etc.)"},
  {"name": "input_tokens",   "type": "INTEGER",   "mode": "NULLABLE", "description": "Input tokens"},
  {"name": "output_tokens",  "type": "INTEGER",   "mode": "NULLABLE", "description": "Output tokens"},
  {"name": "total_tokens",   "type": "INTEGER",   "mode": "NULLABLE", "description": "Total tokens"},
  {"name": "cost_usd",       "type": "FLOAT",     "mode": "NULLABLE", "description": "Cost in USD"},
  {"name": "latency_ms",     "type": "FLOAT",     "mode": "NULLABLE", "description": "Latency in ms"},
  {"name": "endpoint",       "type": "STRING",    "mode": "NULLABLE", "description": "API endpoint (e.g. /chat)"}
]
EOF
}

# --- Outputs ------------------------------------------------------------------

output "bigquery_dataset" {
  description = "BigQuery dataset ID for LLMOps"
  value       = google_bigquery_dataset.llmops.dataset_id
}
