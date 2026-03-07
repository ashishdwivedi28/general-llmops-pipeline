# GCP Resources, Pipeline Flow & Where to Find Everything

> **Document:** Which Google Cloud resources are used, what they do, where to see them, and how to navigate the pipeline flow  
> **Author:** Ashish Dwivedi  
> **Last Updated:** March 2026

---

## Table of Contents

1. [GCP Resources Map](#1-gcp-resources-map)
2. [Where to Find Stuff in GCP Console](#2-where-to-find-stuff-in-gcp-console)
3. [Pipeline Execution Flow](#3-pipeline-execution-flow)
4. [Data Flow Through the System](#4-data-flow-through-the-system)
5. [How the Serving Layer Connects to Everything](#5-how-the-serving-layer-connects-to-everything)
6. [Monitoring & Alerting Flow](#6-monitoring--alerting-flow)
7. [Cost & Usage Tracking Flow](#7-cost--usage-tracking-flow)
8. [Secret Management Flow](#8-secret-management-flow)
9. [CI/CD Pipeline Flow](#9-cicd-pipeline-flow)
10. [End-to-End User Request Flow](#10-end-to-end-user-request-flow)

---

## 1. GCP Resources Map

### All GCP Services Used

| GCP Service | What It Does in Our Pipeline | Terraform File | Console Path |
|-------------|------------------------------|----------------|-------------|
| **Vertex AI Pipelines** | Runs KFP-defined pipeline jobs (FE, Deploy, Monitor, Fine-tune) | — (submitted via API) | Console → Vertex AI → Pipelines |
| **Vertex AI Vector Search** | Stores embeddings, runs similarity search | Created by Phase 1 job | Console → Vertex AI → Vector Search |
| **Vertex AI Model Registry** | Registers champion model versions | Created by Phase 2 job | Console → Vertex AI → Model Registry |
| **Vertex AI Experiments** | Tracks evaluation metrics per pipeline run | Created by pipeline jobs | Console → Vertex AI → Experiments |
| **Vertex AI Tuning** | Supervised fine-tuning jobs | Submitted by Phase 5 | Console → Vertex AI → Tuning |
| **Cloud Run** | Hosts the FastAPI serving layer | `terraform/main/main.tf` | Console → Cloud Run |
| **Cloud Storage (GCS)** | Stores documents, embeddings, manifests, prompts, pipeline artifacts | `terraform/main/main.tf` | Console → Cloud Storage |
| **BigQuery** | Stores interaction logs, feedback, evaluations, costs | `terraform/main/bigquery.tf` | Console → BigQuery |
| **Artifact Registry** | Docker image storage | `terraform/bootstrap/main.tf` | Console → Artifact Registry |
| **Secret Manager** | API keys, provider keys | `terraform/main/secrets.tf` | Console → Secret Manager |
| **Cloud Scheduler** | Triggers pipelines on schedule | `terraform/main/scheduler.tf` | Console → Cloud Scheduler |
| **Cloud Monitoring** | Alerting on errors, latency, quality | `terraform/main/monitoring.tf` | Console → Monitoring |
| **Cloud Logging** | Structured application logs | Auto (agent SA has logging.logWriter) | Console → Logging |
| **Cloud Trace** | Distributed request tracing | Auto (OpenTelemetry) | Console → Trace |
| **Cloud DLP** | PII detection and redaction | Auto (agent SA has dlp.user) | Console → DLP |
| **IAM** | Service accounts, roles, WIF | `terraform/bootstrap/main.tf` | Console → IAM & Admin |
| **API Gateway** | Optional production auth gateway | `terraform/main/api_gateway.tf` | Console → API Gateway |

### Service Account Roles

**Agent SA** (`llmops-agent-dev@project.iam`):
| Role | Purpose |
|------|---------|
| `aiplatform.user` | Run pipelines, access Vector Search, tuning |
| `storage.objectAdmin` | Read/write documents, manifests, prompts |
| `logging.logWriter` | Write structured logs |
| `cloudtrace.agent` | Send traces to Cloud Trace |
| `bigquery.dataEditor` | Write interactions, feedback, evaluations, costs |
| `secretmanager.secretAccessor` | Read API keys |
| `dlp.user` | PII detection |

**CI/CD SA** (`llmops-cicd-dev@project.iam`):
| Role | Purpose |
|------|---------|
| `run.admin` | Deploy Cloud Run services |
| `artifactregistry.writer` | Push Docker images |
| `iam.serviceAccountUser` | Impersonate agent SA for deployments |
| `storage.objectAdmin` | Upload pipeline artifacts |
| `aiplatform.user` | Submit pipeline jobs |

---

## 2. Where to Find Stuff in GCP Console

### Vertex AI Pipelines (Pipeline Runs)

**Path:** `Console → Vertex AI → Pipelines → Runs`

Here you see:
- **All pipeline runs** — master, feature_engineering, deployment, monitoring, fine_tuning
- **Status** — Running, Succeeded, Failed
- **DAG visualization** — Click a run to see the step-by-step graph
- **Step logs** — Click any step to see its Python output logs
- **Artifacts** — Input/output parameters for each step

**Tip:** Filter by `Display Name` to find specific pipeline runs.

### Vertex AI Vector Search (Embeddings)

**Path:** `Console → Vertex AI → Vector Search`

Two resources:
1. **Indexes** — The embedding index (created by Phase 1)
   - Shows: index name, creation time, dimensions, approximate count
2. **Index Endpoints** — The deployed endpoint
   - Shows: endpoint URL, deployed indexes, traffic info

### Vertex AI Model Registry

**Path:** `Console → Vertex AI → Model Registry`

Shows registered model versions with:
- Model display name
- Version ID
- Creation timestamp
- Container image (serving)

### Cloud Run (Serving Layer)

**Path:** `Console → Cloud Run → Services → llmops-agent-dev`

Here you see:
- **URL** — The public endpoint URL
- **Revisions** — All deployed versions
- **Metrics** — Request count, latency, container instances
- **Logs** — Click "Logs" tab to see server output
- **Environment variables** — Under "Edit & Deploy a New Revision"
- **Traffic** — Which revision gets what % of traffic

### Cloud Storage (GCS)

**Path:** `Console → Cloud Storage → Buckets → {project}-llmops-dev`

Bucket structure:
```
{project}-llmops-dev/
├── documents/                    # Raw uploaded documents
├── embeddings/                   # Generated embeddings
├── manifests/
│   └── llmops-app/
│       └── latest.json           # ← THE MANIFEST (the bridge)
├── prompts/
│   └── llmops-app/
│       ├── v1.yaml               # Prompt version 1
│       ├── v2.yaml               # Prompt version 2
│       └── v3.yaml               # Prompt version 3
├── pipeline_root/                # Vertex AI Pipeline artifacts
├── fine_tuning/
│   └── datasets/
│       ├── train.jsonl           # Training data
│       └── test.jsonl            # Test data
└── signals/
    └── retrigger_fe.json         # Self-healing trigger signal
```

### BigQuery (Analytics)

**Path:** `Console → BigQuery → {project} → llmops`

| Table | What You See | Key Columns |
|-------|-------------|-------------|
| `interactions` | All user chat interactions | session_id, query, response, latency_ms, model, tokens, timestamp |
| `feedback` | User ratings | session_id, rating (1-5), comment, timestamp |
| `evaluations` | Pipeline evaluation results | run_id, metrics (JSON), quality_gate_passed, timestamp |
| `costs` | Per-request costs | model, input_tokens, output_tokens, cost_usd, timestamp |

**Useful queries:**

```sql
-- Recent interactions
SELECT timestamp, session_id, query, response, latency_ms, model
FROM `llmops.interactions`
ORDER BY timestamp DESC
LIMIT 20;

-- Average latency per model
SELECT model, AVG(latency_ms) as avg_latency, COUNT(*) as requests
FROM `llmops.interactions`
GROUP BY model;

-- Feedback summary
SELECT rating, COUNT(*) as count
FROM `llmops.feedback`
GROUP BY rating
ORDER BY rating;

-- Total cost by model
SELECT model, SUM(cost_usd) as total_cost, SUM(input_tokens + output_tokens) as total_tokens
FROM `llmops.costs`
GROUP BY model;
```

### Cloud Logging (Application Logs)

**Path:** `Console → Logging → Logs Explorer`

**Useful filters:**
```
resource.type="cloud_run_revision"
resource.labels.service_name="llmops-agent-dev"
```

For structured query logs:
```
resource.type="cloud_run_revision"
jsonPayload.event="interaction"
```

For error logs:
```
resource.type="cloud_run_revision"
severity>=ERROR
```

### Cloud Monitoring (Alerts)

**Path:** `Console → Monitoring → Alerting`

Configured alerts:
| Alert | Condition | Notification |
|-------|-----------|-------------|
| Error Rate | >5% 5xx responses over 5 min | Email |
| Latency | p95 > 10 seconds | Email |
| Quality Degradation | Custom log-based metric | Email |

**Path to metrics:** `Console → Monitoring → Metrics Explorer`

### Secret Manager

**Path:** `Console → Secret Manager`

| Secret | Purpose |
|--------|---------|
| `llmops-api-keys` | Hashed API keys for gateway auth |
| `llmops-openai-key` | OpenAI API key (for fallback provider) |
| `llmops-anthropic-key` | Anthropic API key (for fallback provider) |

### Cloud Scheduler

**Path:** `Console → Cloud Scheduler`

| Job | Schedule | Target |
|-----|----------|--------|
| `llmops-monitoring-daily` | Every day at 2 AM UTC | Vertex AI Pipelines REST API (monitoring) |
| `llmops-master-weekly` | Every Sunday at 3 AM UTC | Vertex AI Pipelines REST API (master) |

### Artifact Registry

**Path:** `Console → Artifact Registry → llmops-agent-dev`

Shows all Docker images with tags (commit SHA + `latest`). Each CI/CD push creates a new tagged image.

---

## 3. Pipeline Execution Flow

### Master Pipeline — Full Flow

```
                  ┌────────────────────┐
                  │  TRIGGER            │
                  │  • Cloud Scheduler  │
                  │  • GitHub Actions   │
                  │  • Manual submit    │
                  └─────────┬──────────┘
                            │
                  ┌─────────▼──────────────────┐
                  │  Phase 1: Feature Eng.      │
                  │  1. Ingest documents from    │
                  │     GCS/local                │
                  │  2. Chunk (configurable size) │
                  │  3. Generate embeddings      │
                  │  4. Create/update Vector     │
                  │     Search index             │
                  │  5. Deploy to endpoint       │
                  │  6. Update manifest (FE)     │
                  └─────────┬──────────────────┘
                            │
                  ┌─────────▼──────────────────┐
                  │  Phase 2: Optimization       │
                  │  1. Generate eval dataset    │
                  │     (Q&A pairs from docs)    │
                  │  2. Evaluate N prompts ×     │
                  │     M models                 │
                  │  3. Gemini-as-judge scoring  │
                  │  4. Quality gate check       │
                  │  5. Register best combo      │
                  │     in Model Registry        │
                  │  6. Update manifest (Deploy) │
                  └─────────┬──────────────────┘
                            │
                  ┌─────────▼──────────────────┐
                  │  Phase 3: Deployment         │
                  │  1. Deploy to Cloud Run      │
                  │  2. Run smoke tests          │
                  │  3. Canary traffic split     │
                  │     (10% → 50% → 100%)      │
                  │  4. Rollback if tests fail   │
                  └─────────┬──────────────────┘
                            │
                  ┌─────────▼──────────────────┐
                  │  Phase 4: Self-Healing       │
                  │  1. Evaluate production      │
                  │     quality (Gemini-judge)   │
                  │  2. Check: degraded?         │
                  │     ├── No → Done ✅         │
                  │     └── Yes → Continue ↓     │
                  │  3. Diagnose root cause      │
                  │     (data? prompt? infra?)   │
                  │  4. Remediate:               │
                  │     • Retrigger Phase 1      │
                  │     • Rollback prompt         │
                  │     • Alert human            │
                  └─────────┬──────────────────┘
                            │ (if sufficient feedback)
                  ┌─────────▼──────────────────┐
                  │  Phase 5: Fine-Tuning (opt.) │
                  │  1. Query BQ for high-       │
                  │     quality interactions     │
                  │  2. Format as JSONL          │
                  │  3. Submit SFT to Vertex AI  │
                  │  4. Evaluate vs base model   │
                  │  5. Register if better       │
                  └────────────────────────────┘
```

### Individual Pipeline Submission

You can run any phase independently:

```bash
# Just feature engineering
python -m kfp_pipelines.compile_and_run --pipeline feature_engineering --project X --bucket Y

# Just monitoring (most common standalone run)
python -m kfp_pipelines.compile_and_run --pipeline monitoring --project X --bucket Y

# Full master
python -m kfp_pipelines.compile_and_run --pipeline master --project X --bucket Y
```

---

## 4. Data Flow Through the System

### Documents → Searchable Knowledge

```
Local files / GCS           Chunked text            Embeddings (768d)         Vertex AI Vector Search
data/documents/*.pdf   →    chunks[]           →    float vectors[]      →    Index + Endpoint
                            (LangChain             (text-embedding-004)       (Deployed, searchable)
                             RecursiveCharSplitter)
```

### User Query → Response

```
User: "What is the leave policy?"
  ↓
API Gateway (auth, rate limit)
  ↓
FastAPI /chat endpoint
  ↓
Input guardrail check
  ↓
Task Detection → "general_qa" → rag_search tool
  ↓
Vector Search → top 10 similar chunks
  ↓
Gemini 2.0 Flash (with system prompt + retrieved context)
  ↓
Output guardrail check
  ↓
Response + log to BigQuery
  ↓
{"response": "The leave policy states...", "latency_ms": 850}
```

### Feedback → Fine-Tuning

```
User gives 👍 (rating=5)
  ↓
POST /feedback → BigQuery feedback table
  ↓
(Accumulated over time)
  ↓
Fine-tuning pipeline → Query BQ WHERE rating >= 4
  ↓
Format as JSONL: {"input": query, "output": response}
  ↓
Vertex AI SFT Job → Fine-tuned model
  ↓
Evaluate vs base → Register if better
```

---

## 5. How the Serving Layer Connects to Everything

```
                                ┌──────────────────────┐
                                │   Cloud Run Server    │
                                │                       │
      ┌─── reads ──────────────→│  ManifestWatcher      │←── polls GCS every 120s
      │                         │                       │
      │    ┌── reads ──────────→│  PromptRegistry       │←── loads prompts from GCS
      │    │                    │                       │
      │    │    ┌── calls ─────→│  ModelRouter           │←── LiteLLM + fallback chain
      │    │    │               │                       │
      │    │    │    ┌── logs ──│  InteractionLogger     │──→ Cloud Logging + BigQuery
      │    │    │    │          │                       │
      │    │    │    │  ┌── wr │  CostTracker           │──→ BigQuery costs table
      │    │    │    │  │      │                       │
GCS   │    │    │    │  │      │  GatewayMiddleware     │──→ Secret Manager (API keys)
Manifest   Prompts  Gemini  Logs  Costs                │
```

---

## 6. Monitoring & Alerting Flow

```
Cloud Scheduler (daily 2 AM)
  ↓
Triggers Monitoring Pipeline (Vertex AI)
  ↓
Step 1: evaluate_quality
  • Pull recent interactions from Cloud Logging
  • Gemini-as-judge scores each (relevance, faithfulness, toxicity)
  • Write scores to BigQuery evaluations table
  ↓
Step 2: diagnose_degradation (if degraded)
  • Compare metrics against thresholds
  • Query BigQuery for latency p95, error rate
  • Determine primary cause (data_drift, prompt_degradation, infrastructure_issue)
  ↓
Step 3: remediate
  • Data drift → write retrigger signal to GCS
  • Prompt issue → rollback to previous version via PromptRegistry
  • Infra issue → alert via Cloud Logging
  ↓
Cloud Monitoring Alerts
  • Error rate > 5% → email notification
  • Latency p95 > 10s → email notification
  • Quality degradation log → email notification
```

---

## 7. Cost & Usage Tracking Flow

```
Every /chat request
  ↓
CostTracker.record()
  • Count input tokens
  • Count output tokens
  • Calculate cost_usd from model pricing
  ↓
BigQuery costs table
  • model, app_id, input_tokens, output_tokens, cost_usd, timestamp
  ↓
/costs endpoint (GET)
  • Returns total_cost_usd, by_model breakdown, by_app breakdown
  ↓
Streamlit Dashboard → Cost Analytics page
  • Visualizes: cost trends, model comparison, daily costs
```

---

## 8. Secret Management Flow

```
Terraform creates secrets
  ↓
terraform/main/secrets.tf
  • llmops-api-keys     (gateway auth)
  • llmops-openai-key   (LLM provider)
  • llmops-anthropic-key (LLM provider)
  ↓
CI/CD fetches at deploy time
  • gcloud secrets versions access latest --secret="llmops-openai-key"
  • Passed to Cloud Run as env vars
  ↓
Runtime
  • Gateway middleware reads API keys from Secret Manager
  • ModelRouter reads provider keys from environment
```

---

## 9. CI/CD Pipeline Flow

```
Developer pushes feature/* branch
  ↓
GitHub Actions: Lint & Test only
  • ruff check, ruff format, pytest
  ↓
Open PR: feature/* → main
  • Same: Lint & Test
  • Branch protection blocks merge until green ✅
  ↓
Merge to main
  ↓
Stage 1: Lint & Test (again, on main)
  ↓
Stage 2: Build Docker image → push to Artifact Registry
  ↓
Stage 3: Deploy to Cloud Run
  • Fetch secrets from Secret Manager
  • gcloud run deploy with env vars
  ↓
(Optional) Stage 4: Submit Vertex AI Pipeline
  • Only on workflow_dispatch with run_pipeline=true
  ↓
(Automatic) Stage 5: Scheduled Monitoring
  • Daily cron: submit monitoring pipeline
```

---

## 10. End-to-End User Request Flow

Complete flow from user typing a question to seeing the answer:

```
1. User types: "What is the leave policy?"
   
2. Browser sends POST /chat to Cloud Run URL
   
3. Cloud Run → API Gateway middleware
   • Validates API key (SHA-256 hash check)
   • Checks rate limit (sliding window per key)
   • Checks RBAC (is this route allowed for this role?)
   
4. FastAPI → Input guardrail
   • Topic filter: is this question within valid topics?
   • Injection detection: is this a prompt injection attempt?
   
5. Task Detection
   • Classify query → "general_qa" → use rag_search tool
   
6. ADK Agent processes query
   a. Query rewriter: "leave policy" → "annual leave, sick leave, PTO policy"
   b. Vector Search: find top 10 similar document chunks
   c. Gemini 2.0 Flash: generate answer using context + system prompt
   
7. Output guardrail
   • Check for PII, toxicity, hallucination flags
   
8. Log everything
   • Cloud Logging: structured log entry
   • BigQuery interactions: full Q&A for analytics
   • CostTracker: token count + cost calculation → BigQuery costs
   
9. Return response
   {"response": "The leave policy allows 20 days annual leave...", 
    "session_id": "user-123", "latency_ms": 850}

10. User clicks 👍
    POST /feedback → BigQuery feedback table
```

---

## Navigation Quick Reference

| I Want To... | Where To Look |
|-------------|---------------|
| See pipeline run status | Vertex AI → Pipelines → Runs |
| Check if agent is healthy | Cloud Run → Services → llmops-agent-dev → Metrics |
| View user conversations | BigQuery → llmops.interactions |
| See quality over time | BigQuery → llmops.evaluations |
| Check costs | BigQuery → llmops.costs OR `/costs` endpoint |
| View feedback | BigQuery → llmops.feedback |
| See deployment logs | Cloud Run → Logs |
| Check active alerts | Monitoring → Alerting |
| See vector index | Vertex AI → Vector Search |
| Check prompts | GCS → {bucket}/prompts/{app_id}/ |
| See the manifest | GCS → {bucket}/manifests/{app_id}/latest.json |
| View Docker images | Artifact Registry → llmops-agent-dev |
| Check secrets | Secret Manager |
| View scheduled jobs | Cloud Scheduler |
| See CI/CD runs | GitHub → Actions tab |
