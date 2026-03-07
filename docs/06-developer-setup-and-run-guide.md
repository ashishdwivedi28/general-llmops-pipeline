# Developer Setup & Run Guide

> **Document:** Step-by-step instructions to set up, run, and test the LLMOps pipeline locally and on GCP  
> **Author:** Ashish Dwivedi  
> **Last Updated:** March 2026

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Clone & Local Setup](#2-repository-clone--local-setup)
3. [Environment Configuration](#3-environment-configuration)
4. [Running Locally (Development Mode)](#4-running-locally-development-mode)
5. [Running Pipeline Jobs via CLI](#5-running-pipeline-jobs-via-cli)
6. [Running the Serving Layer Locally](#6-running-the-serving-layer-locally)
7. [Running with Docker](#7-running-with-docker)
8. [Running Tests](#8-running-tests)
9. [GCP Bootstrap (One-Time Setup)](#9-gcp-bootstrap-one-time-setup)
10. [Deploying to GCP](#10-deploying-to-gcp)
11. [Submitting Vertex AI Pipelines](#11-submitting-vertex-ai-pipelines)
12. [Running the Admin Dashboard](#12-running-the-admin-dashboard)
13. [Running Monitoring Pipeline](#13-running-monitoring-pipeline)
14. [Running Fine-Tuning Pipeline](#14-running-fine-tuning-pipeline)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Prerequisites

### Software Requirements

| Tool | Version | Purpose | Install |
|------|---------|---------|---------|
| **Python** | 3.11+ | Runtime | [python.org](https://www.python.org/downloads/) |
| **Poetry** | 1.8.4 | Dependency management | `pip install "poetry==1.8.4"` |
| **Google Cloud SDK** | Latest | GCP CLI (`gcloud`) | [cloud.google.com/sdk](https://cloud.google.com/sdk/docs/install) |
| **Terraform** | >= 1.5.0 | Infrastructure as Code | [terraform.io](https://www.terraform.io/downloads) |
| **Docker** | Latest | Containerization | [docker.com](https://docs.docker.com/get-docker/) |
| **Git** | Latest | Version control | [git-scm.com](https://git-scm.com/) |
| **GitHub CLI** | Latest | Repo/secrets setup | `winget install GitHub.cli` |

### GCP Requirements

- A GCP project with billing enabled
- Owner or Editor role on the project (for bootstrap)
- The following APIs will be enabled automatically by Terraform bootstrap:
  - Vertex AI, Cloud Run, Cloud Storage, BigQuery, Secret Manager, Cloud Logging, Artifact Registry, IAM, Cloud Trace, Cloud Monitoring, Cloud DLP

---

## 2. Repository Clone & Local Setup

```powershell
# Clone the repository
git clone https://github.com/ashishdwivedi28/general-llmops-pipeline.git
cd general-llmops-pipeline/final-development-llmops

# Install Python dependencies with Poetry
poetry install

# Activate the virtual environment
poetry shell
```

**On Windows (PowerShell):**
```powershell
# If poetry shell doesn't work, activate manually:
.\.venv\Scripts\Activate.ps1
```

**On macOS/Linux:**
```bash
source .venv/bin/activate
```

### Verify Installation

```bash
# Check the CLI is available
llmops --help

# Check Python imports work
python -c "from llmops_pipeline.pipelines import JobKind; print('OK')"

# Run lint check
ruff check src/ serving/ kfp_pipelines/
```

---

## 3. Environment Configuration

### Step 1: Create your `.env` file

```powershell
# Copy the example file
Copy-Item .env.example .env
```

### Step 2: Fill in the values

Open `.env` in your editor and fill in:

```dotenv
# ---- REQUIRED ----
GCP_PROJECT_ID=your-actual-gcp-project-id
GCP_LOCATION=us-central1
GCS_BUCKET=your-actual-gcp-project-id-llmops-dev

# ---- Agent ----
AGENT_NAME=llmops-rag-agent
MODEL_NAME=gemini-2.0-flash
EMBEDDING_MODEL=text-embedding-004

# ---- Manifest (enables offline→online bridge) ----
MANIFEST_ENABLED=true
MANIFEST_APP_ID=llmops-app
MANIFEST_BUCKET=your-actual-gcp-project-id-llmops-dev
MANIFEST_REFRESH_INTERVAL=120

# ---- Prompt Registry ----
PROMPT_REGISTRY_ENABLED=true
PROMPT_ACTIVE_VERSION=1

# ---- BigQuery ----
BQ_DATASET=llmops

# ---- Gateway (disable for local dev) ----
GATEWAY_CONFIG_PATH=confs/gateway.yaml
SECRET_MANAGER_ENABLED=false
```

### Step 3: Authenticate with GCP

```bash
# Login to GCP
gcloud auth login

# Set your project
gcloud config set project YOUR_PROJECT_ID

# Set Application Default Credentials (for local Python SDK calls)
gcloud auth application-default login
```

---

## 4. Running Locally (Development Mode)

### Minimal Local Run (No GCP)

The pipeline supports "local fallback" mode — all GCS operations fall back to local filesystem when `GCS_BUCKET` is empty or set to `__local__`:

```dotenv
# In .env — set these for offline development:
GCS_BUCKET=__local__
MANIFEST_ENABLED=true
MANIFEST_BUCKET=__local__
SECRET_MANAGER_ENABLED=false
```

This mode writes manifests to `.manifests/` and prompts to `.prompts/` in the project directory.

---

## 5. Running Pipeline Jobs via CLI

The unified CLI `llmops` accepts YAML config files and runs the corresponding pipeline job:

### Feature Engineering Pipeline (Phase 1)

```bash
# Ingest documents → chunk → embed → create vector index → update manifest
llmops confs/feature_engineering.yaml
```

### Deployment Pipeline (Phase 2+3)

```bash
# Generate eval set → evaluate prompts/models → quality gate → deploy
llmops confs/deployment.yaml
```

### Monitoring Pipeline (Phase 4)

```bash
# Evaluate production quality → diagnose → remediate
llmops confs/monitoring.yaml
```

### Fine-Tuning Pipeline (Phase 5)

```bash
# Prepare dataset → fine-tune → evaluate → (optional deploy)
llmops confs/fine_tuning.yaml
```

### Override Config Values

You can override any config value inline:

```bash
# Override the model and evaluation threshold
llmops confs/deployment.yaml -e "job.active_model=gemini-1.5-pro" -e "job.relevance_threshold=0.80"

# Override GCS bucket for testing
llmops confs/feature_engineering.yaml -e "job.gcs_bucket=__local__"
```

### View Config Schema

```bash
# Print the JSON Schema for all valid job types
llmops --schema
```

---

## 6. Running the Serving Layer Locally

### Start the FastAPI Server

```bash
# Method 1: Direct uvicorn
python -m uvicorn serving.server:app --host 0.0.0.0 --port 8080 --reload

# Method 2: Via Python module
python -m serving.server
```

### Test Endpoints

```bash
# Health check
curl http://localhost:8080/health

# Readiness check
curl http://localhost:8080/ready

# Chat
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the leave policy?", "session_id": "test-1"}'

# Feedback
curl -X POST http://localhost:8080/feedback \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-1", "rating": 5, "comment": "Great answer!"}'

# View manifest
curl http://localhost:8080/manifest

# View cost summary
curl http://localhost:8080/costs
```

**PowerShell equivalents:**
```powershell
# Health check
Invoke-RestMethod http://localhost:8080/health

# Chat
Invoke-RestMethod -Uri http://localhost:8080/chat -Method POST -ContentType "application/json" -Body '{"query": "What is the leave policy?", "session_id": "test-1"}'
```

---

## 7. Running with Docker

### Build & Run

```powershell
# Build the Docker image
docker build -t llmops-agent .

# Run with environment variables
docker run -p 8080:8080 --env-file .env llmops-agent
```

### Using Docker Compose

```powershell
# Start the service (builds if needed)
docker compose up --build

# Run in background
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

---

## 8. Running Tests

### Run All Tests

```bash
# Full test suite
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=src --cov=serving --cov-report=term-missing
```

### Run Specific Test Files

```bash
# Unit tests — pipeline config validation
pytest tests/test_config.py -v

# Unit tests — manifest system
pytest tests/test_manifest.py -v

# Unit tests — model router
pytest tests/test_model_router.py -v

# Unit tests — prompt registry
pytest tests/test_prompt_registry.py -v

# Integration tests — serving endpoints
pytest tests/test_endpoints.py -v

# Integration tests — pipeline jobs (mocked)
pytest tests/test_pipelines_mock.py -v

# Tests — evaluation, task detection, canary
pytest tests/test_evaluation.py -v
```

### Run Lint

```bash
# Check for lint issues
ruff check src/ serving/ kfp_pipelines/

# Auto-fix lint issues
ruff check --fix src/ serving/ kfp_pipelines/

# Format check
ruff format --check src/ serving/ kfp_pipelines/

# Auto-format
ruff format src/ serving/ kfp_pipelines/
```

---

## 9. GCP Bootstrap (One-Time Setup)

This creates all the foundation GCP infrastructure. **Run this once per environment.**

### Step 1: Configure Terraform Variables

```powershell
cd terraform/bootstrap
Copy-Item terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:
```hcl
project_id       = "your-gcp-project-id"
region           = "us-central1"
environment      = "dev"
repository_owner = "your-github-username"
repository_name  = "general-llmops-pipeline"
```

### Step 2: Run Terraform Bootstrap

```bash
terraform init
terraform plan    # Review what will be created
terraform apply   # Type "yes" to confirm
```

**What this creates:**
| Resource | Purpose |
|----------|---------|
| Workload Identity Federation (WIF) | Keyless auth for GitHub Actions → GCP |
| Artifact Registry | Docker image storage |
| GCS Bucket (state) | Terraform remote state |
| Agent Service Account | Cloud Run runtime identity |
| CI/CD Service Account | GitHub Actions identity |
| 16 GCP APIs | Required services enabled |

### Step 3: Configure GitHub Repository

After `terraform apply`, run the output commands:

```bash
# Get the commands
terraform output setup_instructions

# Run each command (example):
gh variable set GCP_PROJECT_ID --repo your-user/general-llmops-pipeline --body "your-project-id"
gh variable set GCP_REGION --repo your-user/general-llmops-pipeline --body "us-central1"
gh variable set ARTIFACT_REGISTRY_REPO --repo your-user/general-llmops-pipeline --body "llmops-agent-dev"
gh variable set IMAGE_NAME --repo your-user/general-llmops-pipeline --body "llmops-agent"
gh variable set CLOUD_RUN_SERVICE_DEV --repo your-user/general-llmops-pipeline --body "llmops-agent-dev"

gh secret set WIF_PROVIDER --repo your-user/general-llmops-pipeline --body "<terraform output wif_provider>"
gh secret set WIF_SERVICE_ACCOUNT --repo your-user/general-llmops-pipeline --body "<terraform output cicd_service_account>"
gh secret set AGENT_SERVICE_ACCOUNT --repo your-user/general-llmops-pipeline --body "<terraform output agent_service_account>"
gh secret set GCS_BUCKET --repo your-user/general-llmops-pipeline --body "your-project-id-llmops-dev"
```

### Step 4: Apply Main Terraform

```bash
cd ../main

# Initialize with remote state backend
terraform init -backend-config="bucket=$(terraform -chdir=../bootstrap output -raw terraform_state_bucket)"

terraform plan -var="project_id=YOUR_PROJECT" -var="agent_service_account_email=$(terraform -chdir=../bootstrap output -raw agent_service_account)"
terraform apply
```

**What this creates:**
| Resource | Purpose |
|----------|---------|
| GCS Bucket (`{project}-llmops-dev`) | Pipeline artifacts, documents, manifests |
| Cloud Run Service | Hosts the serving layer |
| BigQuery Dataset + 4 Tables | interactions, feedback, evaluations, costs |
| Secret Manager Secrets | API keys, provider keys |
| Cloud Scheduler Jobs | Daily monitoring, weekly master pipeline |
| Cloud Monitoring Alerts | Error rate, latency, quality degradation |
| API Gateway (optional) | Production auth + rate limiting |

---

## 10. Deploying to GCP

### Automatic Deployment (CI/CD)

Once GitHub is configured (Step 9), deployment is automatic:

```bash
# 1. Create a feature branch
git checkout -b feature/my-change

# 2. Make changes, commit, push
git add -A
git commit -m "feat: my improvement"
git push origin feature/my-change

# 3. Open a PR on GitHub (feature/my-change → main)
#    CI runs: Lint → Test
#    If green ✅ → Merge the PR

# 4. Merging to main triggers: Lint → Test → Build → Deploy to Cloud Run
```

### Manual Deployment (Local)

```bash
# Build Docker image
docker build -t us-central1-docker.pkg.dev/YOUR_PROJECT/llmops-agent-dev/llmops-agent:latest .

# Push to Artifact Registry
docker push us-central1-docker.pkg.dev/YOUR_PROJECT/llmops-agent-dev/llmops-agent:latest

# Deploy to Cloud Run
gcloud run deploy llmops-agent-dev \
  --image=us-central1-docker.pkg.dev/YOUR_PROJECT/llmops-agent-dev/llmops-agent:latest \
  --region=us-central1 \
  --platform=managed \
  --port=8080 \
  --service-account=llmops-agent-dev@YOUR_PROJECT.iam.gserviceaccount.com \
  --set-env-vars=GCP_PROJECT_ID=YOUR_PROJECT,GCP_LOCATION=us-central1,GCS_BUCKET=YOUR_PROJECT-llmops-dev,BQ_DATASET=llmops \
  --allow-unauthenticated
```

---

## 11. Submitting Vertex AI Pipelines

### Compile All Pipelines (Local Check)

```bash
# Compile to YAML (no submission) — verifies KFP definitions are valid
python -m kfp_pipelines.compile_and_run --compile-only
```

This creates `compiled_pipelines/` directory with:
- `feature_engineering_pipeline.yaml`
- `deployment_pipeline.yaml`
- `monitoring_pipeline.yaml`
- `master_pipeline.yaml`

### Submit a Specific Pipeline

```bash
# Submit the master pipeline (runs all phases)
python -m kfp_pipelines.compile_and_run \
  --pipeline master \
  --project YOUR_PROJECT_ID \
  --bucket YOUR_PROJECT_ID-llmops-dev \
  --location us-central1 \
  --service-account llmops-agent-dev@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Submit only feature engineering
python -m kfp_pipelines.compile_and_run \
  --pipeline feature_engineering \
  --project YOUR_PROJECT_ID \
  --bucket YOUR_PROJECT_ID-llmops-dev \
  --location us-central1

# Submit only monitoring
python -m kfp_pipelines.compile_and_run \
  --pipeline monitoring \
  --project YOUR_PROJECT_ID \
  --bucket YOUR_PROJECT_ID-llmops-dev \
  --location us-central1
```

### Via CI/CD (GitHub Actions)

Use `workflow_dispatch` to trigger from GitHub:
1. Go to **Actions** tab → **CI/CD Pipeline** workflow
2. Click **Run workflow**
3. Select environment: `dev`
4. Check **Submit Vertex AI Pipeline after deploy?**: `true`
5. Click **Run workflow**

---

## 12. Running the Admin Dashboard

```bash
# Install dashboard dependencies
poetry install --with dashboard

# Set the serving URL
$env:SERVING_URL = "http://localhost:8080"  # or your Cloud Run URL

# Run the Streamlit dashboard
streamlit run dashboard/app.py --server.port 8501
```

Open `http://localhost:8501` in your browser. The dashboard has 6 pages:
- **Overview**: System health, manifest version, active model
- **Pipeline Manifest**: Full artifact manifest details
- **Cost Analytics**: Token usage and costs per model/app
- **Monitoring Scores**: Quality metric trends
- **Model Configuration**: Active model, routing, failover
- **Feedback Analytics**: User feedback trends

---

## 13. Running Monitoring Pipeline

### Manually

```bash
llmops confs/monitoring.yaml
```

### Scheduled (Automatic)

The monitoring pipeline runs automatically via:
1. **Cloud Scheduler** — Triggers daily at 2 AM UTC
2. **GitHub Actions cron** — Runs daily at 2 AM UTC as a fallback

Both submit the monitoring pipeline to Vertex AI Pipelines.

---

## 14. Running Fine-Tuning Pipeline

```bash
# Requirements:
# 1. At least 100 rated interactions in BigQuery (min_samples)
# 2. Production feedback with rating >= 4

llmops confs/fine_tuning.yaml
```

The pipeline will:
1. Query BigQuery for high-quality interactions
2. Format as JSONL and upload to GCS
3. Submit a Vertex AI supervised fine-tuning job
4. Evaluate fine-tuned model vs base model
5. Write results to the pipeline manifest

---

## 15. Troubleshooting

### Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `Import "pydantic" could not be resolved` | VS Code not using Poetry venv | Select Python interpreter: `.venv/Scripts/python.exe` |
| `gcloud: command not found` | Google Cloud SDK not installed | Install from cloud.google.com/sdk |
| `poetry install` fails | Python version mismatch | Ensure Python 3.11+ |
| `Permission denied` on GCS | Not authenticated | Run `gcloud auth application-default login` |
| `/ready` returns 503 | Agent still initializing | Wait 30-60s; check logs for errors |
| `WIF_PROVIDER not set` | GitHub secrets missing | Run Step 9.3 commands |
| Docker build fails | Poetry lockfile stale | Run `poetry lock` then rebuild |
| Pipeline submission fails | Service account lacks permissions | Check Agent SA has `aiplatform.user` role |

### Useful Debug Commands

```bash
# Check GCP auth
gcloud auth list

# Check project config
gcloud config list

# Check Cloud Run logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=llmops-agent-dev" --limit=50 --format=json

# Check Vertex AI Pipeline runs
gcloud ai pipelines list --project=YOUR_PROJECT --region=us-central1

# Check BigQuery tables
bq ls llmops

# Check GCS bucket contents
gsutil ls gs://YOUR_PROJECT-llmops-dev/manifests/
```

---

## Quick Start Summary

```bash
# 1. Clone & install
git clone <repo-url> && cd final-development-llmops
poetry install && poetry shell

# 2. Configure
cp .env.example .env  # edit with your values
gcloud auth application-default login

# 3. Run locally
python -m uvicorn serving.server:app --port 8080

# 4. Test
pytest tests/ -v
curl http://localhost:8080/health

# 5. Deploy (via Git)
git checkout -b feature/xyz
git add -A && git commit -m "feat: xyz" && git push origin feature/xyz
# Open PR → Merge → Auto-deploys to Cloud Run
```
