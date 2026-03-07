# ==============================================================================
# Cloud Scheduler — Automated pipeline triggers
# ==============================================================================
# Creates cron jobs that trigger Vertex AI Pipelines:
#   - Monitoring pipeline: every 24 hours (default)
#   - Master pipeline: weekly (optional)
#
# Triggers use the CICD service account to submit pipeline runs via
# Vertex AI Pipelines REST API.
# ==============================================================================

# --- Enable Cloud Scheduler API -----------------------------------------------

resource "google_project_service" "scheduler" {
  project = var.project_id
  service = "cloudscheduler.googleapis.com"

  disable_dependent_services = false
  disable_on_destroy         = false
}

# --- Variables ----------------------------------------------------------------

variable "monitoring_schedule" {
  description = "Cron schedule for monitoring pipeline (default: daily at 2am UTC)"
  type        = string
  default     = "0 2 * * *"
}

variable "master_pipeline_schedule" {
  description = "Cron schedule for master pipeline (default: weekly Sunday 3am UTC)"
  type        = string
  default     = "0 3 * * 0"
}

variable "monitoring_pipeline_enabled" {
  description = "Enable scheduled monitoring pipeline"
  type        = bool
  default     = true
}

variable "master_pipeline_enabled" {
  description = "Enable scheduled master pipeline"
  type        = bool
  default     = false
}

variable "pipeline_sa_email" {
  description = "Service account email used to run Vertex AI Pipelines"
  type        = string
  default     = ""
}

# --- Service Account for Scheduler -------------------------------------------

# Cloud Scheduler needs a SA to invoke the pipeline endpoint.
# Reuses the pipeline SA or agent SA.

# --- Monitoring Pipeline Scheduler Job ----------------------------------------

resource "google_cloud_scheduler_job" "monitoring_pipeline" {
  count = var.monitoring_pipeline_enabled ? 1 : 0

  name        = "llmops-monitoring-pipeline-${var.environment}"
  project     = var.project_id
  region      = var.region
  description = "Triggers the LLMOps monitoring pipeline on schedule"
  schedule    = var.monitoring_schedule
  time_zone   = "Etc/UTC"

  retry_config {
    retry_count          = 1
    min_backoff_duration = "30s"
    max_backoff_duration = "120s"
  }

  http_target {
    http_method = "POST"
    uri = (
      "https://${var.region}-aiplatform.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/pipelineJobs"
    )

    body = base64encode(jsonencode({
      displayName = "scheduled-monitoring-${var.environment}"
      templateUri = "gs://${var.project_id}-llmops-${var.environment}/pipelines/monitoring_pipeline.yaml"
      serviceAccount = var.pipeline_sa_email != "" ? var.pipeline_sa_email : var.agent_service_account_email
      runtimeConfig = {
        parameterValues = {
          project                = { stringValue = var.project_id }
          location               = { stringValue = var.region }
          monitoring_window_days = { integerValue = "7" }
        }
      }
    }))

    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = var.pipeline_sa_email != "" ? var.pipeline_sa_email : var.agent_service_account_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_project_service.scheduler]
}

# --- Master Pipeline Scheduler Job (Weekly) -----------------------------------

resource "google_cloud_scheduler_job" "master_pipeline" {
  count = var.master_pipeline_enabled ? 1 : 0

  name        = "llmops-master-pipeline-${var.environment}"
  project     = var.project_id
  region      = var.region
  description = "Triggers the full LLMOps master pipeline on schedule"
  schedule    = var.master_pipeline_schedule
  time_zone   = "Etc/UTC"

  retry_config {
    retry_count          = 0
    min_backoff_duration = "60s"
  }

  http_target {
    http_method = "POST"
    uri = (
      "https://${var.region}-aiplatform.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/pipelineJobs"
    )

    body = base64encode(jsonencode({
      displayName = "scheduled-master-${var.environment}"
      templateUri = "gs://${var.project_id}-llmops-${var.environment}/pipelines/master_pipeline.yaml"
      serviceAccount = var.pipeline_sa_email != "" ? var.pipeline_sa_email : var.agent_service_account_email
      runtimeConfig = {
        parameterValues = {
          project    = { stringValue = var.project_id }
          location   = { stringValue = var.region }
          gcs_bucket = { stringValue = "${var.project_id}-llmops-${var.environment}" }
        }
      }
    }))

    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = var.pipeline_sa_email != "" ? var.pipeline_sa_email : var.agent_service_account_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_project_service.scheduler]
}

# --- Outputs ------------------------------------------------------------------

output "monitoring_scheduler_name" {
  description = "Cloud Scheduler job name for monitoring pipeline"
  value       = var.monitoring_pipeline_enabled ? google_cloud_scheduler_job.monitoring_pipeline[0].name : "disabled"
}
