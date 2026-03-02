# GCP Resources Used in This Project

This document lists every Google Cloud Platform service used in the LLMOps pipeline, what it does, and where it appears in the codebase.

---

## Resource Summary Table

| # | GCP Service | Purpose | Where Used |
|---|------------|---------|------------|
| 1 | **Vertex AI Pipelines** | Orchestrate ML/LLM pipelines (KFP) | `kfp_pipelines/master.py` |
| 2 | **Vertex AI Vector Search** (Matching Engine) | Store and query document embeddings | `io/vector_db.py`, `kfp_pipelines/feature_engineering.py` |
| 3 | **Vertex AI Model Registry** | Version and label models (staging/prod) | `deployment/register_model.py` |
| 4 | **Vertex AI Experiments** | Track metrics, params, and runs | `io/services.py` (VertexAIService) |
| 5 | **Vertex AI Embeddings** | Generate text embeddings (text-embedding-004) | `io/vector_db.py` |
| 6 | **Gemini 2.0 Flash** | LLM for chat, evaluation, dataset generation | `serving/agent.py`, `deployment/evaluate_and_deploy.py` |
| 7 | **Cloud Run** | Host the ADK agent (serverless) | `terraform/main.tf`, `.github/workflows/ci-cd.yml` |
| 8 | **Cloud Storage (GCS)** | Store documents, embeddings, configs, pipeline root | `io/services.py` (GCSService) |
| 9 | **Cloud Logging** | Structured logs for all pipeline jobs + serving | `monitoring/post_deploy_eval.py`, `serving/callbacks.py` |
| 10 | **Cloud Trace** | Distributed tracing via OpenTelemetry | `serving/utils/observability.py` |
| 11 | **Artifact Registry** | Store Docker images for Cloud Run | `terraform/main.tf`, CI/CD |
| 12 | **IAM** | Service accounts + role bindings | `terraform/main.tf` |
| 13 | **Workload Identity Federation** | Keyless GitHub Actions → GCP auth | `terraform/main.tf`, CI/CD |
| 14 | **Secret Manager** | Store sensitive config values | Agent service account has `secretAccessor` role |
| 15 | **BigQuery** | Log agent interactions for analytics | `serving/callbacks.py` |
| 16 | **Cloud DLP** | PII detection in guardrails | Agent service account has `dlp.user` role |
| 17 | **Cloud Build** | API enabled for container builds | `terraform/main.tf` (API enablement) |
| 18 | **Compute Engine** | Required for Matching Engine endpoints | API enabled in Terraform |
| 19 | **Cloud Monitoring** | GCP native monitoring (API enabled) | API enabled in Terraform |

---

## Detailed Service Descriptions

### 1. Vertex AI Pipelines (KFP)
**What**: Managed Kubeflow Pipelines service on GCP.  
**Why**: Orchestrates our 3 sub-pipelines + master pipeline as directed acyclic graphs (DAGs).  
**Where**: `kfp_pipelines/` folder — each pipeline is defined as a `@dsl.pipeline` with `@dsl.component` steps.  
**Cost**: Pay per pipeline run (compute time of each component).

### 2. Vertex AI Vector Search (Matching Engine)
**What**: Managed vector similarity search at scale.  
**Why**: Stores document chunk embeddings and serves nearest-neighbor queries for RAG retrieval.  
**Where**: `src/llmops_pipeline/io/vector_db.py` — creates tree-AH indexes, deploys endpoints, ingests documents.  
**Config**: Index dimensions, distance metric, neighbors count in `confs/feature_engineering.yaml`.

### 3. Vertex AI Model Registry
**What**: Versioned model storage with staging/production labels.  
**Why**: Track which RAG config is in staging vs production, with audit trail.  
**Where**: `pipelines/deployment/register_model.py` — uploads model artifact, applies labels.

### 4. Vertex AI Experiments
**What**: Experiment tracking (similar to MLflow tracking).  
**Why**: Log metrics (eval scores), params (config values), and runs for each pipeline execution.  
**Where**: `io/services.py` → `VertexAIService` wraps `aiplatform.start_run()`, `log_metrics()`, `log_params()`.

### 5. Vertex AI Embeddings
**What**: Google's text embedding models (text-embedding-004).  
**Why**: Convert document chunks and queries into vector representations for similarity search.  
**Where**: `io/vector_db.py` uses `VertexAIEmbeddings` from LangChain.  
**Config**: Model name and dimensions in `confs/feature_engineering.yaml`.

