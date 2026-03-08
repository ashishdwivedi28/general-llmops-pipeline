# ==============================================================================
# Bootstrap Terraform — One-time CI/CD infrastructure setup
# ==============================================================================
# Run this ONCE from your local machine to create:
#   1. Workload Identity Federation (WIF) for GitHub Actions
#   2. Artifact Registry for Docker images
#   3. GCS bucket for Terraform state (used by main module)
#   4. GitHub Variables (auto-configured for CI/CD workflow)
#   5. Service Accounts (CI/CD + Agent)
#
# Usage:
#   cd terraform/bootstrap
#   cp terraform.tfvars.example terraform.tfvars  # edit with your values
#   terraform init
#   terraform plan
#   terraform apply
#
# State: Local (terraform.tfstate in this directory — one-time operation)
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

variable "repository_owner" {
  description = "GitHub username or organization that owns the repo"
  type        = string
}

variable "repository_name" {
  description = "GitHub repository name"
  type        = string
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

# GitHub provider removed — Variables are set manually via gh CLI after apply
# See the setup_instructions output for exact commands

# --- Enable APIs --------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "run.googleapis.com",
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
    "sts.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# --- Artifact Registry --------------------------------------------------------

resource "google_artifact_registry_repository" "agent" {
  location      = var.region
  repository_id = "llmops-agent-${var.environment}"
  format        = "DOCKER"
  description   = "Docker images for LLMOps agent"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.apis]
}

# --- GCS Bucket for Terraform State ------------------------------------------

resource "google_storage_bucket" "terraform_state" {
  name          = "terraform-state-llmops-${var.environment}-${random_id.suffix.hex}"
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  labels = {
    environment = var.environment
    managed_by  = "terraform"
    purpose     = "terraform-state"
  }
}

resource "random_id" "suffix" {
  byte_length = 4
}

# --- Service Accounts ---------------------------------------------------------

# Agent SA — attached to Cloud Run
resource "google_service_account" "agent" {
  account_id   = "llmops-agent-${var.environment}"
  display_name = "LLMOps Agent SA (${var.environment})"

  depends_on = [google_project_service.apis]
}

locals {
  agent_roles = [
    "roles/aiplatform.user",
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/bigquery.dataEditor",
    "roles/secretmanager.secretAccessor",
    "roles/dlp.user",
  ]
}

resource "google_project_iam_member" "agent_roles" {
  for_each = toset(local.agent_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.agent.email}"
}

# CI/CD SA — used by GitHub Actions
resource "google_service_account" "cicd" {
  account_id   = "llmops-cicd-${var.environment}"
  display_name = "LLMOps CI/CD SA (${var.environment})"

  depends_on = [google_project_service.apis]
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
  for_each = toset(local.cicd_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.cicd.email}"
}

# --- Workload Identity Federation ---------------------------------------------

# Note: Pool ID "github-pool-dev" may be soft-deleted (GCP keeps deleted names
# for 30 days). Using a different ID "gh-actions-pool-dev" to avoid 409 conflicts.
resource "google_iam_workload_identity_pool" "github" {
  provider                  = google-beta
  workload_identity_pool_id = "gh-actions-pool-${var.environment}"
  display_name              = "GitHub Actions Pool (${var.environment})"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  provider                           = google-beta
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
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

  attribute_condition = "assertion.repository == '${var.repository_owner}/${var.repository_name}'"
}

# Allow GitHub Actions to impersonate the CI/CD SA via WIF
resource "google_service_account_iam_member" "cicd_wif" {
  service_account_id = google_service_account.cicd.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.repository_owner}/${var.repository_name}"
}

# --- GitHub Variables & Secrets — Set manually after terraform apply ---------
# Run: terraform output setup_instructions   to get the exact commands, then paste into your terminal.
#
# VARIABLES (non-sensitive — use gh CLI):
#   gh variable set GCP_PROJECT_ID         --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "YOUR_GCP_PROJECT_ID"
#   gh variable set GCP_REGION             --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "us-central1"
#   gh variable set ARTIFACT_REGISTRY_REPO --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "llmops-agent-dev"
#   gh variable set IMAGE_NAME             --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "llmops-agent"
#   gh variable set CLOUD_RUN_SERVICE_DEV  --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "llmops-agent-dev"
#   gh variable set TERRAFORM_STATE_BUCKET --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "<terraform output terraform_state_bucket>"
#
# SECRETS (sensitive — must use gh CLI, cannot use web UI for WIF values):
#   gh secret set WIF_PROVIDER          --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "<terraform output wif_provider>"
#   gh secret set WIF_SERVICE_ACCOUNT   --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "<terraform output cicd_service_account>"
#   gh secret set AGENT_SERVICE_ACCOUNT --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "<terraform output agent_service_account>"
#   gh secret set GCS_BUCKET            --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "YOUR_GCP_PROJECT_ID-llmops-dev"

# --- Outputs ------------------------------------------------------------------

output "wif_provider" {
  description = "WIF provider name — set as GitHub Secret: WIF_PROVIDER"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "cicd_service_account" {
  description = "CI/CD SA email — set as GitHub Secret: WIF_SERVICE_ACCOUNT"
  value       = google_service_account.cicd.email
}

output "agent_service_account" {
  description = "Agent SA email — set as GitHub Secret: AGENT_SERVICE_ACCOUNT"
  value       = google_service_account.agent.email
}

output "artifact_registry" {
  description = "Artifact Registry URI"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.agent.repository_id}"
}

output "terraform_state_bucket" {
  description = "GCS bucket for main module's Terraform state"
  value       = google_storage_bucket.terraform_state.name
}

output "setup_instructions" {
  description = "Run these commands after apply to configure GitHub repo"
  value       = <<-EOT
    # --- Copy-paste these commands into your terminal ---

    # 1. Variables (non-sensitive)
    gh variable set GCP_PROJECT_ID         --repo ${var.repository_owner}/${var.repository_name} --body "${var.project_id}"
    gh variable set GCP_REGION             --repo ${var.repository_owner}/${var.repository_name} --body "${var.region}"
    gh variable set ARTIFACT_REGISTRY_REPO --repo ${var.repository_owner}/${var.repository_name} --body "${google_artifact_registry_repository.agent.repository_id}"
    gh variable set IMAGE_NAME             --repo ${var.repository_owner}/${var.repository_name} --body "llmops-agent"
    gh variable set CLOUD_RUN_SERVICE_DEV  --repo ${var.repository_owner}/${var.repository_name} --body "llmops-agent-${var.environment}"
    gh variable set TERRAFORM_STATE_BUCKET --repo ${var.repository_owner}/${var.repository_name} --body "${google_storage_bucket.terraform_state.name}"

    # 2. Secrets (sensitive)
    gh secret set WIF_PROVIDER          --repo ${var.repository_owner}/${var.repository_name} --body "${google_iam_workload_identity_pool_provider.github.name}"
    gh secret set WIF_SERVICE_ACCOUNT   --repo ${var.repository_owner}/${var.repository_name} --body "${google_service_account.cicd.email}"
    gh secret set AGENT_SERVICE_ACCOUNT --repo ${var.repository_owner}/${var.repository_name} --body "${google_service_account.agent.email}"
    gh secret set GCS_BUCKET            --repo ${var.repository_owner}/${var.repository_name} --body "${var.project_id}-llmops-${var.environment}"
  EOT
}
