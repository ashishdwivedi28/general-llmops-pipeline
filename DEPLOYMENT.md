# LLMOps Pipeline — Deployment Guide

> Follow these steps in order. After completing this guide your LLMOps pipeline
> will be running live on Google Cloud.

---

## Prerequisites

Install these tools before starting.

| Tool | Version | Install |
|------|---------|---------|
| `gcloud` (Google Cloud SDK) | Latest | https://cloud.google.com/sdk/docs/install |
| Terraform | >= 1.5.0 | https://developer.hashicorp.com/terraform/downloads |
| GitHub CLI | Latest | https://cli.github.com/ |
| Python | 3.11 | https://www.python.org/downloads/ |
| Poetry | 1.8.4 | `pip install "poetry==1.8.4"` |
| Docker | Latest | https://docs.docker.com/get-docker/ |
| Git | Latest | https://git-scm.com/ |

**You also need:**
- A GCP project with billing enabled
- `Owner` role on the GCP project
- A GitHub account with this repository

---

## Step 1 — Authenticate with Google Cloud

```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT_ID
```

Verify:
```powershell
gcloud config get project
# Expected: YOUR_GCP_PROJECT_ID
```

---

## Step 2 — Clone and Set Up Python Environment

```powershell
git clone https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME/final-development-llmops

pip install "poetry==1.8.4"
poetry install

# Verify
python -c "from llmops_pipeline.pipelines import JobKind; print('OK')"
```

---

## Step 3 — Bootstrap GCP Infrastructure (One-Time Only)

> **Already bootstrapped before?** Skip to Step 4.

This creates: Workload Identity Federation, Service Accounts, Artifact Registry,
and Terraform state bucket. Run this once from your local machine.

```powershell
cd terraform/bootstrap
Copy-Item terraform.tfvars.example terraform.tfvars
```

Edit `terraform/bootstrap/terraform.tfvars`:
```hcl
project_id       = "<YOUR_GCP_PROJECT_ID>"
region           = "us-central1"
environment      = "dev"
repository_owner = "<YOUR_GITHUB_USERNAME>"
repository_name  = "<YOUR_REPO_NAME>"
```

```powershell
terraform init
terraform plan
terraform apply
```

Save the output — you need it in Step 4:
```powershell
terraform output
```

---

## Step 4 — Configure GitHub Secrets and Variables

The bootstrap output gives you exact `gh` commands. Copy and paste them.
Here is the full set (replace `<value>` with actual bootstrap output values):

```powershell
gh auth login

# Variables (non-sensitive)
gh variable set GCP_PROJECT_ID         --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "YOUR_GCP_PROJECT_ID"
gh variable set GCP_REGION             --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "us-central1"
gh variable set ARTIFACT_REGISTRY_REPO --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "llmops-agent-dev"
gh variable set IMAGE_NAME             --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "llmops-agent"
gh variable set CLOUD_RUN_SERVICE_DEV  --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "llmops-agent-dev"
gh variable set TERRAFORM_STATE_BUCKET --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "<terraform output terraform_state_bucket>"

# Secrets (sensitive)
gh secret set WIF_PROVIDER          --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "<terraform output wif_provider>"
gh secret set WIF_SERVICE_ACCOUNT   --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "<terraform output cicd_service_account>"
gh secret set AGENT_SERVICE_ACCOUNT --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "<terraform output agent_service_account>"
gh secret set GCS_BUCKET            --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME --body "YOUR_GCP_PROJECT_ID-llmops-dev"
```

Verify:
```powershell
gh variable list --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME
gh secret list   --repo YOUR_GITHUB_USERNAME/YOUR_REPO_NAME
```

---

## Step 5 — Apply Main Terraform Infrastructure

```powershell
cd terraform/main   # from project root
```

Verify `terraform.tfvars` has the correct values (it should already be correct):
```hcl
project_id                  = "<YOUR_GCP_PROJECT_ID>"
region                      = "us-central1"
environment                 = "dev"
agent_service_account_email = "llmops-agent-dev@<YOUR_GCP_PROJECT_ID>.iam.gserviceaccount.com"
cicd_service_account_email  = "llmops-cicd-dev@<YOUR_GCP_PROJECT_ID>.iam.gserviceaccount.com"
docker_image                = ""       # Leave blank — CI/CD sets this
monitoring_pipeline_enabled = false    # Keep false until Step 9
```

```powershell
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

Type `yes` when prompted.

**What gets created:**
- Cloud Run service `llmops-agent-dev` (placeholder image, auth-only)
- GCS bucket `<YOUR_GCP_PROJECT_ID>-llmops-dev`
- BigQuery dataset `llmops` with 4 tables (interactions, feedback, evaluations, costs)
- Secret Manager secrets (empty shells — no values yet)
- Cloud Monitoring error rate and latency alerts
- Cloud Scheduler job (disabled)

---

## Step 6 — Add API Keys to Secret Manager

The secrets are empty after Terraform creates them. Add values now:

```powershell
# Required: gateway API key (any string you choose — used to authenticate callers)
echo -n "your-api-key-here" | gcloud secrets versions add llmops-api-keys `
  --project=YOUR_GCP_PROJECT_ID --data-file=-

# Optional: OpenAI key (only if routing to OpenAI models)
echo -n "sk-..." | gcloud secrets versions add llmops-openai-key `
  --project=YOUR_GCP_PROJECT_ID --data-file=-

# Optional: Anthropic key (only if routing to Claude models)
echo -n "sk-ant-..." | gcloud secrets versions add llmops-anthropic-key `
  --project=YOUR_GCP_PROJECT_ID --data-file=-
```