### 6. Gemini 2.0 Flash
**What**: Google's fast, capable LLM.  
**Why**: Powers the RAG chatbot (agent), LLM-as-judge evaluation, and QA dataset generation.  
**Where**:
- Serving: `serving/agent.py` (via ADK `LlmAgent`)
- Evaluation: `deployment/evaluate_and_deploy.py` (Gemini rates answer quality)
- Dataset generation: `monitoring/generate_dataset.py` (Gemini generates QA pairs)

### 7. Cloud Run
**What**: Serverless container platform.  
**Why**: Hosts the ADK agent with auto-scaling, health checks, and zero cold-start in production.  
**Where**: `terraform/main.tf` (provisioning), `Dockerfile` (build), CI/CD (deploy).  
**Config**: Min/max instances, CPU/memory in Terraform. env vars via deploy step.

### 8. Cloud Storage (GCS)
**What**: Object storage.  
**Why**: Store source documents, embedding JSONL files, pipeline artifacts, model configs.  
**Where**: `io/services.py` → `GCSService`, plus every pipeline component that reads/writes data.  
**Buckets**: One per environment (`{project}-llmops-{env}`), created by Terraform.

### 9. Cloud Logging
**What**: Managed logging service.  
**Why**: Structured logs from Cloud Run + pipeline jobs. Monitoring pipeline pulls inference logs from here.  
**Where**:
- Writing: All `loguru` output goes to Cloud Logging on Cloud Run
- Reading: `monitoring/post_deploy_eval.py` queries logs with filters

### 10. Cloud Trace
**What**: Distributed request tracing.  
**Why**: Track latency and call chains across agent tools.  
**Where**: `serving/utils/observability.py` — OpenTelemetry SDK exports to Cloud Trace.

### 11. Artifact Registry
**What**: Docker image registry on GCP.  
**Why**: Store agent container images with immutable digests for secure deployments.  
**Where**: `terraform/main.tf` (provisioning), CI/CD (push images), Cloud Run (pull images).

### 12. IAM (Identity and Access Management)
**What**: Service accounts and role bindings.  
**Why**: Each component runs with least-privilege permissions.  
**Where**: `terraform/main.tf` — creates service accounts for agent and CI/CD, binds specific roles.  
**Roles assigned**:
- Agent SA: `aiplatform.user`, `storage.objectAdmin`, `logging.logWriter`, `cloudtrace.agent`, `bigquery.dataEditor`, `secretmanager.secretAccessor`, `dlp.user`
- CI/CD SA: `run.admin`, `artifactregistry.writer`, `iam.serviceAccountUser`, `storage.objectAdmin`, `aiplatform.user`

### 13. Workload Identity Federation (WIF)
**What**: Keyless auth from external identity providers.  
**Why**: GitHub Actions authenticates to GCP without service account keys — more secure.  
**Where**: `terraform/main.tf` (pool + provider), CI/CD (uses `google-github-actions/auth`).

### 14. Secret Manager
**What**: Secure storage for API keys and sensitive values.  
**Why**: Store credentials that shouldn't be in code or environment variables.  
**Where**: Agent SA has `secretAccessor` role — use via `google-cloud-secret-manager` SDK.

### 15. BigQuery
**What**: Serverless data warehouse.  
**Why**: Long-term storage of agent interactions for analytics, reporting, and evaluation datasets.  
**Where**: `serving/callbacks.py` → `InteractionLogger` optionally writes to BQ table.

### 16. Cloud DLP (Data Loss Prevention)
**What**: PII detection and de-identification.  
**Why**: Guardrails — scan agent responses for accidental PII exposure.  
**Where**: Agent SA has `dlp.user` role. Basic PII checks in `serving/callbacks.py`, extendable with DLP API.

---

## APIs to Enable

These APIs must be enabled on your GCP project. Terraform does this automatically, but for manual setup:

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

---

## Cost Considerations

| Service | Cost Driver | Estimate (Dev) |
|---------|------------|----------------|
| Cloud Run | CPU-seconds + memory | ~$5-15/month (min instances=0) |
| Vector Search | Index size + queries | ~$50-100/month (small index) |
| Gemini API | Input/output tokens | ~$1-10/month (low traffic) |
| GCS | Storage + operations | ~$1-5/month |
| Vertex Pipelines | Compute per run | ~$2-10/pipeline run |
| Artifact Registry | Storage | ~$1/month |
| BigQuery | Storage + queries | ~$1-5/month |
| Cloud Logging | Ingestion volume | Free tier usually sufficient |

**Total estimated dev cost: $60-150/month** (primarily Vector Search).  
Production costs scale with traffic and index size.
