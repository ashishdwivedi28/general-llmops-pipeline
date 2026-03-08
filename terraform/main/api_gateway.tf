# ==============================================================================
# API Gateway — Authentication, rate limiting, and routing
# ==============================================================================
# Deploys a Google Cloud API Gateway in front of Cloud Run.
#
# In simpler setups the GatewayMiddleware (serving/gateway.py) handles auth
# at the application level.  This Terraform resource adds a cloud-native
# gateway for production hardening:
#   - Managed TLS termination
#   - Request validation via OpenAPI spec
#   - IAM-based access control
#   - Cloud Logging at the edge
# ==============================================================================

# --- Enable API Gateway APIs --------------------------------------------------

resource "google_project_service" "apigateway" {
  project = var.project_id
  service = "apigateway.googleapis.com"

  disable_dependent_services = false
  disable_on_destroy         = false
}

resource "google_project_service" "servicecontrol" {
  project = var.project_id
  service = "servicecontrol.googleapis.com"

  disable_dependent_services = false
  disable_on_destroy         = false
}

resource "google_project_service" "servicemanagement" {
  project = var.project_id
  service = "servicemanagement.googleapis.com"

  disable_dependent_services = false
  disable_on_destroy         = false
}

# --- API Configuration (OpenAPI spec) -----------------------------------------

variable "api_gateway_enabled" {
  description = "Whether to create the Cloud API Gateway resources"
  type        = bool
  default     = false  # Opt-in — set true in production
}

resource "google_api_gateway_api" "llmops" {
  count    = var.api_gateway_enabled ? 1 : 0
  provider = google-beta
  api_id   = "llmops-api-${var.environment}"
  project  = var.project_id

  depends_on = [
    google_project_service.apigateway,
    google_project_service.servicecontrol,
    google_project_service.servicemanagement,
  ]
}

resource "google_api_gateway_api_config" "llmops" {
  count         = var.api_gateway_enabled ? 1 : 0
  provider      = google-beta
  api           = google_api_gateway_api.llmops[0].api_id
  api_config_id = "llmops-config-${var.environment}"
  project       = var.project_id

  openapi_documents {
    document {
      path = "openapi.yaml"
      contents = base64encode(templatefile("${path.module}/openapi.yaml.tpl", {
        cloud_run_url = google_cloud_run_v2_service.agent.uri
        project_id    = var.project_id
      }))
    }
  }

  gateway_config {
    backend_config {
      google_service_account = var.agent_service_account_email
    }
  }

  depends_on = [google_api_gateway_api.llmops]
}

resource "google_api_gateway_gateway" "llmops" {
  count      = var.api_gateway_enabled ? 1 : 0
  provider   = google-beta
  api_config = google_api_gateway_api_config.llmops[0].id
  gateway_id = "llmops-gateway-${var.environment}"
  project    = var.project_id
  region     = var.region

  depends_on = [google_api_gateway_api_config.llmops]
}

# --- Outputs ------------------------------------------------------------------

output "api_gateway_url" {
  description = "API Gateway URL (empty if disabled)"
  value       = var.api_gateway_enabled ? google_api_gateway_gateway.llmops[0].default_hostname : ""
}
