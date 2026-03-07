# GitHub Repository & CI/CD Setup Guide

> **Document:** How to set up the GitHub repository, configure CI/CD, and follow the daily development workflow  
> **Author:** Ashish Dwivedi  
> **Last Updated:** March 2026

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Create the GitHub Repository](#2-create-the-github-repository)
3. [Initial Code Push](#3-initial-code-push)
4. [Run Bootstrap Terraform (WIF + Service Accounts)](#4-run-bootstrap-terraform-wif--service-accounts)
5. [Configure GitHub Variables & Secrets](#5-configure-github-variables--secrets)
6. [Set Up Branch Protection Rules](#6-set-up-branch-protection-rules)
7. [How the CI/CD Pipeline Works](#7-how-the-cicd-pipeline-works)
8. [Daily Developer Workflow](#8-daily-developer-workflow)
9. [Manual Deployment & Pipeline Dispatch](#9-manual-deployment--pipeline-dispatch)
10. [Troubleshooting CI/CD](#10-troubleshooting-cicd)

---

## 1. Prerequisites

Before starting, you need:

| Tool | Installation |
|------|-------------|
| **Git** | `winget install Git.Git` (Windows) or `apt install git` (Linux) |
| **GitHub CLI (`gh`)** | `winget install GitHub.cli` or [cli.github.com](https://cli.github.com) |
| **Google Cloud SDK** | [cloud.google.com/sdk/install](https://cloud.google.com/sdk/docs/install) |
| **Terraform >= 1.5** | `winget install Hashicorp.Terraform` or [terraform.io](https://developer.hashicorp.com/terraform/downloads) |
| **GCP Project** | With billing enabled and owner permissions |
| **GitHub Account** | With permissions to create repositories |

Authenticate both tools:
```bash
gh auth login                         # GitHub CLI login
gcloud auth login                     # Google Cloud login
gcloud auth application-default login # For Terraform
gcloud config set project YOUR_PROJECT_ID
```

---

## 2. Create the GitHub Repository

### Option A: Create via GitHub CLI (Recommended)

```bash
# Create a private repo under your account
gh repo create ashishdwivedi28/general-llmops-pipeline --private --description "LLMOps Pipeline for HR Chatbot"
```

### Option B: Create via GitHub Web UI

1. Go to [github.com/new](https://github.com/new)
2. Repository name: `general-llmops-pipeline`
3. Visibility: **Private**
4. Do NOT initialize with README (we'll push existing code)
5. Click **Create repository**

---

## 3. Initial Code Push

Navigate to the project directory and push:

```bash
cd c:\Users\ashish.dwivedi\Desktop\pipeline-llmops\final-development-llmops

# Initialize git if not already done
git init
git branch -M main

# Add the remote
git remote add origin https://github.com/ashishdwivedi28/general-llmops-pipeline.git

# Stage all files
git add -A

# Verify .gitignore excludes sensitive files
# The following should NOT be tracked:
#   .env, myenv/, __pycache__/, *.tfstate, .terraform/
cat .gitignore

# First commit
git commit -m "feat: initial LLMOps pipeline codebase"

# Push
git push -u origin main
```

### Verify the .gitignore

Make sure your `.gitignore` includes at minimum:

```
# Python
__pycache__/
*.pyc
.venv/
myenv/
dist/
*.egg-info/

# Environment
.env
*.env

# Terraform
.terraform/
*.tfstate
*.tfstate.backup
*.tfvars
!terraform.tfvars.example

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# Poetry
poetry.lock
```

---

## 4. Run Bootstrap Terraform (WIF + Service Accounts)

The bootstrap Terraform module creates all the one-time infrastructure needed for CI/CD:

- **Workload Identity Federation (WIF)** — Lets GitHub Actions authenticate to GCP without storing static keys
- **Artifact Registry** — Docker image repository
- **Service Accounts** — CI/CD SA and Agent SA with appropriate IAM roles
- **Terraform State Bucket** — For the main Terraform module

### Step-by-Step

```bash
cd terraform/bootstrap

# Create your tfvars file
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:
```hcl
project_id       = "your-gcp-project-id"
region           = "us-central1"
environment      = "dev"
repository_owner = "ashishdwivedi28"
repository_name  = "general-llmops-pipeline"
```

Run Terraform:
```bash
terraform init
terraform plan    # Review what will be created
terraform apply   # Type 'yes' to confirm
```

### What Gets Created

| Resource | Name | Purpose |
|----------|------|---------|
| WIF Pool | `gh-actions-pool-dev` | Trust boundary for GitHub OIDC tokens |
| WIF Provider | `github-provider` | Maps GitHub JWT claims to GCP attributes |
| CI/CD Service Account | `llmops-cicd-dev@project.iam` | Used by CI/CD to deploy |
| Agent Service Account | `llmops-agent-dev@project.iam` | Used by Cloud Run at runtime |
| Artifact Registry | `llmops-agent-dev` | Stores Docker images |
| GCS Bucket | `terraform-state-llmops-dev-XXXXXXXX` | Terraform remote state |

### Save the Outputs

After apply, run:
```bash
terraform output
```

You'll see values like:
```
wif_provider           = "projects/123456/locations/global/workloadIdentityPools/gh-actions-pool-dev/providers/github-provider"
cicd_service_account   = "llmops-cicd-dev@your-project.iam.gserviceaccount.com"
agent_service_account  = "llmops-agent-dev@your-project.iam.gserviceaccount.com"
artifact_registry      = "us-central1-docker.pkg.dev/your-project/llmops-agent-dev"
terraform_state_bucket = "terraform-state-llmops-dev-a1b2c3d4"
```

**Copy these values** — you'll need them in the next step.

---

## 5. Configure GitHub Variables & Secrets

GitHub Actions needs two types of configuration:

- **Variables** (non-sensitive, visible in logs) — project details
- **Secrets** (encrypted, masked in logs) — WIF credentials, bucket names

### Using `gh` CLI (Recommended — Fastest)

The bootstrap Terraform outputs the exact commands. Run them:

```bash
# === VARIABLES (non-sensitive) ===
gh variable set GCP_PROJECT_ID         --repo ashishdwivedi28/general-llmops-pipeline --body "your-gcp-project-id"
gh variable set GCP_REGION             --repo ashishdwivedi28/general-llmops-pipeline --body "us-central1"
gh variable set ARTIFACT_REGISTRY_REPO --repo ashishdwivedi28/general-llmops-pipeline --body "llmops-agent-dev"
gh variable set IMAGE_NAME             --repo ashishdwivedi28/general-llmops-pipeline --body "llmops-agent"
gh variable set CLOUD_RUN_SERVICE_DEV  --repo ashishdwivedi28/general-llmops-pipeline --body "llmops-agent-dev"
gh variable set TERRAFORM_STATE_BUCKET --repo ashishdwivedi28/general-llmops-pipeline --body "terraform-state-llmops-dev-XXXXXXXX"

# === SECRETS (sensitive) ===
gh secret set WIF_PROVIDER          --repo ashishdwivedi28/general-llmops-pipeline --body "projects/123456/locations/global/workloadIdentityPools/gh-actions-pool-dev/providers/github-provider"
gh secret set WIF_SERVICE_ACCOUNT   --repo ashishdwivedi28/general-llmops-pipeline --body "llmops-cicd-dev@your-project.iam.gserviceaccount.com"
gh secret set AGENT_SERVICE_ACCOUNT --repo ashishdwivedi28/general-llmops-pipeline --body "llmops-agent-dev@your-project.iam.gserviceaccount.com"
gh secret set GCS_BUCKET            --repo ashishdwivedi28/general-llmops-pipeline --body "your-project-llmops-dev"
```

### Using GitHub Web UI (Alternative)

1. Go to your repo: `https://github.com/ashishdwivedi28/general-llmops-pipeline`
2. Click **Settings** → **Secrets and variables** → **Actions**
3. **Variables tab** → Click "New repository variable" for each variable
4. **Secrets tab** → Click "New repository secret" for each secret

### Summary of All Required Configuration

| Type | Name | Value Source |
|------|------|-------------|
| Variable | `GCP_PROJECT_ID` | Your GCP project ID |
| Variable | `GCP_REGION` | `us-central1` (or your GCP region) |
| Variable | `ARTIFACT_REGISTRY_REPO` | `llmops-agent-dev` |
| Variable | `IMAGE_NAME` | `llmops-agent` |
| Variable | `CLOUD_RUN_SERVICE_DEV` | `llmops-agent-dev` |
| Variable | `TERRAFORM_STATE_BUCKET` | From `terraform output terraform_state_bucket` |
| Secret | `WIF_PROVIDER` | From `terraform output wif_provider` |
| Secret | `WIF_SERVICE_ACCOUNT` | From `terraform output cicd_service_account` |
| Secret | `AGENT_SERVICE_ACCOUNT` | From `terraform output agent_service_account` |
| Secret | `GCS_BUCKET` | `{project_id}-llmops-dev` |

---

## 6. Set Up Branch Protection Rules

Branch protection ensures nobody pushes broken code directly to `main`.

### Via GitHub Web UI

1. Go to **Settings** → **Branches** → **Add branch protection rule**
2. Branch name pattern: `main`
3. Enable these settings:

| Setting | Value |
|---------|-------|
| **Require a pull request before merging** | ✅ Enabled |
| Require approvals | 0 (optional, set to 1 for teams) |
| **Require status checks to pass before merging** | ✅ Enabled |
| Status checks that are required | Select **"Lint & Test"** |
| **Require branches to be up to date before merging** | ✅ Enabled |
| **Do not allow bypassing the above settings** | ✅ Enabled |

4. Click **Create**

### Via `gh` CLI

```bash
gh api repos/ashishdwivedi28/general-llmops-pipeline/branches/main/protection \
  --method PUT \
  --field required_status_checks='{"strict":true,"contexts":["Lint & Test"]}' \
  --field enforce_admins=true \
  --field required_pull_request_reviews='{"required_approving_review_count":0}' \
  --field restrictions=null
```

**Important:** The "Lint & Test" status check name must match exactly — it corresponds to the job name in `ci-cd.yml`:
```yaml
jobs:
  lint-and-test:
    name: "Lint & Test"    # ← This is the status check name
```

---

## 7. How the CI/CD Pipeline Works

The entire CI/CD is defined in `.github/workflows/ci-cd.yml`.

### Pipeline Stages

```
Push to feature/* branch
  └── Stage 1: Lint & Test ─────────────────── (STOP — no build/deploy)

Push to main (via merged PR)
  └── Stage 1: Lint & Test
       └── Stage 2: Build & Push Docker Image to Artifact Registry
            └── Stage 3: Deploy to Cloud Run (dev)

workflow_dispatch (manual, with run_pipeline=true)
  └── Stage 1: Lint & Test
       └── Stage 2: Build & Push
            └── Stage 3: Deploy to Cloud Run
                 └── Stage 4: Submit Vertex AI Pipeline

Cron schedule (daily 2 AM UTC)
  └── Stage 5: Scheduled Monitoring Pipeline
```

### Stage Details

**Stage 1: Lint & Test**
- Checks out code
- Installs Python 3.11 + Poetry
- Runs `ruff check` (linting) and `ruff format --check` (formatting)
- Runs `pytest tests/` 
- Runs on **every** push and PR

**Stage 2: Build & Push**
- Only on `main` branch pushes (not feature branches or PRs)
- Authenticates to GCP via WIF (keyless — no static credentials)
- Builds Docker image using BuildKit with GitHub Actions cache
- Tags with commit SHA + `latest`
- Pushes to Artifact Registry

**Stage 3: Deploy**
- Fetches secrets from GCP Secret Manager (API keys)
- Deploys to Cloud Run with environment variables
- Uses the Agent Service Account for runtime permissions

**Stage 4: Submit Pipeline** (optional, manual)
- Compiles and submits the master KFP pipeline to Vertex AI

**Stage 5: Scheduled Monitoring** (daily)
- Runs at 2 AM UTC via cron
- Submits the monitoring pipeline to Vertex AI

### How WIF Authentication Works

Traditional approach: store a GCP service account key JSON as a GitHub secret. **We don't do this** — it's a security risk.

Instead:
1. GitHub Actions generates an OIDC token (JWT) for the workflow run
2. The JWT is sent to Google's STS (Security Token Service)
3. GCP validates the JWT against the WIF Pool + Provider
4. The `attribute_condition` ensures only our repo can authenticate
5. GCP returns a short-lived access token
6. GitHub Actions uses this token for `gcloud`, Docker push, Cloud Run deploy

```
GitHub Actions  ──OIDC JWT──→  GCP STS  ──validates──→  WIF Pool/Provider
                                  │
                            ←access_token─── GCP returns short-lived token
                                  │
GitHub Actions  ──access_token──→  Artifact Registry, Cloud Run, etc.
```

---

## 8. Daily Developer Workflow

### Standard Development Loop

```bash
# 1. Start from latest main
git checkout main
git pull origin main

# 2. Create a descriptive feature branch
git checkout -b feature/add-new-guardrail

# 3. Make your changes
#    ... edit files ...

# 4. Stage and commit
git add -A
git commit -m "feat: add toxicity guardrail to output pipeline"

# 5. Push (triggers Lint & Test on CI)
git push origin feature/add-new-guardrail

# 6. Go to GitHub and open a PR
#    https://github.com/ashishdwivedi28/general-llmops-pipeline
#    Click "Compare & pull request"
#    Base: main ← Compare: feature/add-new-guardrail

# 7. Wait for CI (green checkmark ✅ = pass)
#    If FAIL: fix locally, commit, push to same branch. CI re-runs.
#    If PASS: click "Merge pull request" → "Confirm merge"

# 8. Clean up (optional but recommended)
git push origin --delete feature/add-new-guardrail
git branch -d feature/add-new-guardrail
git checkout main && git pull origin main
```

### Commit Message Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | When to Use |
|--------|-------------|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `refactor:` | Code change that neither fixes nor adds |
| `test:` | Adding or modifying tests |
| `chore:` | Build process or auxiliary tools |
| `ci:` | CI/CD changes |

### What Happens After Merge

```
1. Your code merges into main
2. CI runs → Lint & Test (again on main)
3. Build job → Docker image pushed to Artifact Registry
4. Deploy job → New image deployed to Cloud Run
5. Cloud Run serves new version immediately
```

---

## 9. Manual Deployment & Pipeline Dispatch

### Deploy with Pipeline Submission

Sometimes you want to deploy AND submit a Vertex AI pipeline run:

1. Go to: **GitHub → Actions → CI/CD Pipeline → Run workflow**
2. Select parameters:
   - **Branch:** `main`
   - **Target environment:** `dev` (or staging/prod)
   - **Submit Vertex AI Pipeline after deploy?** ✅ Check this
3. Click **Run workflow**

This will:
1. Lint & Test
2. Build Docker image
3. Deploy to Cloud Run (selected environment)
4. Submit the master pipeline to Vertex AI

### Deploy Without Pipeline

Same as above but leave "Submit Vertex AI Pipeline" unchecked.

### Quick Deploy of a Specific Commit

```bash
# Trigger workflow_dispatch from CLI
gh workflow run "CI/CD Pipeline" \
  --ref main \
  --field environment=dev \
  --field run_pipeline=false
```

---

## 10. Troubleshooting CI/CD

### Lint & Test Fails

```
Error: ruff check found issues
```
**Fix:** Run locally first:
```bash
ruff check src/ serving/ kfp_pipelines/ --fix
ruff format src/ serving/ kfp_pipelines/
```

```
Error: pytest failed
```
**Fix:** Run tests locally:
```bash
$env:GCP_PROJECT_ID="test-project"
$env:GCS_BUCKET="test-bucket"
pytest tests/ -v --tb=long
```

### Build Fails — "Missing required configuration"

```
Error: Missing required configuration: vars.GCP_PROJECT_ID secrets.WIF_PROVIDER
```
**Fix:** You haven't set the GitHub Variables/Secrets. Go to Section 5 above.

### Deploy Fails — "Permission denied"

```
Error: Permission 'run.services.update' denied
```
**Fix:** The CI/CD service account is missing `roles/run.admin`. Check:
```bash
gcloud projects get-iam-policy YOUR_PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:llmops-cicd-dev" \
  --format="table(bindings.role)"
```

### WIF Authentication Fails

```
Error: Unable to exchange token
```
**Possible causes:**
1. WIF provider `attribute_condition` doesn't match your repo name
2. Pool or provider was deleted and recreated (GCP keeps deleted names for 30 days)
3. `id-token: write` permission missing from `permissions:` block

**Debug:**
```bash
# Check pool exists
gcloud iam workload-identity-pools list --location=global

# Check provider exists
gcloud iam workload-identity-pools providers list \
  --workload-identity-pool=gh-actions-pool-dev \
  --location=global

# Check attribute condition
gcloud iam workload-identity-pools providers describe github-provider \
  --workload-identity-pool=gh-actions-pool-dev \
  --location=global \
  --format="value(attributeCondition)"
```

### Docker Push Fails — "Unauthorized"

```
Error: unauthorized: failed to authorize
```
**Fix:** The `docker/login-action` step needs the WIF access token. Verify:
1. The `auth` step has `token_format: access_token`
2. The login step uses `password: ${{ steps.auth.outputs.access_token }}`

### Cloud Run Deploy — Service Account Not Found

```
Error: service account not found
```
**Fix:** Verify the Agent SA exists:
```bash
gcloud iam service-accounts list --filter="email:llmops-agent-dev"
```

### Pipeline Submission Fails

```
Error: kfp module not found
```
**Fix:** The pipeline submission step only installs `kfp` and `google-cloud-aiplatform`. If your pipeline code imports other packages, add them to the `pip install` step in the workflow.

---

## Appendix: Full GitHub Repository Structure

After setup, your repository should look like:

```
.github/
  workflows/
    ci-cd.yml              # The CI/CD pipeline definition
confs/                     # OmegaConf YAML configurations
data/                      # Documents and datasets
docs/                      # Documentation (this file!)
kfp_pipelines/             # KFP pipeline definitions and compiler
serving/                   # FastAPI server, canary, gateway
src/                       # Core Python package (llmops_pipeline)
terraform/
  bootstrap/               # One-time WIF + SA + AR setup
  main/                    # Cloud Run + BigQuery + Secrets + etc.
tests/                     # Unit tests
Dockerfile                 # Container image definition
docker-compose.yml         # Local development setup
pyproject.toml             # Poetry dependencies
README.md                  # Project readme
```
