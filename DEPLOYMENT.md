# Deployment Guide — LLMOps Pipeline on GCP

Complete step-by-step guide to deploy the LLMOps pipeline on Google Cloud Platform
using Terraform and GitHub Actions CI/CD.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [GCP Organization Permissions](#2-gcp-organization-permissions)
3. [Option A: Fresh Setup with Terraform Bootstrap](#3-option-a-fresh-setup-with-terraform-bootstrap)
4. [Option B: Use Existing Infrastructure](#4-option-b-use-existing-infrastructure)
5. [Configure GitHub Repository](#5-configure-github-repository)
6. [Trigger CI/CD Pipeline](#6-trigger-cicd-pipeline)
7. [Verify Deployment](#7-verify-deployment)
8. [Troubleshooting](#8-troubleshooting)
9. [Architecture Overview](#9-architecture-overview)

---

## 1. Prerequisites

### Tools

| Tool | Version | Install |
|------|---------|---------|
| Google Cloud SDK (`gcloud`) | Latest | https://cloud.google.com/sdk/docs/install |
| Terraform | >= 1.5.0 | https://developer.hashicorp.com/terraform/downloads |
| GitHub CLI (`gh`) | Latest | https://cli.github.com/ |
| Python | 3.11 | https://www.python.org/downloads/ |
| Poetry | 1.8.x | `pip install poetry==1.8.4` |

### Accounts

- Google Cloud account with billing enabled
- GitHub account with repository created

---

## 2. GCP Organization Permissions

If you are using an **organization Google Cloud account**, you need specific
permissions. Ask your GCP admin to grant these roles on the project:

### Minimum Roles Required (for Terraform Bootstrap)

| Role | Why |
|------|-----|
| `roles/owner` **OR** the specific roles below | Simplest option — full admin |
| `roles/iam.workloadIdentityPoolAdmin` | Create WIF pool for GitHub Actions |
| `roles/iam.serviceAccountAdmin` | Create service accounts (CI/CD + Agent) |
| `roles/resourcemanager.projectIamAdmin` | Bind IAM roles to service accounts |
| `roles/artifactregistry.admin` | Create Artifact Registry Docker repo |
| `roles/storage.admin` | Create GCS buckets (state + artifacts) |
| `roles/run.admin` | Create Cloud Run services |
| `roles/serviceusage.serviceUsageAdmin` | Enable Google APIs |

### If Organization Policies Block You

Some organizations have policies that restrict:

1. **Domain Restricted Sharing** — prevents `allUsers` IAM binding on Cloud Run.
   - Fix: Skip public access; use IAP or authenticated access instead.
   
2. **Disable Service Account Key Creation** — no issue, we use WIF (keyless).

3. **Allowed External IPs** — no issue, Cloud Run handles this.

4. **Required Labels** — add required labels to Terraform resources.

Ask your admin to check: `gcloud org-policies list --project=YOUR_PROJECT_ID`

---

## 3. Option A: Fresh Setup with Terraform Bootstrap

Use this if you're starting from scratch with a new GCP project.

### Step 1: Authenticate

```bash
# Login to GCP
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID

# Login to GitHub
gh auth login
```

### Step 2: Configure Bootstrap

```bash
cd terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
project_id       = "project-3e17312f-26d8-4511-821"   # Your GCP project ID
region           = "us-central1"
environment      = "dev"
repository_owner = "your-github-username"               # GitHub user or org
repository_name  = "general-llmops-pipeline"            # Your repo name
```

### Step 3: Run Bootstrap

```bash
terraform init
terraform plan    # Review what will be created
terraform apply   # Type 'yes' to confirm
```

This creates (in ~3 minutes):
- ✅ All required GCP APIs enabled
- ✅ Artifact Registry repo (`llmops-agent-dev`)
- ✅ GCS bucket for Terraform state
- ✅ Agent service account with correct IAM roles
- ✅ CI/CD service account with correct IAM roles  
- ✅ Workload Identity Federation (WIF) pool + provider
- ✅ GitHub Variables auto-configured

### Step 4: Set GitHub Secrets

Bootstrap creates Variables automatically but **Secrets must be set manually**
(Terraform should not store secrets in state files).

Get the values from Terraform output:

```bash
terraform output
```

Then set them in GitHub:

```bash
gh secret set WIF_PROVIDER --body "$(terraform output -raw wif_provider)"
gh secret set WIF_SERVICE_ACCOUNT --body "$(terraform output -raw cicd_service_account)"
gh secret set AGENT_SERVICE_ACCOUNT --body "$(terraform output -raw agent_service_account)"
```

The GCS bucket for artifacts needs to be created by the main module or set manually:

```bash
gh secret set GCS_BUCKET --body "YOUR_PROJECT_ID-llmops-dev"
```

### Step 5: Verify

```bash
# Check GitHub Variables were created
gh variable list

# Expected output:
# GCP_PROJECT_ID           project-3e17312f-26d8-4511-821
# GCP_REGION               us-central1
# ARTIFACT_REGISTRY_REPO   llmops-agent-dev
# IMAGE_NAME               llmops-agent
# CLOUD_RUN_SERVICE_DEV    llmops-agent-dev
# TERRAFORM_STATE_BUCKET   terraform-state-llmops-dev-XXXXXXXX

# Check GitHub Secrets were set
gh secret list

# Expected output (values hidden):
# WIF_PROVIDER              Updated ...
# WIF_SERVICE_ACCOUNT       Updated ...
# AGENT_SERVICE_ACCOUNT     Updated ...
# GCS_BUCKET                Updated ...
```

---

## 4. Option B: Use Existing Infrastructure

Use this if you already have WIF, service accounts, Cloud Run, and Artifact
Registry set up (e.g., from the previous `deploy.sh` script).

### Your Existing Resources

Based on your GCP project, these already exist:

| Resource | Value |
|----------|-------|
| Project ID | `project-3e17312f-26d8-4511-821` |
| Project Number | `1012647224038` |
| Region | `us-central1` |
| Artifact Registry | `us-central1-docker.pkg.dev/project-3e17312f-26d8-4511-821/llmops-agent-dev` |
| Cloud Run Service | `llmops-agent-dev` |
| Agent SA | `llmops-agent-dev@project-3e17312f-26d8-4511-821.iam.gserviceaccount.com` |
| CI/CD SA | `llmops-cicd-dev@project-3e17312f-26d8-4511-821.iam.gserviceaccount.com` |
| WIF Provider | `projects/1012647224038/locations/global/workloadIdentityPools/github-pool-dev/providers/github-provider` |
| GCS Bucket | `project-3e17312f-26d8-4511-821-llmops-dev` |

### Set GitHub Variables

```bash
gh variable set GCP_PROJECT_ID         --body "project-3e17312f-26d8-4511-821"
gh variable set GCP_REGION             --body "us-central1"
gh variable set ARTIFACT_REGISTRY_REPO --body "llmops-agent-dev"
gh variable set IMAGE_NAME             --body "llmops-agent"
gh variable set CLOUD_RUN_SERVICE_DEV  --body "llmops-agent-dev"
```

### Set GitHub Secrets

```bash
gh secret set WIF_PROVIDER          --body "projects/1012647224038/locations/global/workloadIdentityPools/github-pool-dev/providers/github-provider"
gh secret set WIF_SERVICE_ACCOUNT   --body "llmops-cicd-dev@project-3e17312f-26d8-4511-821.iam.gserviceaccount.com"
gh secret set AGENT_SERVICE_ACCOUNT --body "llmops-agent-dev@project-3e17312f-26d8-4511-821.iam.gserviceaccount.com"
gh secret set GCS_BUCKET            --body "project-3e17312f-26d8-4511-821-llmops-dev"
```

> **Important:** We use GitHub **Variables** (not Secrets) for non-sensitive
> values like project ID, region, and repo name. This avoids a GitHub Actions
> bug where secret values get masked (`***`) when passed between jobs.

---

## 5. Configure GitHub Repository

### Step 1: Create Environments

Go to **Settings → Environments** in your GitHub repo and create:

1. **dev** — no protection rules (auto-deploy)
2. **staging** — (optional) add reviewers for approval
3. **prod** — (optional) add reviewers for approval

### Step 2: Verify WIF Attribute Condition

The WIF provider has an attribute condition that restricts which GitHub repos
can authenticate. Verify it matches your repo:

```bash
gcloud iam workload-identity-pools providers describe github-provider \
  --project="project-3e17312f-26d8-4511-821" \
  --location="global" \
  --workload-identity-pool="github-pool-dev" \
  --format="value(attributeCondition)"
```

Expected output should contain your repo: `assertion.repository == 'YOUR_ORG/general-llmops-pipeline'`

If it doesn't match, update it:

```bash
gcloud iam workload-identity-pools providers update-oidc github-provider \
  --project="project-3e17312f-26d8-4511-821" \
  --location="global" \
  --workload-identity-pool="github-pool-dev" \
  --attribute-condition="assertion.repository == 'YOUR_ORG/general-llmops-pipeline'"
```

### Step 3: Verify CI/CD SA Permissions

```bash
gcloud projects get-iam-policy project-3e17312f-26d8-4511-821 \
  --flatten="bindings[].members" \
  --filter="bindings.members:llmops-cicd-dev@" \
  --format="table(bindings.role)"
```

Required roles:
- `roles/artifactregistry.writer`
- `roles/run.admin`
- `roles/iam.serviceAccountUser`
- `roles/storage.objectAdmin`
- `roles/aiplatform.user`

---

## 6. Trigger CI/CD Pipeline

### Automatic (push to branch)

```bash
git add -A
git commit -m "feat: setup CI/CD pipeline"
git push origin main    # or develop
```

This triggers: **Lint & Test → Build & Push → Deploy to Dev**

### Manual (workflow_dispatch)

1. Go to **Actions** tab in your GitHub repo
2. Click **CI/CD Pipeline** workflow
3. Click **Run workflow**
4. Select branch and environment
5. Optionally check "Submit Vertex AI Pipeline"

### What Happens

```
Push to main/develop
    │
    ▼
┌─────────────────┐
│  Lint & Test     │  ruff check + pytest
│  (~2 min)        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Build & Push    │  Docker build + push to Artifact Registry
│  (~4 min)        │  Uses BuildKit caching for speed
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Deploy to Dev   │  Cloud Run deploy with new image
│  (~2 min)        │
└─────────────────┘
```

---

## 7. Verify Deployment

### Check Cloud Run

```bash
# Get the service URL
gcloud run services describe llmops-agent-dev \
  --region=us-central1 \
  --format="value(status.url)"

# Test health endpoint
curl https://llmops-agent-dev-1012647224038.us-central1.run.app/health

# Expected: {"status":"healthy","agent":"llmops-rag-agent"}
```

### Check in GCP Console

1. **Cloud Run** → Services → `llmops-agent-dev` → should show latest revision
2. **Artifact Registry** → `llmops-agent-dev` → should show image with commit SHA tag
3. **GitHub Actions** → All jobs should show green checkmarks

---

## 8. Troubleshooting

### "Missing required configuration" in Build step

**Cause:** GitHub Variables or Secrets not set.

**Fix:** Follow Section 4 (Option B) to set all Variables and Secrets.

### "Missing --image" in Deploy step

**Cause:** Image URI is empty. Usually means Variables are not set.

**Fix:**
```bash
# Verify Variables are set
gh variable list

# Should show: GCP_PROJECT_ID, GCP_REGION, ARTIFACT_REGISTRY_REPO, IMAGE_NAME, CLOUD_RUN_SERVICE_DEV
```

### Docker build fails with "README.md not found"

**Cause:** Poetry requires `README.md` to resolve package metadata.

**Fix:** Ensure `README.md` exists in the project root. The Dockerfile copies it.

### "Permission denied" during Docker push

**Cause:** WIF authentication failed or CI/CD SA lacks `artifactregistry.writer` role.

**Fix:**
```bash
# Check WIF provider attribute condition matches your repo
gcloud iam workload-identity-pools providers describe github-provider \
  --project=YOUR_PROJECT_ID --location=global \
  --workload-identity-pool=github-pool-dev

# Grant role if missing
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:llmops-cicd-dev@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"
```

### Cloud Run deploy fails with "revision failed to become healthy"

**Cause:** Container crashes on startup.

**Fix:**
```bash
# Check Cloud Run logs for the specific error
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=llmops-agent-dev" \
  --limit=20 --format="table(timestamp, textPayload)"

# Common causes:
# 1. Missing env vars → set GCP_PROJECT_ID, GCP_LOCATION, GCS_BUCKET in Cloud Run
# 2. google-adk import error → ensure google-adk is in pyproject.toml dependencies
# 3. Port mismatch → Dockerfile uses 8080, Cloud Run expects 8080
```

### Tests fail in CI but pass locally

**Cause:** Different Python version or missing env vars.

**Fix:** CI uses Python 3.11 and sets `GCP_PROJECT_ID=test-project`, `GCS_BUCKET=test-bucket` as env vars.

### Terraform state lock error

```bash
terraform -chdir=terraform/bootstrap force-unlock LOCK_ID
```

---

## 9. Architecture Overview

### Project Structure

```
final-development-llmops/
├── .github/workflows/
│   └── ci-cd.yml            # CI/CD pipeline (lint → build → deploy)
├── terraform/
│   ├── bootstrap/            # One-time: WIF, AR, SAs, GitHub vars
│   │   ├── main.tf
│   │   └── terraform.tfvars.example
│   └── main/                 # Ongoing: Cloud Run, GCS bucket
│       ├── main.tf
│       └── terraform.tfvars.example
├── src/llmops_pipeline/      # Pipeline code (config-driven jobs)
├── serving/                  # ADK agent serving layer (FastAPI)
├── kfp_pipelines/            # Vertex AI Pipeline definitions
├── confs/                    # YAML configs for pipeline jobs
├── tests/                    # pytest tests
├── Dockerfile                # Multi-stage Docker build
├── .dockerignore             # Exclude files from Docker context
├── pyproject.toml            # Poetry dependencies
└── docker-compose.yml        # Local development
```

### CI/CD Flow

```
GitHub Push  ──►  Lint & Test  ──►  Build Docker Image  ──►  Deploy to Cloud Run
                     │                     │                        │
                  ruff check            BuildKit             deploy-cloudrun@v2
                  pytest              + AR push              + env vars
```

### GitHub Config Separation

| Type | Storage | Why |
|------|---------|-----|
| Project ID, Region, Repo name | **Variables** | Non-sensitive; avoids secret masking in cross-job outputs |
| WIF Provider, SA emails, Bucket | **Secrets** | Sensitive; masked in logs |

### Terraform Two-Phase Design

| Phase | Module | When | State |
|-------|--------|------|-------|
| Bootstrap | `terraform/bootstrap/` | One-time, from laptop | Local file |
| Main | `terraform/main/` | Every deploy, from CI/CD | Remote (GCS) |

---

## Quick Reference

### GitHub Variables to Set

```
GCP_PROJECT_ID           = your-project-id
GCP_REGION               = us-central1
ARTIFACT_REGISTRY_REPO   = llmops-agent-dev
IMAGE_NAME               = llmops-agent
CLOUD_RUN_SERVICE_DEV    = llmops-agent-dev
```

### GitHub Secrets to Set

```
WIF_PROVIDER             = projects/NNNN/locations/global/workloadIdentityPools/github-pool-dev/providers/github-provider
WIF_SERVICE_ACCOUNT      = llmops-cicd-dev@PROJECT_ID.iam.gserviceaccount.com
AGENT_SERVICE_ACCOUNT    = llmops-agent-dev@PROJECT_ID.iam.gserviceaccount.com
GCS_BUCKET               = PROJECT_ID-llmops-dev
```

### CI/CD SA Required Roles

```
roles/artifactregistry.writer
roles/run.admin
roles/iam.serviceAccountUser
roles/storage.objectAdmin
roles/aiplatform.user
```
