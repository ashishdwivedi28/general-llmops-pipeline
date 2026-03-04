# ==============================================================================
# Main Terraform — Cloud Run deployment (managed by CI/CD)
# ==============================================================================
# Creates:
#   1. GCS bucket for pipeline artifacts
#   2. Cloud Run service
#   3. IAM bindings for Cloud Run
#
# Prerequisites:
#   - Bootstrap module already applied (WIF, AR, SAs exist)
#   - Remote state bucket created by bootstrap
#
# Usage (local):
#   terraform -chdir=terraform/main init -backend-config="bucket=<STATE_BUCKET>"
#   terraform -chdir=terraform/main plan
#   terraform -chdir=terraform/main apply
#
# In CI/CD this is handled by the workflow automatically.
# ==============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    # Bucket configured via:
    #   terraform init -backend-config="bucket=BUCKET_NAME"
    prefix = "main/"
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

variable "docker_image" {
  description = "Full Docker image URI including tag or digest"
  type        = string
  default     = ""
}

variable "agent_service_account_email" {
  description = "Agent service account email (created by bootstrap)"
  type        = string
}

# --- Provider -----------------------------------------------------------------

provider "google" {
  project = var.project_id
  region  = var.region
}

# --- GCS Bucket (Pipeline Artifacts) -----------------------------------------

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

# --- Cloud Run ----------------------------------------------------------------

resource "google_cloud_run_v2_service" "agent" {
  name     = "llmops-agent-${var.environment}"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = var.agent_service_account_email

    scaling {
      min_instance_count = var.environment == "prod" ? 1 : 0
      max_instance_count = var.environment == "prod" ? 10 : 3
    }

    containers {
      # Use provided image or fall back to a placeholder
      image = var.docker_image != "" ? var.docker_image : "gcr.io/cloudrun/hello"

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
        initial_delay_seconds = 15
        period_seconds        = 5
        failure_threshold     = 6
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
}

# Public access for dev, authenticated for staging/prod
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.environment == "dev" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Outputs ------------------------------------------------------------------

output "cloud_run_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.agent.uri
}

output "gcs_bucket" {
  description = "GCS bucket for pipeline artifacts"
  value       = google_storage_bucket.llmops.name
}
