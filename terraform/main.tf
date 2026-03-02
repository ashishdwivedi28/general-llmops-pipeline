# ==============================================================================
# Terraform — LLMOps Pipeline Infrastructure on GCP
# ==============================================================================
# Provisions: Cloud Run, GCS, IAM, Artifact Registry, Vertex AI resources,
#             Workload Identity Federation for CI/CD
# ==============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    # Configure in terraform.tfvars or via CLI:
    # terraform init -backend-config="bucket=<tf-state-bucket>"
  }
}

# --- Variables ----------------------------------------------------------------

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "agent_image" {
  description = "Docker image for the agent (fully qualified)"
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "GitHub repository (owner/repo) for WIF"
  type        = string
  default     = ""
}

# --- Providers ----------------------------------------------------------------

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# --- Enable APIs --------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudrun.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "cloudtrace.googleapis.com",
    "bigquery.googleapis.com",
    "dlp.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# --- GCS Bucket ---------------------------------------------------------------

resource "google_storage_bucket" "llmops" {
  name          = "${var.project_id}-llmops-${var.environment}"
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    environment = var.environment
    managed_by  = "terraform"
    purpose     = "llmops-pipeline"
  }
}

# --- Artifact Registry --------------------------------------------------------

resource "google_artifact_registry_repository" "agent" {
  location      = var.region
  repository_id = "llmops-agent-${var.environment}"
  format        = "DOCKER"
  description   = "Docker images for LLMOps agent serving"

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }
}

# --- Service Account (Agent) -------------------------------------------------

resource "google_service_account" "agent" {
  account_id   = "llmops-agent-${var.environment}"
  display_name = "LLMOps Agent Service Account (${var.environment})"
}

# IAM roles for the agent service account
locals {
  agent_roles = [
    "roles/aiplatform.user",           # Vertex AI (models, endpoints, experiments)
    "roles/storage.objectAdmin",        # GCS (read/write)
    "roles/logging.logWriter",          # Cloud Logging
    "roles/cloudtrace.agent",           # Cloud Trace
    "roles/bigquery.dataEditor",        # BigQuery (logging)
    "roles/secretmanager.secretAccessor", # Secret Manager
    "roles/dlp.user",                   # Cloud DLP (PII detection)
  ]
}

resource "google_project_iam_member" "agent_roles" {
  for_each = toset(local.agent_roles)

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.agent.email}"
}

# --- Cloud Run (Agent Serving) ------------------------------------------------

resource "google_cloud_run_v2_service" "agent" {
  name     = "llmops-agent-${var.environment}"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.agent.email

    scaling {
      min_instance_count = var.environment == "prod" ? 1 : 0
      max_instance_count = var.environment == "prod" ? 10 : 3
    }

    containers {
      image = var.agent_image != "" ? var.agent_image : "gcr.io/cloudrun/hello"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_LOCATION"
        value = var.region
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.llmops.name
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        initial_delay_seconds = 10
        period_seconds        = 5
        failure_threshold     = 5
      }

      liveness_probe {
        http_get {
          path = "/health"
        }
        period_seconds = 30
      }
    }
  }

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.apis]
}

# Allow unauthenticated access (public API) — adjust for production
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.environment == "dev" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Workload Identity Federation (for GitHub Actions CI/CD) ------------------

resource "google_iam_workload_identity_pool" "github" {
  count                     = var.github_repo != "" ? 1 : 0
  provider                  = google-beta
  workload_identity_pool_id = "github-pool-${var.environment}"
  display_name              = "GitHub Actions Pool (${var.environment})"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = var.github_repo != "" ? 1 : 0
  provider                           = google-beta
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub Actions Provider"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_condition = "assertion.repository == '${var.github_repo}'"
}

# CI/CD Service Account
resource "google_service_account" "cicd" {
  count        = var.github_repo != "" ? 1 : 0
  account_id   = "llmops-cicd-${var.environment}"
  display_name = "LLMOps CI/CD Service Account (${var.environment})"
}

locals {
  cicd_roles = [
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/storage.objectAdmin",
    "roles/aiplatform.user",
  ]
}

resource "google_project_iam_member" "cicd_roles" {
  for_each = var.github_repo != "" ? toset(local.cicd_roles) : toset([])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.cicd[0].email}"
}

resource "google_service_account_iam_member" "cicd_wif" {
  count              = var.github_repo != "" ? 1 : 0
  service_account_id = google_service_account.cicd[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repo}"
}

# --- Outputs ------------------------------------------------------------------

output "cloud_run_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.agent.uri
}

output "gcs_bucket" {
  description = "GCS bucket name"
  value       = google_storage_bucket.llmops.name
}

output "artifact_registry" {
  description = "Artifact Registry repository"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.agent.repository_id}"
}

output "agent_service_account" {
  description = "Agent service account email"
  value       = google_service_account.agent.email
}

output "wif_provider" {
  description = "Workload Identity Federation provider"
  value       = var.github_repo != "" ? google_iam_workload_identity_pool_provider.github[0].name : "N/A"
}
