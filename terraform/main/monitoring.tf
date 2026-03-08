# ==============================================================================
# Cloud Monitoring — Alerting policies and notification channels
# ==============================================================================
# Creates:
#   1. Email notification channel for degradation alerts
#   2. Cloud Run error-rate alert policy
#   3. Cloud Run latency alert policy
#   4. Custom log-based metric for degradation signals
# ==============================================================================

# --- Enable Monitoring API ----------------------------------------------------

resource "google_project_service" "monitoring" {
  project = var.project_id
  service = "monitoring.googleapis.com"

  disable_dependent_services = false
  disable_on_destroy         = false
}

# --- Variables ----------------------------------------------------------------

variable "alert_email" {
  description = "Email address for monitoring alerts"
  type        = string
  default     = ""
}

variable "monitoring_enabled" {
  description = "Whether to create monitoring alert policies"
  type        = bool
  default     = true
}

# --- Notification Channel (Email) --------------------------------------------

resource "google_monitoring_notification_channel" "email" {
  count        = var.monitoring_enabled && var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "LLMOps Alert Email"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.monitoring]
}

# --- Cloud Run Error Rate Alert -----------------------------------------------

resource "google_monitoring_alert_policy" "cloud_run_errors" {
  count        = var.monitoring_enabled ? 1 : 0
  project      = var.project_id
  display_name = "LLMOps Cloud Run Error Rate (${var.environment})"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run 5xx error rate > 5% of total requests"

    condition_threshold {
      # Numerator: count of 5xx responses
      filter = join(" AND ", [
        "resource.type=\"cloud_run_revision\"",
        "resource.labels.service_name=\"llmops-agent-${var.environment}\"",
        "metric.type=\"run.googleapis.com/request_count\"",
        "metric.labels.response_code_class=\"5xx\"",
      ])

      # Denominator: count of ALL responses (ratio = 5xx / total)
      denominator_filter = join(" AND ", [
        "resource.type=\"cloud_run_revision\"",
        "resource.labels.service_name=\"llmops-agent-${var.environment}\"",
        "metric.type=\"run.googleapis.com/request_count\"",
      ])

      denominator_aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }

      comparison      = "COMPARISON_GT"
      threshold_value = 0.05   # 5% error rate (ratio of 5xx to all requests)
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = var.alert_email != "" ? [
    google_monitoring_notification_channel.email[0].id
  ] : []

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    content   = "Cloud Run service llmops-agent-${var.environment} is returning >5% 5xx errors. Check Cloud Run logs and serving/server.py for issues."
    mime_type = "text/markdown"
  }

  depends_on = [google_project_service.monitoring]
}

# --- Cloud Run Latency Alert --------------------------------------------------

resource "google_monitoring_alert_policy" "cloud_run_latency" {
  count        = var.monitoring_enabled ? 1 : 0
  project      = var.project_id
  display_name = "LLMOps Cloud Run Latency (${var.environment})"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run p95 latency > 10s"

    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"cloud_run_revision\"",
        "resource.labels.service_name=\"llmops-agent-${var.environment}\"",
        "metric.type=\"run.googleapis.com/request_latencies\"",
      ])

      comparison      = "COMPARISON_GT"
      threshold_value = 10000  # 10 seconds in ms
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
      }
    }
  }

  notification_channels = var.alert_email != "" ? [
    google_monitoring_notification_channel.email[0].id
  ] : []

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    content   = "Cloud Run service llmops-agent-${var.environment} p95 latency exceeds 10s. Check model routing configuration and LLM provider status."
    mime_type = "text/markdown"
  }

  depends_on = [google_project_service.monitoring]
}

# --- Log-based Metric for Quality Degradation ---------------------------------

resource "google_logging_metric" "quality_degradation" {
  count   = var.monitoring_enabled ? 1 : 0
  name    = "llmops_quality_degradation_${var.environment}"
  project = var.project_id

  description = "Counts quality degradation events logged by the monitoring pipeline"

  filter = join(" AND ", [
    "resource.type=\"cloud_run_revision\"",
    "jsonPayload.message=~\"QUALITY DEGRADATION DETECTED\"",
  ])

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    labels {
      key         = "service"
      value_type  = "STRING"
      description = "Cloud Run service name"
    }
  }

  label_extractors = {
    "service" = "EXTRACT(resource.labels.service_name)"
  }
}

# --- Outputs ------------------------------------------------------------------

output "notification_channel_id" {
  description = "Notification channel ID"
  value       = var.monitoring_enabled && var.alert_email != "" ? google_monitoring_notification_channel.email[0].id : ""
}
