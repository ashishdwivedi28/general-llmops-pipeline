# Terraform Infrastructure — LLMOps Pipeline
> **Author:** Ashish Dwivedi | **Date:** March 2026 | **Project:** General-Purpose LLMOps Pipeline on GCP

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture Decision: Destroy vs Reuse](#2-architecture-decision-destroy-vs-reuse)
3. [Infrastructure Modules](#3-infrastructure-modules)
4. [Resources Created](#4-resources-created)
5. [Security Design](#5-security-design)
6. [Audit Findings & Fixes](#6-audit-findings--fixes)
7. [Deployment Steps](#7-deployment-steps)
8. [File Reference](#8-file-reference)
9. [Environment Separation](#9-environment-separation)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Overview

The Terraform infrastructure is split into **two independent modules** that must be applied in order:

```
terraform/
├── bootstrap/     ← Run ONCE from local machine
│   └── main.tf   ← WIF, Service Accounts, Artifact Registry, State Bucket
└── main/          ← Run by CI/CD on every deployment
    ├── main.tf          ← Cloud Run, GCS Bucket, Providers, Backend
    ├── bigquery.tf      ← BigQuery Dataset + 4 Tables
    ├── api_gateway.tf   ← Cloud API Gateway (optional, enabled per env)
    ├── monitoring.tf    ← Cloud Monitoring Alerts
    ├── scheduler.tf     ← Cloud Scheduler for pipeline automation
    └── secrets.tf       ← Secret Manager secrets
```

### Design Philosophy
- **Bootstrap = one-time**: Sets up the trust relationship between GitHub Actions and GCP
- **Main = repeatable**: Applied by every CI/CD deployment; idempotent
- **Config-driven**: Change `terraform.tfvars` to change environment behavior
- **Least privilege**: Each service account has only the roles it needs

---

## 2. Architecture Decision: Destroy vs Reuse

### Recommendation: ✅ UPDATE AND REUSE — Do NOT Destroy

**Reasoning:**

| Module | Decision | Why |
|--------|----------|-----|
| **bootstrap/** | ✅ Keep as-is | WIF, SAs, Artifact Registry are still valid. Destroying and recreating would require re-configuring all GitHub Secrets/Variables. WIF pool names cannot be reused for 30 days after deletion. |
| **main/** | ⚠️ Update & Reapply | New resources added (BigQuery, Scheduler, Secrets, Monitoring, API Gateway). Apply will add these without touching Cloud Run or GCS. |

**When to use `terraform destroy`:**
- Only if you want to completely shut down the GCP project and start fresh
- If you are changing the `project_id` (resources cannot be moved between projects)
- If resource names conflict due to architecture rename

**Safe approach for current situation:**
```bash
# In main/ — run plan first to see what changes
terraform plan

# Then apply only what's needed
terraform apply
```

---

## 3. Infrastructure Modules

### 3.1 Bootstrap Module (`terraform/bootstrap/`)

**Run once from your local machine before anything else.**

Creates the foundational trust layer:

| Resource | Type | Purpose |
|----------|------|---------|
| `google_iam_workload_identity_pool` | WIF Pool | Allows GitHub Actions to authenticate with GCP without stored keys |
| `google_iam_workload_identity_pool_provider` | WIF Provider | Maps GitHub OIDC tokens to GCP identities |
| `google_service_account.agent` | SA | Attached to Cloud Run; runs the LLMOps agent |
| `google_service_account.cicd` | SA | Used by GitHub Actions for deployments |
| `google_artifact_registry_repository` | Docker Registry | Stores Docker images for Cloud Run |
| `google_storage_bucket.terraform_state` | GCS Bucket | Stores Terraform state for the main module |
| `google_project_service.apis` | API Enablement | Enables 17 required GCP APIs |

**Bootstrap Service Account Roles:**

```
Agent SA (Cloud Run):               CI/CD SA (GitHub Actions):
  roles/aiplatform.user               roles/run.admin
  roles/storage.objectAdmin           roles/artifactregistry.writer
  roles/logging.logWriter             roles/iam.serviceAccountUser
  roles/cloudtrace.agent              roles/storage.objectAdmin
  roles/bigquery.dataEditor           roles/aiplatform.user
  roles/secretmanager.secretAccessor
  roles/dlp.user
```

### 3.2 Main Module (`terraform/main/`)

**Applied by CI/CD on every deployment. Also runnable locally.**

---

## 4. Resources Created

### 4.1 Cloud Run Service (`main.tf`)

```
resource: google_cloud_run_v2_service.agent
name:     llmops-agent-{environment}
```

| Setting | Dev | Staging/Prod |
|---------|-----|--------------|
| Ingress | `INGRESS_TRAFFIC_ALL` (requires auth token) | `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` |
| Min instances | 0 (scale to zero) | Configurable via `cloud_run_min_instances` |
| Max instances | 3 | Configurable via `cloud_run_max_instances` |
| CPU | 2 vCPU | 2 vCPU (configurable) |
| Memory | 2 GiB | 2 GiB (configurable) |
| Public access (`allUsers`) | ❌ Removed — auth required | ❌ No |

**Environment Variables injected:**
- `GCP_PROJECT_ID` — plain value
- `GCP_LOCATION` — plain value
- `GCS_BUCKET` — plain value (bucket name)
- `ENVIRONMENT` — plain value
- `LLMOPS_API_KEYS` — from Secret Manager (secure)

**Health Checks:**
- Startup probe: `GET /health` — 15s delay, 5s interval, 6 retries
- Liveness probe: `GET /health` — 30s interval

### 4.2 GCS Bucket (`main.tf`)

```
resource: google_storage_bucket.llmops
name:     {project_id}-llmops-{environment}
```

Used for all LLMOps artifacts. Structured as:

```
gs://{bucket}/
├── documents/              ← Source documents for ingestion
├── embeddings/             ← Generated embeddings
├── pipelines/              ← Compiled KFP pipeline YAMLs
├── manifests/              ← Pipeline Artifact Manifest (online/offline bridge)
│   └── {app_id}/
│       └── latest.json     ← Active artifact references (vector index, model, prompt version)
├── prompts/                ← Prompt Registry (versioned per app)
│   └── {app_id}/
│       ├── v1.yaml
│       ├── v2.yaml
│       └── latest.yaml
└── eval_datasets/          ← Generated evaluation QA datasets
```

Settings:
- Uniform bucket-level access: ✅ enabled
- Versioning: ✅ enabled (for manifests + prompts)
- 90-day lifecycle rule: ✅ (auto-delete old objects)
- Public access: ❌ blocked

### 4.3 BigQuery Dataset + Tables (`bigquery.tf`)

```
dataset: llmops
project: {project_id}
```

| Table | Description | Partitioned | Used By |
|-------|-------------|-------------|---------|
| `interactions` | Every chat Q&A pair with tokens + cost | By `timestamp` (DAY) | Monitoring pipeline, Dashboard |
| `feedback` | User thumbs-up/down ratings with comments | By `timestamp` (DAY) | Fine-tuning pipeline, Dashboard |
| `evaluations` | Pipeline eval scores (PASS/BLOCKED) | By `timestamp` (DAY) | Admin dashboard, Quality gate |
| `costs` | Per-request LLM cost per model/user/app | By `timestamp` (DAY) | Cost tracking, Dashboard |

Access control:
- Agent SA: `WRITER` — Cloud Run can write interactions, feedback, costs
- Project owners: `OWNER`
- Project readers: `READER`

### 4.4 Secret Manager Secrets (`secrets.tf`)

| Secret ID | Purpose |
|-----------|---------|
| `llmops-api-keys` | Comma-separated API keys for gateway authentication |
| `llmops-openai-key` | OpenAI API key (if using multi-provider model routing) |
| `llmops-anthropic-key` | Anthropic API key (if using multi-provider model routing) |

**How to add a secret value:**
```bash
# Add an API key value
echo "my-api-key-value" | gcloud secrets versions add llmops-api-keys --data-file=-

# Or via console
gcloud secrets versions add llmops-api-keys --data-file=./apikeys.txt
```

Agent SA has `roles/secretmanager.secretAccessor` on all secrets. Secrets are injected into Cloud Run as environment variables (not hardcoded).

### 4.5 Cloud Monitoring Alerts (`monitoring.tf`)

| Alert | Condition | Threshold |
|-------|-----------|-----------|
| Error Rate | Cloud Run 5xx error rate | > 5% over 5 minutes |
| Latency | Cloud Run p95 response time | > 10 seconds |
| Log-based Degradation | Custom log metric from monitoring pipeline | Configurable |

Set `alert_email` in `terraform.tfvars` to receive email notifications.

Email notification channel is created only when `alert_email != ""`.

### 4.6 Cloud Scheduler Jobs (`scheduler.tf`)

| Job | Schedule | Purpose |
|-----|----------|---------|
| `llmops-monitoring-pipeline-{env}` | Daily 2am UTC | Triggers KFP monitoring pipeline |
| `llmops-master-pipeline-{env}` | Weekly Sunday 3am UTC | Triggers full master pipeline |

Both jobs POST to the Vertex AI Pipelines REST API using OAuth tokens from the agent SA.

Scheduled pipeline YAML must be compiled and uploaded to GCS before the jobs can succeed:
```
gs://{bucket}/pipelines/monitoring_pipeline.yaml
```

### 4.7 API Gateway (`api_gateway.tf`)

Disabled by default (`api_gateway_enabled = false`). Enable for staging/prod.

When enabled, creates:
- `google_api_gateway_api` — API definition
- `google_api_gateway_api_config` — OpenAPI spec (from `openapi.yaml.tpl`)
- `google_api_gateway_gateway` — Edge gateway instance

Enables:
- Managed TLS termination
- API key authentication
- Rate limiting
- Cloud Logging at the edge

---

## 5. Security Design

### Security Principles Applied

| Principle | Implementation |
|-----------|---------------|
| **Least Privilege** | Agent SA and CI/CD SA have minimum required roles. No `Owner` or `Editor`. |
| **No stored credentials** | GitHub Actions uses Workload Identity Federation (OIDC) — no service account keys |
| **Secret Manager** | No credentials in environment variables as plain text — injected from Secret Manager |
| **No public access (any env)** | `allUsers` IAM binding removed from ALL environments including dev. Auth token always required. |
| **Uniform bucket access** | GCS bucket uses uniform access control — no ACLs |
| **Versioned state** | Terraform state bucket has versioning enabled to recover from accidental changes |
| **No force destroy** | GCS and state buckets have `force_destroy = false` to prevent accidental data loss |
| **Explicit invoker list** | Only Agent SA, CI/CD SA, and optional dev SA emails can invoke Cloud Run |

### Security Risks Identified and Addressed

| Risk | Status | Mitigation |
|------|--------|-----------|
| Hardcoded credentials in .env | ⚠️ Dev only | Secret Manager secrets created; Cloud Run wired to use them |
| Public Cloud Run (allUsers) in ANY env | ✅ Fixed | `allUsers` IAM binding removed. Only specific SAs can invoke. |
| Public Cloud Run in prod | ✅ Fixed | `ingress = INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` for staging/prod |
| Overly broad SA roles | ✅ Acceptable | Roles scoped to required services only (no `Owner`/`Editor`) |
| BigQuery open writes | ✅ Scoped | Only Agent SA has WRITER access; others are READER |
| Exposed API without auth | ✅ Fixed | Cloud Run requires auth token in all environments; API Gateway for prod |
| Storage bucket public | ✅ Blocked | `uniform_bucket_level_access = true`; no public ACLs |

---

## 5.1 Cloud Run Authentication and Access Control

### Security Issue Fixed: `allUsers` Removed

**What was wrong:**
The previous Terraform configuration granted `roles/run.invoker` to `allUsers` for `environment = "dev"`:

```hcl
# ❌ INSECURE — removed
resource "google_cloud_run_v2_service_iam_member" "public" {
  count  = var.environment == "dev" ? 1 : 0
  role   = "roles/run.invoker"
  member = "allUsers"          # ← Anyone on the internet, no auth required
}
```

This made the Cloud Run endpoint **completely public** — any HTTP client anywhere in the world could call the LLMOps agent with no credentials. Risks included:

- Unauthorized access to the HR chatbot / RAG agent
- API abuse and prompt injection attacks from untrusted callers
- Unexpected GCP cost from unsolicited traffic
- Exposure of internal GCS paths, model names, and system prompts through responses

**What was changed:**

The `allUsers` binding was removed entirely. Cloud Run now uses **explicit, least-privilege IAM bindings** for every environment:

```hcl
# ✅ SECURE — only specific service accounts can invoke

# Agent SA self-invocation (internal tool calls, canary health checks)
resource "google_cloud_run_v2_service_iam_member" "invoker_agent_sa" {
  member = "serviceAccount:${var.agent_service_account_email}"
  role   = "roles/run.invoker"
}

# CI/CD SA (smoke tests and health checks from GitHub Actions)
resource "google_cloud_run_v2_service_iam_member" "invoker_cicd_sa" {
  count  = var.cicd_service_account_email != "" ? 1 : 0
  member = "serviceAccount:${var.cicd_service_account_email}"
  role   = "roles/run.invoker"
}

# Optional developer SAs (dev environment only, empty by default)
resource "google_cloud_run_v2_service_iam_member" "invoker_developers" {
  for_each = toset(var.environment == "dev" ? var.developer_sa_emails : [])
  member   = "serviceAccount:${each.value}"
  role     = "roles/run.invoker"
}
```

### Who Can Invoke Cloud Run

| Caller | How | Binding |
|--------|-----|---------|
| Agent SA (Cloud Run itself) | Internal service-to-service calls | `invoker_agent_sa` |
| CI/CD SA (GitHub Actions) | Deployment smoke tests, health checks | `invoker_cicd_sa` |
| Developer SA (dev only) | Manual testing via CLI or Postman | `invoker_developers` (opt-in via tfvars) |
| API Gateway (prod) | Routes external user traffic | Via service account in gateway config |
| `allUsers` (anonymous) | ❌ **Blocked in all environments** | Removed |

### How to Call Cloud Run as a Developer

Since `allUsers` is removed, developers must authenticate before calling Cloud Run. Options:

**Option A — Local proxy (easiest, no token management):**
```bash
# Creates a local tunnel to Cloud Run on localhost:8080
gcloud run services proxy llmops-agent-dev \
  --region=us-central1 \
  --project=YOUR_GCP_PROJECT_ID

# Now call it locally
curl http://localhost:8080/health
curl -X POST http://localhost:8080/chat -d '{"message": "hello"}'
```

**Option B — Bearer token (for scripts, Postman, curl):**
```bash
# Get an identity token for the Cloud Run URL
TOKEN=$(gcloud auth print-identity-token)
CLOUD_RUN_URL=$(terraform output -raw cloud_run_url)

curl -H "Authorization: Bearer $TOKEN" "$CLOUD_RUN_URL/health"
```

**Option C — Add your SA to developer_sa_emails (persistent dev access):**
```hcl
# In terraform.tfvars (dev only):
developer_sa_emails = ["your-dev-sa@project.iam.gserviceaccount.com"]
```
Then run `terraform apply`.

### Ingress vs Authentication — Key Distinction

| Concept | What it controls |
|---------|-----------------|
| `ingress` | **Network path** — which networks can reach Cloud Run |
| IAM `roles/run.invoker` | **Authentication** — which identities can call Cloud Run |

Both must be configured correctly:

| Environment | Ingress | Auth |
|-------------|---------|------|
| `dev` | `INGRESS_TRAFFIC_ALL` (reachable from internet but auth required) | Agent SA + CI/CD SA + optional dev SAs |
| `staging/prod` | `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` (VPC/API Gateway only) | Agent SA + CI/CD SA |

---

## 6. Audit Findings & Fixes

### Issues Found and Fixed

| # | File | Issue | Fix |
|---|------|-------|-----|
| 1 | `api_gateway.tf` | `google_api_gateway_*` resources used `provider = google` — not supported | Changed to `provider = google-beta` for all 3 resources |
| 2 | `api_gateway.tf` | Typo: `provider = google-beta-beta` | Fixed to `provider = google-beta` |
| 3 | `bigquery.tf` | `default_table_expiration_ms = 31536000000` caused Terraform float error | Removed (optional attribute; tables use `deletion_protection = false` for dev) |
| 4 | `main.tf` | `google-beta` provider declared in `required_providers` but no `provider {}` block | Added `provider "google-beta"` block |
| 5 | `main.tf` | Backend was GCS but state bucket doesn't exist → replaced with local for dev | Documented both options; GCS backend commented out with instructions |
| 6 | `main.tf` | Cloud Run `ingress = INGRESS_TRAFFIC_ALL` for all environments | Changed to internal load balancer ingress for non-dev environments |
| 7 | `main.tf` | Secret Manager secrets not wired into Cloud Run env vars | Added `value_source.secret_key_ref` for `LLMOPS_API_KEYS` |
| 8 | `terraform.tfvars` | Missing `alert_email`, `api_gateway_enabled`, `monitoring_*`, `scheduler_*` vars | Updated `terraform.tfvars` with all variables and sensible defaults |
| 9 | `main.tf` | Hard-coded scaling limits instead of variables | Added `cloud_run_min_instances`, `cloud_run_max_instances`, `cloud_run_cpu`, `cloud_run_memory` variables |
| 10 | `main.tf` | `allUsers` IAM binding granted public unauthenticated access to Cloud Run in dev | Removed entirely; replaced with 3 explicit SA bindings (agent SA, CI/CD SA, optional developer SAs) |

---

## 7. Deployment Steps

### First-Time Setup (Run Once)

#### Step 1: Bootstrap (One-Time)
```bash
cd terraform/bootstrap

# Create tfvars from example
cp terraform.tfvars.example terraform.tfvars
# Edit: set project_id, repository_owner, repository_name

terraform init
terraform plan
terraform apply

# Copy the output commands and run them to set GitHub secrets
terraform output setup_instructions
```

#### Step 2: Configure GitHub
Run the commands from `terraform output setup_instructions` to set:
- GitHub Variables: `GCP_PROJECT_ID`, `GCP_REGION`, `ARTIFACT_REGISTRY_REPO`, etc.
- GitHub Secrets: `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`, `AGENT_SERVICE_ACCOUNT`, etc.

#### Step 3: Apply Main Module (Local Dev)
```bash
cd terraform/main

# For local development testing (state stored locally)
terraform init
terraform plan
terraform apply

# For production (state stored in GCS — requires bootstrap to have run)
terraform init -backend-config="bucket=<TERRAFORM_STATE_BUCKET>"
terraform plan
terraform apply
```

#### Step 4: Add Secret Values
```bash
# Add API keys for gateway auth
echo "your-api-key" | gcloud secrets versions add llmops-api-keys \
  --project=YOUR_GCP_PROJECT_ID --data-file=-

# Optional: Add OpenAI key if using multi-provider routing
echo "sk-..." | gcloud secrets versions add llmops-openai-key \
  --project=YOUR_GCP_PROJECT_ID --data-file=-
```

#### Step 5: Upload Pipeline YAMLs to GCS
```bash
# After compiling KFP pipelines
python kfp_pipelines/compile_and_run.py

# Upload compiled YAMLs (the scheduler will pick them up automatically)
gsutil cp monitoring_pipeline.yaml gs://{bucket}/pipelines/
gsutil cp master_pipeline.yaml gs://{bucket}/pipelines/
```

### CI/CD Deployment (Automated)

The GitHub Actions workflow handles main module deployment automatically:
```yaml
# CI/CD runs terraform in:
# 1. terraform init -backend-config="bucket=${{ vars.TERRAFORM_STATE_BUCKET }}"
# 2. terraform plan
# 3. terraform apply -auto-approve
```

### Switching to Production Environment

Update `terraform.tfvars`:
```hcl
environment             = "prod"
cloud_run_min_instances = 1      # Keep warm
cloud_run_max_instances = 10
api_gateway_enabled     = true   # Enable for production
alert_email             = "ops@yourcompany.com"
master_pipeline_enabled = true   # Enable weekly master pipeline
```

Then apply:
```bash
terraform plan  # Review changes carefully
terraform apply
```

---

## 8. File Reference

### `terraform/main/main.tf`
Core infrastructure: providers, backend, variables, Cloud Run service, GCS bucket, outputs.

**Key variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `project_id` | required | GCP project ID |
| `region` | `us-central1` | GCP region |
| `environment` | `dev` | Deployment environment |
| `docker_image` | `""` | Full Docker image URI (set by CI/CD) |
| `agent_service_account_email` | required | Agent SA from bootstrap |
| `cloud_run_min_instances` | `0` | Scale-to-zero setting |
| `cloud_run_max_instances` | `3` | Cost control |

### `terraform/main/bigquery.tf`
BigQuery dataset and 4 tables for the LLMOps data plane.

### `terraform/main/api_gateway.tf`
Cloud API Gateway resources. **Disabled by default** (`api_gateway_enabled = false`).  
Enable for production where Cloud Run should be protected behind the gateway.

### `terraform/main/monitoring.tf`
Cloud Monitoring alert policies for error rate, latency, and custom log metrics.

### `terraform/main/scheduler.tf`
Cloud Scheduler jobs for automated pipeline execution.

### `terraform/main/secrets.tf`
Secret Manager secret resources + IAM bindings for the agent SA.

### `terraform/bootstrap/main.tf`
One-time bootstrap: WIF, Service Accounts, Artifact Registry, Terraform state bucket, API enablement.

---

## 9. Environment Separation

| Setting | `dev` | `staging` | `prod` |
|---------|-------|-----------|--------|
| Cloud Run ingress | `INGRESS_TRAFFIC_ALL` (auth required) | Internal LB only | Internal LB only |
| Cloud Run `allUsers` | ❌ Never | ❌ Never | ❌ Never |
| API Gateway | Disabled | Enabled | Enabled |
| Scale-to-zero | Yes (min=0) | Optional | No (min=1) |
| Alert email | Optional | Required | Required |
| Master pipeline schedule | Disabled | Optional | Enabled |
| State backend | Local (dev) | GCS | GCS |

To deploy to staging:
```bash
cp terraform.tfvars terraform.staging.tfvars
# Edit: environment = "staging", enable api_gateway, set alert_email
terraform apply -var-file=terraform.staging.tfvars
```

---

## 10. Troubleshooting

### `terraform init` fails with "bucket doesn't exist"
The GCS backend state bucket was not created by bootstrap, or was deleted.
```bash
# Option 1: Run bootstrap first
cd terraform/bootstrap && terraform apply

# Option 2: Use local backend for now
# In main.tf, switch backend block to:
# backend "local" { path = "terraform.tfstate" }
terraform init -reconfigure
```

### `provider google-beta` not installed
```bash
rm .terraform.lock.hcl
terraform init
```

### `google_api_gateway_api` not supported
Make sure all three API Gateway resources use `provider = google-beta`:
```hcl
resource "google_api_gateway_api" "llmops" {
  provider = google-beta  # ← Required
  ...
}
```

### `default_table_expiration_ms` float error
This is a known Terraform + Google provider issue with large integers on some platforms.
**Fix:** Remove the attribute entirely from the `google_bigquery_dataset` resource (it's optional).

### Cloud Run fails with "Secret not found"
The secret exists but has no version yet. Either:
1. Add a secret version via `gcloud secrets versions add`
2. Or comment out the secret env var in `main.tf` until a value is added

### Cloud Scheduler jobs failing
- Ensure the KFP pipeline YAML has been uploaded to GCS: `gs://{bucket}/pipelines/monitoring_pipeline.yaml`
- Verify the service account has `roles/aiplatform.user` permission

### `terraform plan` hangs
Terraform is connecting to GCP APIs. This requires:
- `gcloud auth application-default login` to be run
- Or a valid service account key in `GOOGLE_APPLICATION_CREDENTIALS`
- Check: `gcloud auth application-default print-access-token`
