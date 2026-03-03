# Complete Setup & Deployment Guide

> **Who this is for:** Someone deploying this project to Google Cloud for the first time. No prior cloud experience required. Every step is explained with *why* not just *how*.

---

## You Have the Code — What Is the Next Step?

If you have cloned this project and are asking "where do I even start?", follow this order:

```
You are here: have the code on your machine
        │
        ▼
① Set up GCP project (Section 3) — 15 minutes
        │  Enable billing, enable APIs, authenticate gcloud
        ▼
② Install tools + run lab tests (Section 4 + docs/04-lab-testing-guide.md)
        │  Verify Gemini + GCS work before spending money on full infra
        │  Cost: ~free (lab uses free Gemini API quota)
        ▼
③ Run Terraform (Section 5) — 5 minutes of your time, 3 min for GCP
        │  Creates: Cloud Run, GCS bucket, IAM roles, Docker registry
        ▼
④ Upload your documents + run Pipeline 1 locally (Section 7.1)
        │  Test feature engineering works with YOUR documents
        ▼
⑤ Build + deploy to Cloud Run (Section 8)
        │  Your agent is live on a real HTTPS URL
        ▼
⑥ Submit Pipeline 2 to Vertex AI (Section 7.2)
        │  Quality gate evaluates and promotes to production
        ▼
⑦ Set up CI/CD (Section 6)
        │  Future code pushes auto-deploy
        ▼
⑧ Schedule Pipeline 3 monitoring (Section 10)
           System now self-monitors
```

**Minimum viable start (get something working today):**
```bash
# 1. Enable APIs + authenticate (15 min, one-time)
gcloud auth login && gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com storage.googleapis.com

# 2. Lab test (verify Gemini + GCS work)
export PROJECT_ID=$(gcloud config get-value project)
python lab_test/run_lab_test.py --project $PROJECT_ID --location us-central1 \
  --bucket ${PROJECT_ID}-lab --skip-serving

# 3. Run feature engineering locally (builds knowledge base)
# Edit confs/feature_engineering.yaml with your project ID + bucket
poetry run llmops confs/feature_engineering.yaml

# 4. Start the agent locally
poetry run python -m serving.server
curl http://localhost:8080/health
```

---

## Table of Contents

