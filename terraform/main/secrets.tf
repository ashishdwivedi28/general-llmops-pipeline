# ==============================================================================
# Secret Manager — Secure credential storage
# ==============================================================================
# Replaces .env files for production. Secrets are injected into Cloud Run
# as environment variables via the CI/CD pipeline or Terraform.
#
# Secrets created:
#   llmops-api-keys        — comma-separated API keys for gateway auth
#   llmops-openai-key      — OpenAI API key (if using multi-provider routing)
#   llmops-anthropic-key   — Anthropic API key (if using multi-provider routing)
# ==============================================================================

# --- Enable Secret Manager API ------------------------------------------------

resource "google_project_service" "secretmanager" {
  project = var.project_id
  service = "secretmanager.googleapis.com"

  disable_dependent_services = false
  disable_on_destroy         = false
}

# --- Secrets ------------------------------------------------------------------

resource "google_secret_manager_secret" "api_keys" {
  secret_id = "llmops-api-keys"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret" "openai_key" {
  secret_id = "llmops-openai-key"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret" "anthropic_key" {
  secret_id = "llmops-anthropic-key"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.secretmanager]
}

# --- IAM: Allow agent SA to access secrets ------------------------------------

resource "google_secret_manager_secret_iam_member" "agent_api_keys" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.api_keys.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.agent_service_account_email}"
}

resource "google_secret_manager_secret_iam_member" "agent_openai_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.openai_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.agent_service_account_email}"
}

resource "google_secret_manager_secret_iam_member" "agent_anthropic_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.anthropic_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.agent_service_account_email}"
}

# --- Outputs ------------------------------------------------------------------

output "secret_api_keys_id" {
  description = "Secret Manager resource ID for API keys"
  value       = google_secret_manager_secret.api_keys.id
}
