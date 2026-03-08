# ==============================================================================
# Main Terraform — Cloud Run deployment (managed by CI/CD)
# ==============================================================================
# Creates:
#   1. GCS bucket for pipeline artifacts + manifests
#   2. Cloud Run service (LLMOps agent)
#   3. IAM bindings for Cloud Run
#   4. BigQuery dataset + tables (see bigquery.tf)
#   5. Cloud Monitoring alerts (see monitoring.tf)
#   6. Cloud Scheduler jobs (see scheduler.tf)
#   7. Secret Manager secrets (see secrets.tf)
#   8. API Gateway [optional] (see api_gateway.tf)
#
# Prerequisites:
#   - Bootstrap module already applied (WIF, AR, SAs exist)
#   - Remote GCS state bucket created by bootstrap
#
# Local usage:
#   cd terraform/main
#   terraform init -backend-config="bucket=<TERRAFORM_STATE_BUCKET>"
#   terraform plan -var-file=terraform.tfvars
#   terraform apply -var-file=terraform.tfvars
#
#   To use local backend for development/testing:
#   terraform init -reconfigure -backend-config="path=terraform.tfstate"
#
# In CI/CD this is handled by the workflow automatically via:
#   terraform init -backend-config="bucket=${{ vars.TERRAFORM_STATE_BUCKET }}"
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

  # PRODUCTION: Use GCS backend (uncomment and set bucket in init command)
  # Run: terraform init -backend-config="bucket=<TERRAFORM_STATE_BUCKET>"
  # backend "gcs" {
  #   prefix = "main/"
  # }

  # LOCAL / DEV: Use local backend for development and testing
  backend "local" {
    path = "terraform.tfstate"
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

variable "cicd_service_account_email" {
  description = "CI/CD service account email (created by bootstrap)"
  type        = string
  default     = ""
}

variable "cloud_run_min_instances" {
  description = "Minimum Cloud Run instances (0 = scale to zero)"
  type        = number
  default     = 0
}

variable "cloud_run_max_instances" {
  description = "Maximum Cloud Run instances"
  type        = number
  default     = 3
}

variable "cloud_run_cpu" {
  description = "CPU limit for Cloud Run containers"
  type        = string
  default     = "2"
}

variable "cloud_run_memory" {
  description = "Memory limit for Cloud Run containers"
  type        = string
  default     = "2Gi"
}

variable "developer_sa_emails" {
  description = "List of developer or user service account emails allowed to invoke Cloud Run in dev/testing. Empty list = no additional invokers."
  type        = list(string)
  default     = []
}

# --- Provider -----------------------------------------------------------------

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
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

  # Transition older objects to Nearline (cheaper) before full deletion
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
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
  # INGRESS POLICY:
  # dev     — TRAFFIC_ALL: reachable from internet but authentication is still required
  #           (developers use: gcloud run services proxy OR gcloud auth print-identity-token)
  # staging/prod — INTERNAL_LOAD_BALANCER: only reachable via API Gateway / VPC
  ingress = var.environment == "dev" ? "INGRESS_TRAFFIC_ALL" : "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = var.agent_service_account_email

    # Explicit timeout: LLM responses can take 60-120s; 300s matches the default
    # and covers complex multi-tool agent workflows. Increase to 3600 if needed.
    timeout = "300s"

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    containers {
      # Use provided image or fall back to a placeholder for initial apply
      image = var.docker_image != "" ? var.docker_image : "gcr.io/cloudrun/hello"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
        # CPU only allocated during request processing (cost optimization)
        cpu_idle = false
      }

      # --- Plain environment variables ------------------------------------------
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
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }

      # --- Secret Manager environment variables (injected securely) -------------
      # IMPORTANT: Secret env vars below require an active secret VERSION in
      # Secret Manager before terraform apply will succeed.
      #
      # Step 1: terraform apply (this creates the secret shells)
      # Step 2: Add secret values:
      #   echo -n "your-api-key" | gcloud secrets versions add llmops-api-keys --data-file=-
      # Step 3: Uncomment the env block below and re-run terraform apply
      #
       env {
         name = "LLMOPS_API_KEYS"
         value_source {
           secret_key_ref {
             secret  = google_secret_manager_secret.api_keys.secret_id
             version = "latest"
           }
         }
      }

      # Uncomment after adding a secret version to Secret Manager:
      # env {
      #    name = "OPENAI_API_KEY"
      #    value_source {
      #      secret_key_ref {
      #        secret  = google_secret_manager_secret.openai_key.secret_id
      #        version = "latest"
      #      }
      #    }
      # }

       # env {
       #   name = "ANTHROPIC_API_KEY"
       #   value_source {
       #     secret_key_ref {
       #       secret  = google_secret_manager_secret.anthropic_key.secret_id
       #       version = "latest"
       #     }
       #   }
       # }

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
        period_seconds    = 30
        failure_threshold = 3
      }
    }
  }

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  # Ensure GCS bucket and secrets are created before Cloud Run
  # (secrets are referenced by name in env vars above)
  depends_on = [
    google_storage_bucket.llmops,
    google_secret_manager_secret.api_keys,
    google_secret_manager_secret.openai_key,
    google_secret_manager_secret.anthropic_key,
  ]
}