Verify:
```powershell
gcloud secrets versions list llmops-api-keys --project=YOUR_GCP_PROJECT_ID
# Should show: VERSION  STATE=enabled
```

---

## Step 7 — Enable Secret Injection into Cloud Run

Open `terraform/main/main.tf` and find the commented-out `LLMOPS_API_KEYS` env block.
Uncomment it:

```hcl
env {
  name = "LLMOPS_API_KEYS"
  value_source {
    secret_key_ref {
      secret  = google_secret_manager_secret.api_keys.secret_id
      version = "latest"
    }
  }
}
```

Re-apply:
```powershell
# From terraform/main/
terraform apply -var-file=terraform.tfvars
```

---

## Step 8 — Deploy Application via CI/CD

Push to `main` to trigger the full CI/CD pipeline:
build image → push to Artifact Registry → deploy to Cloud Run.

```powershell
# From project root (final-development-llmops/)
git add -A
git commit -m "feat: initial deployment"
git push origin main
```

Watch it run at:
```
https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME/actions
```

**CI/CD stages (automatic):**
1. Lint & Test (ruff + pytest)
2. Build (Docker image pushed to Artifact Registry)
3. Deploy (Cloud Run updated with real image)

After the pipeline is green, Cloud Run is serving your application.

---

## Step 9 — Verify the Deployment

```powershell
# Check Cloud Run service status
gcloud run services describe llmops-agent-dev --region=YOUR_GCP_REGION

# Get the service URL
$URL = gcloud run services describe llmops-agent-dev `
  --region=YOUR_GCP_REGION --format="value(status.url)"

# Call health endpoint (auth is required — use identity token)
$TOKEN = gcloud auth print-identity-token
curl -H "Authorization: Bearer $TOKEN" "$URL/health"
# Expected: {"status": "ok", ...}

# Send a test chat message
curl -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -X POST "$URL/chat" `
  -d '{"message": "Hello", "session_id": "test-1"}'
```

---

## Step 10 — Upload KFP Pipelines and Enable Scheduler

Compile and upload all pipeline YAMLs to GCS:

```powershell
# From project root
poetry run python -m kfp_pipelines.compile_and_run `
  --pipeline monitoring `
  --project YOUR_GCP_PROJECT_ID `
  --bucket YOUR_GCP_PROJECT_ID-llmops-dev `
  --location YOUR_GCP_REGION `
  --service-account llmops-agent-dev@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com
```

Verify the YAML is in GCS:
```powershell
gcloud storage ls gs://YOUR_GCP_PROJECT_ID-llmops-dev/pipelines/
```

Enable the daily monitoring scheduler — edit `terraform/main/terraform.tfvars`:
```hcl
monitoring_pipeline_enabled = true
```

Re-apply:
```powershell
cd terraform/main
terraform apply -var-file=terraform.tfvars
```

---

## Done — Your Pipeline Is Live

| Component | What It Does |
|-----------|-------------|
| **Cloud Run** `llmops-agent-dev` | Serves chat, feedback, manifest endpoints |
| **GCS bucket** | Stores pipeline artifacts, prompts, KFP YAMLs |
| **BigQuery** `llmops` dataset | Logs every interaction, feedback, cost |
| **Secret Manager** | Injects API keys securely into Cloud Run |
| **Cloud Monitoring** | Alerts on >5% errors or >10s latency |
| **Cloud Scheduler** | Runs monitoring pipeline daily at 2am UTC |
| **CI/CD** | Every push to `main` auto-deploys |

---

## Daily Development Workflow

```powershell
# 1. Create feature branch
git checkout main && git pull origin main
git checkout -b feature/my-change

# 2. Make changes, test locally
pytest tests/ -v

# 3. Push — triggers Lint & Test only (no deploy on feature branches)
git push origin feature/my-change

# 4. Open PR on GitHub → merge to main → CI/CD auto-deploys
```

---

## Calling Cloud Run as a Developer

Cloud Run requires authentication — no public access allowed.

```powershell
# Option A: Local proxy (easiest — no token management)
gcloud run services proxy llmops-agent-dev --region=us-central1
# Then call: http://localhost:8080/health, /chat, etc.

# Option B: Identity token in curl
$TOKEN = gcloud auth print-identity-token
$URL = gcloud run services describe llmops-agent-dev --region=YOUR_GCP_REGION --format="value(status.url)"
curl -H "Authorization: Bearer $TOKEN" -X POST "$URL/chat" `
  -H "Content-Type: application/json" `
  -d '{"message": "What is your leave policy?", "session_id": "s1"}'
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `terraform apply` fails: secret has no version | Complete Step 6 first — add a secret version before uncommenting env vars |
| Cloud Run returns 403 Forbidden | Use `gcloud auth print-identity-token` as the Bearer token |
| CI/CD fails: missing required configuration | Re-check Step 4 — all 4 secrets and 5 variables must be set in GitHub |
| Scheduler job failing (pipeline not found) | Complete Step 10 — upload KFP pipeline YAML to GCS first |
| Cloud Run still serving placeholder page | Push to `main` triggers build and deploy; wait for Actions to complete |
| `terraform init` fails with GCS backend error | Using local backend for dev — this is expected; run: `terraform init -reconfigure` |
| Bootstrap fails: WIF pool already exists | Import it: `terraform import google_iam_workload_identity_pool.github projects/PROJECT_ID/locations/global/workloadIdentityPools/gh-actions-pool-dev` |