1. [Before You Start — Understanding What We're Deploying](#1-before-you-start--understanding-what-were-deploying)
2. [Prerequisites — Tools You Need](#2-prerequisites--tools-you-need)
3. [GCP Project Setup](#3-gcp-project-setup)
4. [Local Development Setup](#4-local-development-setup)
5. [Infrastructure with Terraform](#5-infrastructure-with-terraform)
6. [CI/CD Setup — GitHub Actions](#6-cicd-setup--github-actions)
7. [Running the Pipelines](#7-running-the-pipelines)
8. [Deploying the Agent to Cloud Run](#8-deploying-the-agent-to-cloud-run)
9. [Testing Your Production Deployment](#9-testing-your-production-deployment)
10. [Monitoring & Operations](#10-monitoring--operations)
11. [Production Hardening — API Gateway](#11-production-hardening--api-gateway)
12. [Troubleshooting](#12-troubleshooting)
13. [Quick Reference Card](#13-quick-reference-card)

---

## 1. Before You Start — Understanding What We're Deploying

Before running any command, make sure you understand what you are setting up. Read this carefully.

### What Are We Deploying?

We are deploying **two systems**:

**System A — The Pipeline (Offline, Vertex AI)**
Three automated workflows that run in the background:
- **Pipeline 1:** Reads your documents → chunks them → generates embeddings → builds the vector search index
- **Pipeline 2:** Tests answer quality → only deploys if scores pass threshold (quality gate)
- **Pipeline 3:** Continuously monitors production quality → alerts if it degrades

**System B — The Agent (Online, Cloud Run)**
A FastAPI server running 24/7 that:
- Receives user questions via HTTP
- Searches the vector index for relevant context
- Calls Gemini to generate grounded answers
- Applies guardrails (safety filters)
- Returns the answer

### Deployment Order We Will Follow

```
Step 1: Set up GCP project (enable APIs, configure billing)
     ↓
Step 2: Set up local environment (install tools, configure secrets)
     ↓
Step 3: Terraform (creates all GCP infrastructure automatically)
     ↓
Step 4: CI/CD setup (so future code changes auto-deploy)
     ↓
Step 5: Upload documents + run Pipeline 1 (build knowledge base)
     ↓
Step 6: Run Pipeline 2 (evaluate quality + deploy if passes)
     ↓
Step 7: Test the live agent
     ↓
Step 8: Set up monitoring (Pipeline 3 on schedule)
```

Do **not** skip steps. Each step depends on the previous one.

---

## 2. Prerequisites — Tools You Need

### Install These on Your Machine

| Tool | Version | Why Needed | Install |
|---|---|---|---|
| Python | 3.11+ | Runtime for pipeline code | [python.org](https://python.org) |
| Poetry | 1.8+ | Python dependency management | `curl -sSL https://install.python-poetry.org \| python3 -` |
| gcloud CLI | Latest | Authenticate + manage GCP resources | [cloud.google.com/sdk](https://cloud.google.com/sdk/docs/install) |
| Terraform | 1.5+ | Provision GCP infrastructure | [terraform.io](https://developer.hashicorp.com/terraform/install) |
| Docker | Latest | Build container images | [docker.com](https://docs.docker.com/get-docker/) |
| Git | Latest | Version control + CI/CD | Pre-installed on most systems |

### Verify All Installed

```bash
python --version      # should say Python 3.11.x or higher
poetry --version      # should say Poetry 1.8.x or higher
gcloud --version      # should show Google Cloud SDK
terraform --version   # should say Terraform v1.5.x or higher
docker --version      # should say Docker version
git --version         # should show git version
```

### Accounts You Need

- **Google Cloud Platform account** with a project and billing enabled
- **GitHub account** (for CI/CD)

---

## 2.5 Permissions You Need

This section explains **exactly what GCP permissions are required and why**. There are three permission contexts: your personal developer account, the agent service account, and the CI/CD service account.

> **Short answer:** If you have `roles/owner` on the GCP project you can do everything. If your organization restricts that, use the exact role list below.

### Your Personal Google Account (Running Terraform / First-Time Setup)

You need enough permissions to run `terraform apply`, which creates infrastructure and assigns IAM roles.

**Option A — Easiest (for personal projects / lab environments):**
```bash
# Grant yourself project owner — can do everything
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="user:your-email@gmail.com" \
  --role="roles/owner"
```

**Option B — Minimum roles needed (for organizations that restrict Owner):**
```bash
# Paste these one at a time
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/resourcemanager.projectIamAdmin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/serviceusage.serviceUsageAdmin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/storage.admin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/run.admin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/aiplatform.admin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/artifactregistry.admin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/iam.serviceAccountAdmin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/iam.serviceAccountUser"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/bigquery.admin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="user:YOUR_EMAIL" --role="roles/secretmanager.admin"
```

**What each role allows you to do:**

| Role | Why You Need It |
|---|---|
| `roles/resourcemanager.projectIamAdmin` | Grant IAM roles to service accounts (Terraform does this) |
| `roles/serviceusage.serviceUsageAdmin` | Enable GCP APIs (`gcloud services enable ...`) |
| `roles/storage.admin` | Create GCS buckets, upload Terraform state |
| `roles/run.admin` | Deploy Cloud Run services |
| `roles/aiplatform.admin` | Create Vertex AI resources, submit pipelines |
| `roles/artifactregistry.admin` | Create Docker image registry |
| `roles/iam.serviceAccountAdmin` | Create service accounts for the agent + CI/CD |
| `roles/iam.serviceAccountUser` | Impersonate service accounts when deploying |
| `roles/bigquery.admin` | Create BigQuery datasets for logs |
| `roles/secretmanager.admin` | Store API keys securely |

---

### Agent Service Account (Created Automatically by Terraform)

This service account is attached to the **Cloud Run container** that runs your agent. You do NOT configure this manually — `terraform apply` creates it and grants all roles automatically.

**What Terraform creates:**
```hcl
# From terraform/main.tf — these are the exact roles granted:
agent_roles = [
  "roles/aiplatform.user",              # Call Gemini, use Vertex AI Vector Search, submit pipeline jobs
  "roles/storage.objectAdmin",           # Read documents from GCS, write evaluation artifacts
  "roles/logging.logWriter",             # Write structured logs to Cloud Logging
  "roles/cloudtrace.agent",              # Export OpenTelemetry traces for request debugging
  "roles/bigquery.dataEditor",           # Write conversation logs to BigQuery table
  "roles/secretmanager.secretAccessor",  # Read API keys and database passwords from Secret Manager
  "roles/dlp.user",                      # Scan user messages for PII (GDPR compliance)
]
```

**Why each role is needed at runtime:**

| Role | When It Is Used |
|---|---|
| `roles/aiplatform.user` | Every user request — calls Gemini + Vector Search |
| `roles/storage.objectAdmin` | Agent startup — loads schema and document index from GCS |
| `roles/logging.logWriter` | Every user request — structured logs for debugging |
| `roles/cloudtrace.agent` | Every user request — distributed tracing |
| `roles/bigquery.dataEditor` | Background batch — logs Q&A pairs for evaluation |
| `roles/secretmanager.secretAccessor` | Agent startup — reads secrets from Secret Manager |
| `roles/dlp.user` | Every user request — checks for PII before responding |

---

### CI/CD Service Account (Created Automatically by Terraform)

This service account is used by **GitHub Actions** to deploy your code. You do NOT configure this manually.

**What Terraform creates:**
```hcl
# From terraform/main.tf — these are the exact roles granted:
cicd_roles = [
  "roles/run.admin",                    # Deploy a new container image to Cloud Run
  "roles/artifactregistry.writer",       # Push Docker images built by GitHub Actions
  "roles/iam.serviceAccountUser",        # Impersonate agent SA when configuring Cloud Run
  "roles/storage.objectAdmin",           # Upload compiled KFP pipeline YAML to GCS
  "roles/aiplatform.user",              # Submit Vertex AI Pipeline jobs from CI/CD
]
```

---

### Quick Reference — "What permission error do I have?"

| Error Message | Meaning | Fix |
|---|---|---|
| `PERMISSION_DENIED: iam.serviceAccounts.create` | Your account can't create SAs | Add `roles/iam.serviceAccountAdmin` |
| `PERMISSION_DENIED: storage.buckets.create` | Can't create GCS bucket | Add `roles/storage.admin` |
| `PERMISSION_DENIED: aiplatform.jobs.create` | Can't submit pipeline jobs | Add `roles/aiplatform.admin` |
| `PERMISSION_DENIED: run.services.create` | Can't deploy Cloud Run | Add `roles/run.admin` |
| `PERMISSION_DENIED: serviceusage.services.enable` | Can't turn on APIs | Add `roles/serviceusage.serviceUsageAdmin` |
| `PERMISSION_DENIED: resourcemanager.projects.setIamPolicy` | Can't set IAM | Add `roles/resourcemanager.projectIamAdmin` |
| `The caller does not have permission` (generic) | Usually missing role above | Check which resource failed and match to table |

---

## 3. GCP Project Setup

### 3.1 Create or Select Your GCP Project

```bash
# Option A: Create a new project
gcloud projects create llmops-pipeline --name="LLMOps Pipeline"
gcloud config set project llmops-pipeline

# Option B: Use existing project
gcloud config set project YOUR_EXISTING_PROJECT_ID

# Verify your active project
gcloud config get-value project
```

> **What is a GCP project?** Think of it as a "workspace" on Google Cloud. Every resource (Cloud Run, GCS bucket, APIs) lives inside a project. Billing is attached to the project.

### 3.2 Enable Billing

Billing must be enabled for Vertex AI, Cloud Run, and other services to work.

```bash
# List your billing accounts
gcloud billing accounts list

# Link billing to the project (replace BILLING_ACCOUNT_ID with yours)
gcloud billing projects link YOUR_PROJECT_ID --billing-account=BILLING_ACCOUNT_ID

# Verify billing is enabled
gcloud billing projects describe YOUR_PROJECT_ID
```

### 3.3 Enable Required APIs

APIs are features of GCP that need to be "turned on" before you can use them. This is a one-time step.

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
  dlp.googleapis.com \
  apigateway.googleapis.com
```

> **What are APIs?** Each API is a service you're turning on. For example, `aiplatform.googleapis.com` turns on Vertex AI (which includes pipelines, embeddings, vector search). Without enabling it, API calls fail.

This command takes ~2 minutes. You'll see each API being enabled.

### 3.4 Set Default Region

```bash
gcloud config set compute/region us-central1
gcloud config set run/region us-central1
```

> **Why us-central1?** This region has the best availability for Vertex AI services. If you use a different region, make sure Vertex AI Pipelines and Vector Search are available there.

### 3.5 Authenticate gcloud

```bash
# Login to your Google account
gcloud auth login

# Set up Application Default Credentials (ADC) — used by Python SDKs
gcloud auth application-default login

# Verify everything is set correctly
gcloud config list
```

> **What is ADC?** When Python code calls `google-cloud-aiplatform`, it needs credentials. ADC is the automatic way Python finds those credentials — either from your gcloud login, or from a service account key in production.

---

## 4. Local Development Setup

### 4.1 Clone the Repository

```bash
git clone <your-repo-url>
cd final-development-llmops
```

### 4.2 Install Python Dependencies

```bash
# Install all dependencies defined in pyproject.toml
poetry install

# Activate the virtual environment
poetry shell

# Verify
poetry run python -c "import google.cloud.aiplatform; print('OK')"
```

### 4.3 Configure Environment Variables

```bash
# Copy the template
cp .env.example .env

# Open and edit .env
nano .env   # or open in VS Code: code .env
```

Fill in at minimum:
```bash
# Your GCP project ID (from gcloud config get-value project)
GCP_PROJECT_ID=your-project-id

# Region (keep as us-central1 unless you have a reason)
GCP_LOCATION=us-central1

# GCS bucket name (you'll create this next)
GCS_BUCKET=your-project-id-llmops-dev

# Gemini model
MODEL_NAME=gemini-2.0-flash
EMBEDDING_MODEL=text-embedding-004

# Agent config
AGENT_NAME=llmops-rag-agent
```

### 4.4 Create GCS Bucket for Data

```bash
# Create the bucket (replace YOUR_PROJECT_ID)
gsutil mb -l us-central1 gs://YOUR_PROJECT_ID-llmops-dev

# Verify it exists
gsutil ls gs://YOUR_PROJECT_ID-llmops-dev
```

> **What is GCS?** Google Cloud Storage — like Amazon S3 or a massive file server in the cloud. We store documents, embeddings, compiled pipelines, and model artifacts here.

### 4.5 Upload Your Documents

Place your source documents (PDF, TXT, DOCX) in `data/documents/`, then upload:

```bash
# Upload all documents to GCS
gsutil -m cp -r data/documents/* gs://YOUR_PROJECT_ID-llmops-dev/documents/

# Verify upload
gsutil ls gs://YOUR_PROJECT_ID-llmops-dev/documents/
```

> **Why GCS?** The Vertex AI Pipeline containers don't have access to your local machine. They read from and write to GCS. Every document, every embedding, every artifact lives in GCS.

### 4.6 Verify Local Setup

```bash
# Test config parsing
poetry run python -c "
from llmops_pipeline.io.configs import parse_file
cfg = parse_file('confs/feature_engineering.yaml')
print('Config parsed OK:', dict(cfg.job))
"

# Test GCP connectivity
poetry run python -c "
from google.cloud import aiplatform
aiplatform.init(project='YOUR_PROJECT_ID', location='us-central1')
print('GCP connection OK')
"
```

---

## 5. Infrastructure with Terraform

Terraform creates all the GCP resources automatically. One command creates everything.

### What Terraform Will Create

Before running anything, understand what you're creating:

| GCP Resource | Name Pattern | What It Does |
|---|---|---|
| Cloud Storage Bucket | `{project}-llmops-{env}` | Stores all pipeline data and artifacts |
| Artifact Registry | `llmops-agent-{env}` | Stores Docker images for Cloud Run |
| Cloud Run Service | `llmops-agent-{env}` | Runs the FastAPI agent server |
| Service Account (Agent) | `llmops-agent-{env}@` | Identity for Cloud Run to access GCP services |
| Service Account (CI/CD) | `llmops-cicd-{env}@` | Identity for GitHub Actions to deploy |
| IAM Bindings × 7 | Various | Minimum permissions for both service accounts |
| Workload Identity Pool | `github-pool-{env}` | Enables GitHub Actions auth without JSON keys |

### 5.1 Configure Terraform Variables

```bash
cd terraform

# Copy the template
cp terraform.tfvars.example terraform.tfvars

# Edit the variables
nano terraform.tfvars
```

Fill in:
```hcl
# Your GCP project ID
project_id = "your-actual-project-id"

# Region
region = "us-central1"

# Environment (dev for first deployment)
environment = "dev"

# GitHub repo in format "owner/repo-name" (for Workload Identity)
github_repo = "your-github-username/final-development-llmops"
```

### 5.2 Create Terraform State Bucket

Terraform needs a place to save its "memory" (which resources it created). Store it in GCS:

```bash
# Go back up to project directory
cd ..

# Create a dedicated bucket for Terraform state
gsutil mb -l us-central1 gs://YOUR_PROJECT_ID-tf-state
```

> **Why a state bucket?** Terraform remembers what it created in a state file. If you run `terraform apply` twice, it knows what already exists and only creates what's missing. Without remote state, this memory is lost when you close the terminal.

### 5.3 Initialize Terraform

```bash
cd terraform

# Initialize with remote state
terraform init -backend-config="bucket=YOUR_PROJECT_ID-tf-state"
```

You'll see: `Terraform has been successfully initialized!`

### 5.4 Preview What Terraform Will Do

**Always run plan before apply:**

```bash
terraform plan
```

Read through the output. Every `+` means a resource will be created. Every `-` means a resource will be deleted. Every `~` means a resource will be modified. This is just a preview — nothing happens yet.

### 5.5 Apply — Create the Infrastructure

```bash
terraform apply
```

Terraform will show the plan again and ask: `Do you want to perform these actions?`

Type `yes` and press Enter.

This takes **3–5 minutes**. Terraform creates all resources in the right order.

### 5.6 Save the Output Values

After apply completes, Terraform shows output values. **Save these:**

```bash
# View all outputs
terraform output

# Save to a file for reference
terraform output | tee ../terraform_outputs.txt
```

You'll see something like:
```
cloud_run_url        = "https://llmops-agent-dev-abc123.run.app"
gcs_bucket           = "your-project-llmops-dev"
artifact_registry    = "us-central1-docker.pkg.dev/your-project/llmops-agent-dev"
agent_service_account = "llmops-agent-dev@your-project.iam.gserviceaccount.com"
wif_provider         = "projects/123456/locations/global/workloadIdentityPools/github-pool-dev/providers/github"
```

You will need all of these in the next steps.

---

## 6. CI/CD Setup — GitHub Actions

CI/CD (Continuous Integration / Continuous Deployment) means: when you push code to GitHub, it automatically tests, builds, and deploys — without you doing anything manually.

### How the Auth Works (Workload Identity Federation)

Traditional CI/CD needs a JSON key file for a service account. This is a security risk — if the key leaks, an attacker has full access to your GCP project.

Our setup uses **Workload Identity Federation (WIF)** — no JSON keys:

```
GitHub Actions runs a job
        │
        │ "I am GitHub, running workflow for repo owner/repo-name, branch main"
        │ (GitHub proves this with a cryptographic token — unfakeable)
        ▼
Google Cloud verifies:
  ✓ This is indeed GitHub
  ✓ The repo matches what we configured
  ✓ The requesting workflow is allowed
        │
        ▼
Google issues a SHORT-LIVED access token (expires in 1 hour)
        │
        ▼
GitHub Actions can now deploy to Cloud Run
```

No keys. No rotation. No secrets to leak.

### 6.1 Set GitHub Repository Secrets

1. Go to your GitHub repository
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** for each:

| Secret Name | Value | Where to Get It |
|---|---|---|
| `GCP_PROJECT_ID` | `your-project-id` | `gcloud config get-value project` |
| `GCP_REGION` | `us-central1` | Your chosen region |
| `WIF_PROVIDER` | Long WIF resource name | `terraform output wif_provider` |
| `WIF_SERVICE_ACCOUNT` | Service account email | `terraform output wif_service_account` |
| `ARTIFACT_REGISTRY` | Docker registry URL | `terraform output artifact_registry` |
| `GCS_BUCKET` | Bucket name | `terraform output gcs_bucket` |
| `AGENT_NAME` | `llmops-rag-agent` | From your `.env` |

### 6.2 Set GitHub Environments

1. Go to **Settings** → **Environments**
2. Create these environments:

| Environment | Description | Protection Rule |
|---|---|---|
| `dev` | Development | None (auto-deploy on any push to `develop` branch) |
| `staging` | Staging / QA | None (auto-deploy on merge to `main`) |
| `prod` | Production | Add required reviewers (team leads must approve) |

### 6.3 Understanding the CI/CD Pipeline

```yaml
# .github/workflows/ci-cd.yml does this:

On every Pull Request:
  ├── Runs ruff (code style linter)
  ├── Runs mypy (type checker)
  ├── Runs pytest (unit tests)
  ├── Builds Docker image (to verify it compiles)
  └── terraform plan (shows what infra would change, posts comment on PR)

On merge to main branch:
  ├── Runs all tests (must pass)
  ├── Builds Docker image
  ├── Pushes image to Artifact Registry (tagged with git SHA)
  └── Deploys to Cloud Run (dev environment)

On git tag (e.g. v1.0.0):
  ├── Builds Docker image (same code)
  ├── Pushes to Artifact Registry
  ├── Requests human approval in GitHub
  └── After approval → Deploys to Cloud Run (production)
```

> **Why deploy by git SHA, not "latest" tag?** The `latest` Docker tag is mutable — it can silently point to different code if someone pushes a new image. A SHA like `image@sha256:abc123...` is immutable. It always points to exactly the code you tested.

### 6.4 Test CI/CD

```bash
# Push a change and watch GitHub Actions
git checkout -b test-ci-cd
echo "# test" >> README.md
git add README.md
git commit -m "test: verify CI/CD pipeline"
git push origin test-ci-cd

# Open a Pull Request on GitHub
# Go to Actions tab to watch the workflow run
```

---

## 7. Running the Pipelines

### 7.1 Option A — Run Locally (Fastest for Testing)

Good for verifying your config before submitting to Vertex AI:

```bash
# Feature Engineering (builds vector index from documents)
poetry run llmops confs/feature_engineering.yaml

# Generate QA dataset (Gemini generates test questions from your docs)
poetry run llmops confs/generate_dataset.yaml

# Deployment pipeline (evaluates + would promote if cloud resources exist)
poetry run llmops confs/deployment.yaml

# Monitoring pipeline (evaluates production quality)
poetry run llmops confs/monitoring.yaml
```

> **What does this do?** `poetry run llmops <config>` calls `scripts.py`, which reads the YAML, identifies the `KIND` field, creates the correct Job class, and runs it. The job runs Python code directly on your local machine — no Vertex AI cluster.

### 7.2 Option B — Run on Vertex AI (Production)

This runs jobs on managed Vertex AI infrastructure — the production way:

```bash
# Compile all pipelines to KFP YAML format
python -m kfp_pipelines.compile_and_run --compile-only \
  --project $GCP_PROJECT_ID \
  --bucket $GCS_BUCKET

# Submit Feature Engineering pipeline to Vertex AI
python -m kfp_pipelines.compile_and_run \
  --pipeline feature_engineering \
  --project $GCP_PROJECT_ID \
  --bucket $GCS_BUCKET

# Submit Deployment pipeline
python -m kfp_pipelines.compile_and_run \
  --pipeline deployment \
  --project $GCP_PROJECT_ID \
  --bucket $GCS_BUCKET

# Submit Master pipeline (runs all three in sequence)
python -m kfp_pipelines.compile_and_run \
  --pipeline master \
  --project $GCP_PROJECT_ID \
  --bucket $GCS_BUCKET
```

### 7.3 Monitor Pipeline Runs

After submitting, watch progress in the GCP Console:

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Navigate to: **Vertex AI → Pipelines → Pipeline runs**
3. Click on your run
4. You'll see a visual graph with each step turning green (pass) or red (fail)

```bash
# Or from terminal
gcloud ai pipeline-jobs list --region us-central1

# View a specific run
gcloud ai pipeline-jobs describe PIPELINE_JOB_ID --region us-central1
```

### 7.4 Understanding Pipeline 2 Output (Quality Gate)

When Pipeline 2 (Deployment) runs, it will log something like:

```
Evaluation Results:
  Answer Relevance:  0.82 / 1.0   ← How well the answer matches the question
  Faithfulness:      0.79 / 1.0   ← How grounded the answer is in the documents
  Toxicity:          0.02 / 1.0   ← Lower is better (0.0 = not toxic)
  Overall:           0.80 / 1.0

Quality Gate Threshold: 0.75
Decision: PASS ✅ → Promoting model to production label
```

If it says FAIL, the model is NOT deployed. You must:
1. Check which metric is low
2. Improve documents OR adjust the prompt in `confs/rag_chain_config.yaml`
3. Re-run Pipeline 1 (if documents changed)
4. Re-run Pipeline 2

---

## 8. Deploying the Agent to Cloud Run

### 8.1 Build the Docker Image Locally

```bash
# Build (this may take 3-5 minutes first time)
docker build -t llmops-agent:latest .

# Test locally – make sure it starts
docker run -p 8080:8080 --env-file .env llmops-agent:latest

# In a second terminal, test it
curl http://localhost:8080/health
# Expected: {"status":"healthy","agent":"llmops-rag-agent"}
```

### 8.2 Push to Artifact Registry

```bash
# Set registry URL (from terraform output)
REGISTRY="us-central1-docker.pkg.dev/YOUR_PROJECT_ID/llmops-agent-dev"

# Tag the image
docker tag llmops-agent:latest $REGISTRY/agent:latest

# Configure Docker to use gcloud credentials for the registry
gcloud auth configure-docker us-central1-docker.pkg.dev

# Push the image
docker push $REGISTRY/agent:latest

# Verify it was pushed
gcloud artifacts docker images list $REGISTRY
```

### 8.3 Deploy to Cloud Run

```bash
# Deploy (replace with your actual values)
gcloud run deploy llmops-agent-dev \
  --image $REGISTRY/agent:latest \
  --region us-central1 \
  --service-account llmops-agent-dev@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT_ID=YOUR_PROJECT_ID,GCP_LOCATION=us-central1,MODEL_NAME=gemini-2.0-flash,AGENT_NAME=llmops-rag-agent \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 10 \
  --memory 2Gi
```

> **What `--allow-unauthenticated` means:** The Cloud Run URL is publicly accessible (no auth required). This is fine for development. For production, remove this flag and add authentication via API Gateway (see Section 11).

> **What `--min-instances 0` means:** When no one is using the agent, it scales down to zero. This saves cost. The trade-off is a "cold start" of ~5-10 seconds for the first request. Set to `--min-instances 1` to avoid cold starts (but incurs cost 24/7).

### 8.4 Verify the Deployment

```bash
# Get the Cloud Run URL
URL=$(gcloud run services describe llmops-agent-dev \
  --region us-central1 \
  --format="value(status.url)")

echo "Your agent is live at: $URL"

# Health check
curl $URL/health
# Expected: {"status":"healthy"}

# Test chat
curl -X POST $URL/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the annual leave policy?", "session_id": "test-001"}'
```

---

## 9. Testing Your Production Deployment

### 9.1 End-to-End Smoke Test

Run these in order to verify every component is working:

```bash
URL="https://llmops-agent-dev-XXXX.run.app"  # your actual URL

# Test 1: Basic health check
echo "--- Test 1: Health ---"
curl -s $URL/health | python -m json.tool

# Test 2: Readiness check
echo "--- Test 2: Ready ---"
curl -s $URL/ready | python -m json.tool

# Test 3: Simple chat
echo "--- Test 3: Chat ---"
curl -s -X POST $URL/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Hello, what can you help me with?", "session_id": "smoke-test"}' \
  | python -m json.tool

# Test 4: RAG retrieval (uses your actual documents)
echo "--- Test 4: RAG ---"
curl -s -X POST $URL/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the company leave policy?", "session_id": "smoke-test"}' \
  | python -m json.tool

# Test 5: Guardrail (off-topic question)
echo "--- Test 5: Guardrail ---"
curl -s -X POST $URL/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the best competitor to our company?", "session_id": "smoke-test"}' \
  | python -m json.tool
# Should return a message declining to answer off-topic questions
```

### 9.2 What to Look For

| Test | Expected Result | If It Fails |
|---|---|---|
| Health check | `{"status":"healthy"}` | Cloud Run didn't start — check logs |
| Simple chat | Non-empty `response` field | Gemini API issue — check credentials |
| RAG chat | Answer references your documents | Vector index not built — run Pipeline 1 |
| Guardrail | "I can only assist with..." type message | Check `confs/rag_chain_config.yaml` |

### 9.3 Check Cloud Run Logs

```bash
# View recent logs
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="llmops-agent-dev"' \
  --limit 50 \
  --format "value(textPayload)"

# Or in GCP Console: Logging → Log Explorer
# Filter: resource.type="cloud_run_revision"
```

---

## 10. Monitoring & Operations

### 10.1 Set Up Scheduled Monitoring (Pipeline 3)

After deploying, set up a daily check that evaluates production quality:

```bash
# Create a Cloud Scheduler job to run monitoring daily at 6 AM
gcloud scheduler jobs create http monitoring-daily \
  --schedule="0 6 * * *" \
  --uri="https://us-central1-aiplatform.googleapis.com/v1/projects/${GCP_PROJECT_ID}/locations/us-central1/pipelineJobs" \
  --http-method=POST \
  --message-body="{
    \"displayName\": \"monitoring-scheduled\",
    \"templatePath\": \"gs://${GCS_BUCKET}/compiled_pipelines/monitoring_pipeline.json\"
  }" \
  --oauth-service-account-email="llmops-agent-dev@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --location=us-central1

# Verify the scheduler job
gcloud scheduler jobs list --location=us-central1
```

### 10.2 View Experiment Metrics

Each pipeline run logs metrics to Vertex AI Experiments — like a scoreboard for every run:

1. Go to **GCP Console → Vertex AI → Experiments**
2. Find experiment: `llmops-pipeline-runs`
3. Each run shows: evaluation scores, number of documents, chunks, and the deployment decision

### 10.3 Set Up Alerting

Create alerts so you get notified when quality drops:

```bash
# Create a notification channel (email)
gcloud alpha monitoring channels create \
  --display-name="LLMOps Alerts" \
  --type=email \
  --channel-labels=email_address=your-email@example.com

# Create alerting policy for quality degradation
# (Do this in GCP Console → Cloud Monitoring → Alerting → Create Policy)
# Alert when custom metric "answer_relevance" < 0.60 for 5 minutes
```

### 10.4 View Cloud Run Metrics

In **GCP Console → Cloud Run → llmops-agent-dev → Metrics**:
- **Request count** — how many requests per minute
- **Request latency** — p50, p95, p99 latency
- **Instance count** — how many containers are running
- **Error rate** — percentage of 4xx/5xx responses

### 10.5 Database of Runs

```bash
# List all pipeline runs
gcloud ai pipeline-jobs list --region us-central1

# View failed runs only
gcloud ai pipeline-jobs list \
  --region us-central1 \
  --filter="state=PIPELINE_STATE_FAILED"

# View specific run details
gcloud ai pipeline-jobs describe PIPELINE_JOB_ID --region us-central1
```

---

## 11. Production Hardening — API Gateway

This section covers adding an API Gateway in front of Cloud Run. This is the **Layer 2** of our 7-layer architecture — authentication, rate limiting, and WAF protection.

> **When to add this:** After you have the core system working and before going public-facing production. For internal tools or small teams, this can be skipped initially.

### 11.1 What API Gateway Adds

```
Without API Gateway:
  Anyone with your Cloud Run URL can send unlimited requests
  No authentication required
  Direct exposure to the internet

With API Gateway:
  Every request must have a valid API key or OAuth token
  Max 60 requests/minute per client (configurable)
  WAF blocks SQL injection, XSS, and other attacks
  All requests logged with client identity
```

### 11.2 Create the API Gateway

```bash
# Step 1: Create an API config (describes your endpoints)
cat > api_config.yaml << 'EOF'
swagger: "2.0"
info:
  title: LLMOps Agent API
  description: RAG chatbot powered by Gemini
  version: "1.0"
host: "llmops-api.endpoints.YOUR_PROJECT_ID.cloud.goog"
x-google-backend:
  address: https://llmops-agent-dev-XXXX.run.app
  deadline: 30.0
paths:
  /health:
    get:
      summary: Health check
      operationId: health
      responses:
        "200":
          description: OK
  /chat:
    post:
      summary: Chat endpoint
      operationId: chat
      security:
        - api_key: []
      responses:
        "200":
          description: OK
securityDefinitions:
  api_key:
    type: apiKey
    name: key
    in: query
EOF

# Step 2: Create the Endpoints service
gcloud endpoints services deploy api_config.yaml

# Step 3: Create the API
gcloud api-gateway apis create llmops-api \
  --project=YOUR_PROJECT_ID

# Step 4: Create the API config
gcloud api-gateway api-configs create v1 \
  --api=llmops-api \
  --openapi-spec=api_config.yaml \
  --project=YOUR_PROJECT_ID

# Step 5: Create the gateway (this is the public URL)
gcloud api-gateway gateways create llmops-gateway \
  --api=llmops-api \
  --api-config=v1 \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID

# Step 6: Get the gateway URL
gcloud api-gateway gateways describe llmops-gateway \
  --location=us-central1 \
  --format="value(defaultHostname)"
```

### 11.3 Create API Keys for Clients

```bash
# Create an API key for a client
gcloud alpha services api-keys create \
  --display-name="LLMOps Client Key" \
  --api-target=service=llmops-api.endpoints.YOUR_PROJECT_ID.cloud.goog

# Get the key value
gcloud alpha services api-keys list
```

### 11.4 Test With API Key

```bash
GATEWAY_URL="https://llmops-gateway-XXXX.uc.gateway.dev"
API_KEY="your-api-key-here"

# With API key authentication
curl -X POST "${GATEWAY_URL}/chat?key=${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the leave policy?", "session_id": "test"}'
```

---

## 12. Troubleshooting

### Common Errors and Fixes

| Error | Likely Cause | Fix |
|---|---|---|
| `Permission denied` on Vertex AI | Missing IAM role | `gcloud projects add-iam-policy-binding ...` or re-run Terraform |
| `Vector Search index creation timeout` | Normal — takes 30-60 min | Wait. Check progress in GCP Console → Vertex AI → Vector Search |
| `Cloud Run 503 Service Unavailable` | Cold start or container crash | Check Cloud Run logs. Increase memory: `--memory 4Gi` |
| `KFP component fails with ModuleNotFound` | Package missing in `packages_to_install` | Add the missing package to the `@dsl.component` decorator |
| `WIF authentication failed in GitHub Actions` | Wrong pool/provider value | Verify `terraform output wif_provider` matches the GitHub secret |
| `Config validation error: extra fields not permitted` | YAML has unknown field | Check field names match the Pydantic Job class |
| `FAISS / local_vector_db fails in serving` | Running locally without lab setup | Install: `pip install faiss-cpu numpy` |
| `Gemini returns empty response` | Quota exceeded | Wait 1 minute, retry. Or reduce batch size in config |
| `Cloud Run cannot access GCS` | Service account missing Storage role | Check Terraform IAM bindings are applied |
| `Pipeline fails at IngestDocuments` | No documents found in GCS path | Upload documents: `gsutil cp data/documents/* gs://BUCKET/documents/` |

### Debug Commands

```bash
# Check which IAM roles the agent service account has
gcloud projects get-iam-policy YOUR_PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:llmops-agent-dev@" \
  --format="table(bindings.role)"

# Check Cloud Run service status
gcloud run services describe llmops-agent-dev \
  --region us-central1 \
  --format="value(status.conditions.message)"

# Check if Vertex AI Vector Search index is ready
gcloud ai indexes list --region us-central1

# View recent pipeline failures
gcloud ai pipeline-jobs list \
  --region us-central1 \
  --filter="state=PIPELINE_STATE_FAILED" \
  --format="table(displayName,state,createTime)"

# Stream Cloud Run logs in real time
gcloud run services logs tail llmops-agent-dev --region us-central1

# Check all enabled APIs
gcloud services list --enabled --filter="name:aiplatform OR name:run OR name:storage"
```

---

## 13. Quick Reference Card

```bash
# ───── ENVIRONMENT SETUP ─────────────────────────────────────────
export PROJECT_ID=$(gcloud config get-value project)
export LOCATION=us-central1
export BUCKET="${PROJECT_ID}-llmops-dev"
export REGISTRY="us-central1-docker.pkg.dev/${PROJECT_ID}/llmops-agent-dev"

# ───── RUN PIPELINES LOCALLY ─────────────────────────────────────
poetry run llmops confs/feature_engineering.yaml  # build vector index
poetry run llmops confs/generate_dataset.yaml     # generate QA pairs
poetry run llmops confs/deployment.yaml           # evaluate + deploy
poetry run llmops confs/monitoring.yaml           # check quality

# ───── SUBMIT TO VERTEX AI ───────────────────────────────────────
python -m kfp_pipelines.compile_and_run \
  --pipeline master --project $PROJECT_ID --bucket $BUCKET

# ───── SERVE LOCALLY ─────────────────────────────────────────────
poetry run python -m serving.server
curl http://localhost:8080/health

# ───── DOCKER ────────────────────────────────────────────────────
docker build -t llmops-agent:latest .
docker run -p 8080:8080 --env-file .env llmops-agent:latest

# ───── PUSH + DEPLOY TO CLOUD RUN ────────────────────────────────
docker tag llmops-agent:latest $REGISTRY/agent:latest
docker push $REGISTRY/agent:latest
gcloud run deploy llmops-agent-dev \
  --image $REGISTRY/agent:latest \
  --region $LOCATION \
  --service-account llmops-agent-dev@${PROJECT_ID}.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT_ID=${PROJECT_ID},MODEL_NAME=gemini-2.0-flash

# ───── INFRASTRUCTURE ────────────────────────────────────────────
cd terraform
terraform plan   # preview changes
terraform apply  # apply changes
terraform output # view all outputs

# ───── DEBUGGING ─────────────────────────────────────────────────
gcloud run services logs tail llmops-agent-dev --region $LOCATION
gcloud ai pipeline-jobs list --region $LOCATION
gcloud logging read 'resource.type="cloud_run_revision"' --limit 20

# ───── LAB TESTING (no billing services) ─────────────────────────
python lab_test/run_lab_test.py \
  --project $PROJECT_ID --location $LOCATION --bucket $BUCKET --skip-serving
```

---

*Guide version: 2.0 | Project: final-development-llmops | Step-by-step from zero to production*


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
