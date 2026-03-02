# Complete Setup Guide

Step-by-step guide to deploy the LLMOps pipeline from scratch on Google Cloud Platform.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [GCP Project Setup](#2-gcp-project-setup)
3. [Local Development Setup](#3-local-development-setup)
4. [Infrastructure with Terraform](#4-infrastructure-with-terraform)
5. [CI/CD Setup (GitHub Actions)](#5-cicd-setup-github-actions)
6. [Running Pipelines](#6-running-pipelines)
7. [Deploying the Agent](#7-deploying-the-agent)
8. [Monitoring & Operations](#8-monitoring--operations)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

### Tools Required
| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Runtime |
| Poetry | 1.8+ | Dependency management |
| gcloud CLI | Latest | GCP authentication + management |
| Terraform | 1.5+ | Infrastructure provisioning |
| Docker | Latest | Container builds |
| Git | Latest | Version control |

### Accounts Required
- Google Cloud Platform account with billing enabled
- GitHub account (for CI/CD)

---

## 2. GCP Project Setup

### 2.1 Create or Select Project

```bash
# Create new project
gcloud projects create llmops-pipeline-project --name="LLMOps Pipeline"

# Or select existing
gcloud config set project YOUR_PROJECT_ID
```

### 2.2 Enable Billing

```bash
# Link billing account
gcloud billing projects link YOUR_PROJECT_ID --billing-account=BILLING_ACCOUNT_ID
```

### 2.3 Enable Required APIs

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  cloudresourcemanager.googleapis.com \
  run.googleapis.com \
  compute.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  cloudtrace.googleapis.com \
  bigquery.googleapis.com \
  dlp.googleapis.com
```

### 2.4 Set Default Region

```bash
gcloud config set compute/region us-central1
gcloud config set run/region us-central1
```

### 2.5 Authenticate

```bash
# For local development
gcloud auth login
gcloud auth application-default login

# Verify
gcloud config list
```

---

## 3. Local Development Setup

### 3.1 Clone and Install

```bash
git clone <your-repo-url>
cd final-development-llmops

# Install Poetry (if not installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install
```

### 3.2 Configure Environment

```bash
# Copy template
cp .env.example .env

# Edit .env with your values
# Required fields:
#   GCP_PROJECT_ID=your-project-id
#   GCP_LOCATION=us-central1
#   GCS_BUCKET=your-bucket-name
```

### 3.3 Create GCS Bucket (Manual)

```bash
gsutil mb -l us-central1 gs://YOUR_PROJECT_ID-llmops-dev
```

### 3.4 Upload Documents

Place your source documents (PDF, TXT, DOCX) in `data/documents/`, then upload:

```bash
gsutil -m cp -r data/documents/* gs://YOUR_BUCKET/documents/
```

### 3.5 Test Local Setup

```bash
# Verify config parsing works
poetry run python -c "
from llmops_pipeline.io.configs import parse_file
cfg = parse_file('confs/feature_engineering.yaml')
print(cfg)
"

# Verify settings dispatch
poetry run python -c "
from llmops_pipeline.settings import MainSettings
s = MainSettings(job={'KIND': 'FeatureEngineeringJob', 'project': 'test'})
print(type(s.job).__name__)
"
```

---

## 4. Infrastructure with Terraform

### 4.1 Configure Terraform

```bash
cd terraform

# Copy template
cp terraform.tfvars.example terraform.tfvars

# Edit terraform.tfvars:
#   project_id  = "your-project-id"
#   region      = "us-central1"
#   environment = "dev"
#   github_repo = "your-org/your-repo"  # Optional: for WIF
```

### 4.2 Create Terraform State Bucket

```bash
gsutil mb -l us-central1 gs://YOUR_PROJECT_ID-tf-state
```

### 4.3 Initialize and Apply

```bash
# Initialize with remote state
terraform init -backend-config="bucket=YOUR_PROJECT_ID-tf-state"

# Preview changes
terraform plan

# Apply
terraform apply
```

### 4.4 Verify Resources

```bash
# Check outputs
terraform output

# Expected outputs:
#   cloud_run_url        = "https://llmops-agent-dev-xxxxx.run.app"
#   gcs_bucket           = "your-project-llmops-dev"
#   artifact_registry    = "us-central1-docker.pkg.dev/your-project/llmops-agent-dev"
#   agent_service_account = "llmops-agent-dev@your-project.iam.gserviceaccount.com"
```

### What Terraform Creates

| Resource | Name | Purpose |
|----------|------|---------|
| GCS Bucket | `{project}-llmops-{env}` | Pipeline data storage |
| Artifact Registry | `llmops-agent-{env}` | Docker images |
| Service Account (Agent) | `llmops-agent-{env}` | Runtime identity |
| Service Account (CI/CD) | `llmops-cicd-{env}` | Deployment identity |
| Cloud Run Service | `llmops-agent-{env}` | Agent serving |
| WIF Pool + Provider | `github-pool-{env}` | GitHub Actions auth |
| 15 API enablements | Various | Required GCP APIs |

---

## 5. CI/CD Setup (GitHub Actions)

### 5.1 Set GitHub Secrets

Go to your GitHub repository → Settings → Secrets and variables → Actions.

| Secret Name | Value | Source |
|------------|-------|--------|
| `GCP_PROJECT_ID` | Your GCP project ID | GCP Console |
| `GCP_REGION` | `us-central1` | Your choice |
| `WIF_PROVIDER` | WIF provider resource name | `terraform output wif_provider` |
| `WIF_SERVICE_ACCOUNT` | CI/CD service account email | `terraform output` |
| `ARTIFACT_REGISTRY` | Registry URL | `terraform output artifact_registry` |
| `GCS_BUCKET` | Bucket name | `terraform output gcs_bucket` |

### 5.2 Configure Environments

Go to Settings → Environments and create:

| Environment | Protection Rules |
|------------|-----------------|
| `dev` | None (auto-deploy on `develop` push) |
| `staging` | None (auto-deploy on `main` push) |
| `prod` | Required reviewers (add team leads) |

### 5.3 CI/CD Flow

```
Push to develop → Lint → Build → Deploy to dev
Push to main    → Lint → Build → Deploy to staging
Manual trigger  → Lint → Build → Deploy to staging → (approval) → Deploy to prod
```

### 5.4 Test CI/CD

```bash
git checkout -b develop
git push origin develop
# Watch GitHub Actions tab for pipeline execution
```

---

## 6. Running Pipelines

### 6.1 Run Locally (CLI)

```bash
# Feature Engineering Pipeline
poetry run llmops confs/feature_engineering.yaml

# Deployment Pipeline
poetry run llmops confs/deployment.yaml

# Monitoring Pipeline
poetry run llmops confs/monitoring.yaml

# Generate QA Dataset
poetry run llmops confs/generate_dataset.yaml
```

### 6.2 Run on Vertex AI Pipelines

```bash
# Compile all pipelines to YAML
python -m kfp_pipelines.compile_and_run --compile-only

# Submit master pipeline to Vertex AI
python -m kfp_pipelines.compile_and_run \
  --pipeline master \
  --project $GCP_PROJECT_ID \
  --bucket $GCS_BUCKET

# Submit individual pipeline
python -m kfp_pipelines.compile_and_run \
  --pipeline feature_engineering \
  --project $GCP_PROJECT_ID \
  --bucket $GCS_BUCKET
```

### 6.3 Run via CI/CD

Use GitHub Actions workflow dispatch:
1. Go to Actions → CI/CD Pipeline → Run workflow
2. Select environment: `dev`
3. Check "Submit Vertex AI Pipeline after deploy?" if needed
4. Click "Run workflow"

### 6.4 Pipeline Execution Order

The **Master Pipeline** runs everything in order:

```
1. Feature Engineering (if enabled)
   ├── Create Vector Search Index
   └── Ingest Documents (chunk → embed → upload)
       ↓
2. Deployment (if enabled)
   ├── Register Model in Registry (staging label)
   ├── Evaluate with Gemini-as-Judge
   └── Promote to production (if quality gates pass)
       ↓
3. Monitoring (if enabled)
   ├── Pull production logs from Cloud Logging
   ├── Evaluate quality with Gemini-as-Judge
   └── If degraded → Re-trigger Feature Engineering
```

---

## 7. Deploying the Agent

### 7.1 Local Testing

```bash
# Start the agent server
poetry run python -m serving.server

# Test with client
poetry run python serving/client.py --query "What is the leave policy?"

# Or with curl
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Hello", "session_id": "test"}'
```

### 7.2 Docker Build

```bash
# Build
docker build -t llmops-agent:latest .

# Run
docker run -p 8080:8080 --env-file .env llmops-agent:latest

# Or use docker-compose
docker compose up
```

### 7.3 Deploy to Cloud Run (Manual)

```bash
# Tag and push to Artifact Registry
REGISTRY=$(terraform -chdir=terraform output -raw artifact_registry)
docker tag llmops-agent:latest $REGISTRY/agent:latest
docker push $REGISTRY/agent:latest

# Deploy
gcloud run deploy llmops-agent-dev \
  --image $REGISTRY/agent:latest \
  --region us-central1 \
  --service-account llmops-agent-dev@$GCP_PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT_ID=$GCP_PROJECT_ID,GCP_LOCATION=us-central1
```

### 7.4 Verify Deployment

```bash
# Get Cloud Run URL
URL=$(gcloud run services describe llmops-agent-dev --region us-central1 --format="value(status.url)")

# Health check
curl $URL/health

# Test chat
curl -X POST $URL/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Hello, what can you help me with?"}'
```

---

## 8. Monitoring & Operations

### 8.1 View Pipeline Runs

```bash
# List pipeline runs
gcloud ai pipeline-jobs list --region us-central1

# View specific run
gcloud ai pipeline-jobs describe PIPELINE_JOB_ID --region us-central1
```

Or visit: **Vertex AI Console → Pipelines**

### 8.2 View Experiment Metrics

Visit: **Vertex AI Console → Experiments**

Each pipeline run logs metrics:
- Feature Engineering: `num_documents`, `num_chunks`
- Deployment: `avg_answer_relevance`, `avg_faithfulness`, `avg_toxicity`, `decision`
- Monitoring: `answer_relevance`, `faithfulness`, `toxicity`, `degraded`

### 8.3 View Cloud Run Logs

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="llmops-agent-dev"' \
  --limit 50 --format json
```

Or visit: **Cloud Console → Logging → Log Explorer**

### 8.4 Set Up Alerts

Create alerts in Cloud Monitoring for:
- Cloud Run error rate > 1%
- Cloud Run latency (p95) > 5s
- Pipeline failure notifications
- Monitoring pipeline degradation signal

### 8.5 Scheduled Monitoring

Set up a Cloud Scheduler job to trigger the monitoring pipeline periodically:

```bash
gcloud scheduler jobs create http monitoring-trigger \
  --schedule="0 6 * * *" \
  --uri="https://us-central1-aiplatform.googleapis.com/v1/projects/$GCP_PROJECT_ID/locations/us-central1/pipelineJobs" \
  --http-method=POST \
  --message-body='{"displayName":"scheduled-monitoring","templatePath":"gs://$GCS_BUCKET/compiled_pipelines/monitoring_pipeline.yaml"}' \
  --oauth-service-account-email=llmops-agent-dev@$GCP_PROJECT_ID.iam.gserviceaccount.com
```

---

## 9. Troubleshooting

### Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `Permission denied` on Vertex AI | Missing IAM roles | Run Terraform again or manually add `roles/aiplatform.user` |
| `Index creation timeout` | Vector Search takes 30-60 min | Normal — wait for completion |
| `Cloud Run 503` | Cold start or OOM | Increase memory in Terraform, set min instances |
| `KFP component install error` | Package not in `packages_to_install` | Add missing package to `@dsl.component` decorator |
| `WIF authentication failed` | Wrong pool/provider config | Verify `terraform output wif_provider` matches GitHub secret |
| `Config validation error` | YAML field mismatch | Check `KIND` field matches a registered Job class name |

### Debug Commands

```bash
# Check service account permissions
gcloud projects get-iam-policy $GCP_PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:llmops-agent-dev@"

# Check Cloud Run service status
gcloud run services describe llmops-agent-dev --region us-central1

# Check Vertex AI index status
gcloud ai indexes list --region us-central1

# View recent pipeline failures
gcloud ai pipeline-jobs list --region us-central1 --filter="state=PIPELINE_STATE_FAILED"
```

### Getting Help

1. Check Cloud Run logs for stack traces
2. Check Vertex AI Pipeline run details for component failures
3. Verify all APIs are enabled: `gcloud services list --enabled`
4. Ensure service account has required roles (see `terraform/main.tf`)

---

## Quick Reference Card

```
# Run feature engineering
poetry run llmops confs/feature_engineering.yaml

# Run deployment
poetry run llmops confs/deployment.yaml

# Run monitoring
poetry run llmops confs/monitoring.yaml

# Submit master pipeline to Vertex AI
python -m kfp_pipelines.compile_and_run --pipeline master --project $GCP_PROJECT_ID --bucket $GCS_BUCKET

# Start local agent
python -m serving.server

# Build + run Docker
docker compose up

# Deploy infrastructure
cd terraform && terraform apply

# View logs
gcloud logging read 'resource.type="cloud_run_revision"' --limit 20
```