# ==============================================================================
# Cloud Run IAM — Authenticated access only (no allUsers / public access)
# ==============================================================================
# SECURITY: The allUsers binding has been removed from all environments.
# Cloud Run ALWAYS requires a valid Google identity token (Bearer token).
#
# Who can invoke:
#   1. Agent SA       — self-invocation / internal service-to-service calls
#   2. CI/CD SA       — deployment smoke tests and health checks from GitHub Actions
#   3. Scheduler SA   — Cloud Scheduler pipeline trigger jobs
#   4. developer_sa_emails — optional list for dev testing (empty by default)
#
# How developers call Cloud Run in dev:
#   Option A: gcloud run services proxy llmops-agent-dev --region=us-central1
#   Option B: curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" <URL>
# ==============================================================================

# 1. Agent SA — can invoke itself (internal tool calls, self-ping, canary checks)
resource "google_cloud_run_v2_service_iam_member" "invoker_agent_sa" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.agent_service_account_email}"
}

# 2. CI/CD SA — can invoke for smoke tests and health checks after deployment
resource "google_cloud_run_v2_service_iam_member" "invoker_cicd_sa" {
  count    = var.cicd_service_account_email != "" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.cicd_service_account_email}"
}

# 3. Scheduler SA — Cloud Scheduler triggers pipelines via the Vertex AI API directly,
#    not via Cloud Run. The agent SA binding (#1 above) already covers any SA-to-SA
#    internal calls. No separate scheduler binding needed unless pipeline_sa_email
#    is set to a different SA — in that case add it to developer_sa_emails.

# 4. Developer access — optional list of additional invokers for dev testing
#    Add developer SA emails to var.developer_sa_emails in terraform.tfvars
#    Leave empty in staging/prod
resource "google_cloud_run_v2_service_iam_member" "invoker_developers" {
  for_each = toset(var.environment == "dev" ? var.developer_sa_emails : [])
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value}"
}

# --- Outputs ------------------------------------------------------------------

output "cloud_run_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.agent.uri
}

output "cloud_run_service_name" {
  description = "Cloud Run service name (for CI/CD deploy step)"
  value       = google_cloud_run_v2_service.agent.name
}

output "gcs_bucket" {
  description = "GCS bucket name for pipeline artifacts"
  value       = google_storage_bucket.llmops.name
}

output "gcs_manifests_path" {
  description = "GCS path for pipeline artifact manifests (online/offline bridge)"
  value       = "gs://${google_storage_bucket.llmops.name}/manifests/"
}

output "gcs_prompts_path" {
  description = "GCS path for Prompt Registry (versioned prompts)"
  value       = "gs://${google_storage_bucket.llmops.name}/prompts/"
}

output "gcs_pipelines_path" {
  description = "GCS path for compiled KFP pipeline YAMLs"
  value       = "gs://${google_storage_bucket.llmops.name}/pipelines/"
}
