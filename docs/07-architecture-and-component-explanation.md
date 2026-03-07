# LLMOps Pipeline — Architecture & Component Explanation

> **Document:** Deep-dive into the pipeline architecture, components, and how they work together  
> **Author:** Ashish Dwivedi  
> **Last Updated:** March 2026

---

## Table of Contents

1. [What Is This Project?](#1-what-is-this-project)
2. [Core Design Principles](#2-core-design-principles)
3. [10-Layer Architecture Overview](#3-10-layer-architecture-overview)
4. [Component Deep-Dive](#4-component-deep-dive)
5. [5 Pipeline Phases](#5-5-pipeline-phases)
6. [The Offline ↔ Online Bridge (Manifest System)](#6-the-offline--online-bridge-manifest-system)
7. [Config-Driven Design](#7-config-driven-design)
8. [Security Architecture](#8-security-architecture)
9. [Observability Stack](#9-observability-stack)
10. [Self-Healing Loop](#10-self-healing-loop)
11. [Technology Stack](#11-technology-stack)

---

## 1. What Is This Project?

This is a **production-grade, general-purpose LLMOps pipeline** built on Google Cloud Platform. It can power **any** LLM application — RAG chatbot, SQL agent, Drive copilot, multi-agent system — by changing only YAML configuration files.

### The Core Principle

> **One pipeline. Any application. Change the config, not the code.**

### What "General-Purpose" Means

| Dimension | How We Generalize |
|-----------|------------------|
| **Use Case** | Task Detection Layer routes to the right tool/flow based on user query |
| **Model** | Model Abstraction Layer supports Vertex AI, OpenAI, Anthropic — swap via config |
| **Data** | Config points to any GCS path; pipeline ingests whatever documents are there |
| **Prompts** | Prompt Registry with versioning, A/B testing, auto-select best performer |
| **Evaluation** | Pluggable evaluators (Gemini-as-judge, custom metrics) — thresholds in config |
| **Infrastructure** | Terraform modules are parameterized; same IaC works across environments |

---

## 2. Core Design Principles

### 2.1 Config-Driven Everything

Every behavior is controlled by YAML configuration files in `confs/`. Changing the application, model, thresholds, or pipeline behavior requires **zero code changes** — only config updates.

```
confs/
├── feature_engineering.yaml    # Phase 1: document ingestion
├── deployment.yaml             # Phase 2+3: evaluation & deployment
├── monitoring.yaml             # Phase 4: quality monitoring
├── fine_tuning.yaml            # Phase 5: model fine-tuning
├── models.yaml                 # Model routing & failover config
├── evaluation.yaml             # Evaluation metrics & thresholds
├── gateway.yaml                # API auth, rate limiting, RBAC
├── rag_chain_config.yaml       # RAG-specific parameters
├── generate_dataset.yaml       # Eval dataset generation
└── app/
    └── hr_chatbot.yaml         # App-specific task detection config
```

### 2.2 Discriminated Union Dispatch

The CLI reads a YAML file, parses it into a Python dict, and Pydantic's discriminated union dispatch routes to the correct job class based on the `KIND` field:

```yaml
# confs/feature_engineering.yaml
job:
  KIND: FeatureEngineeringJob    # ← this determines which Python class runs
  project: my-project
  ...
```

The `JobKind` type union contains all 15 job types:

```
JobKind = FeatureEngineeringJob | DeploymentJob | MonitoringJob | FineTuningJob
        | IngestDocumentsJob | CreateVectorDbJob | GenerateDatasetJob
        | PostDeployEvalJob | EvaluateAndDeployJob | RegisterModelJob
        | DiagnoseJob | RemediateJob | PrepareDatasetJob | TrainJob
        | EvaluateFineTunedJob
```

### 2.3 Job Lifecycle Pattern

Every job follows the same lifecycle:

```python
class MyJob(Job, frozen=True):
    KIND: Literal["MyJob"] = "MyJob"
    
    def run(self) -> Locals:
        # Business logic here
        return {"result": "data"}
```

Jobs are used as context managers:
```python
with job as runner:
    result = runner.run()
```

The `__enter__` initializes services (Vertex AI, logging), `run()` executes logic, `__exit__` cleans up.

### 2.4 Pydantic Strict + Frozen Models

All data models use Pydantic v2 with `strict=True` and `frozen=True`. This means:
- **strict**: No implicit type coercion (string "5" won't become int 5)
- **frozen**: Models are immutable after creation (thread-safe, hashable)

---

## 3. 10-Layer Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1: CLIENT LAYER                                              │
│  Web Chat UI │ Admin Dashboard (Streamlit) │ API Clients            │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
┌─────────────────────────────────────┼───────────────────────────────┐
│  LAYER 2: API GATEWAY                                               │
│  Auth (API Key / JWT) │ Rate Limiting │ RBAC │ Cost Attribution     │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
┌─────────────────────────────────────┼───────────────────────────────┐
│  LAYER 3: SERVING LAYER (Cloud Run / FastAPI)                       │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────┐    │
│  │ Task Detection   │  │ Model Abstraction │  │ Prompt Manager  │    │
│  │ (Agentic Router)│  │ (LiteLLM Router) │  │ (GCS Registry)  │    │
│  └─────────────────┘  └──────────────────┘  └─────────────────┘    │
│  ┌──────────────┐  ┌────────────────┐  ┌────────────────────────┐  │
│  │ Guardrails   │  │ Cost Tracker   │  │ Callbacks/Observability│  │
│  └──────────────┘  └────────────────┘  └────────────────────────┘  │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
┌─────────────────────────────────────┼───────────────────────────────┐
│  LAYER 4: RETRIEVAL LAYER                                           │
│  Vertex AI Vector Search │ RAG Engine │ FAISS (local) │ No-RAG     │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
┌─────────────────────────────────────┼───────────────────────────────┐
│  LAYER 5: OFFLINE PIPELINE SYSTEM (Vertex AI Pipelines / KFP)       │
│  Phase 1: Feature Engineering                                       │
│  Phase 2+3: Prompt/Model Optimization + Deployment                  │
│  Phase 4: Monitoring & Self-Healing                                 │
│  Phase 5: Fine-Tuning (Optional)                                    │
│  Master Pipeline: Orchestrates Phase 1 → 2 → 3 → 4 → [5]          │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
┌─────────────────────────────────────┼───────────────────────────────┐
│  LAYER 6: DATA & ARTIFACT LAYER                                     │
│  GCS (docs, embeddings, manifests) │ BigQuery (logs, costs)         │
│  Pipeline Artifact Manifest (the bridge)                            │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
┌─────────────────────────────────────┼───────────────────────────────┐
│  LAYER 7: INFRASTRUCTURE LAYER                                      │
│  Terraform (IaC) │ GitHub Actions (CI/CD) │ Secret Manager │ Cron  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Deep-Dive

### 4.1 Pipeline Artifact Manifest (The Bridge)

**Problem:** The offline pipeline creates artifacts (vector index, model registration, prompt version) but the online serving layer doesn't know about them.

**Solution:** A JSON manifest stored at `gs://{bucket}/manifests/{app_id}/latest.json`.

```json
{
  "app_id": "llmops-app",
  "version": "5",
  "feature_engineering": {
    "vector_index_resource_name": "projects/.../indexes/123",
    "vector_endpoint_resource_name": "projects/.../indexEndpoints/456",
    "embedding_model": "text-embedding-004",
    "num_documents": 150,
    "num_chunks": 2340,
    "documents_hash": "sha256:abc..."
  },
  "deployment": {
    "active_model": "gemini-2.0-flash",
    "active_prompt_version": "v3",
    "eval_scores": {"relevance": 0.92, "faithfulness": 0.88},
    "quality_gate_passed": true
  },
  "monitoring": {
    "monitoring_scores": {"relevance": 0.89, "faithfulness": 0.85},
    "degraded": false,
    "status": "healthy"
  }
}
```

**How it works:**
1. Each pipeline phase writes its section via `update_section()` (read-modify-write)
2. The serving layer creates a `ManifestWatcher` that polls for changes
3. On refresh, the server auto-adapts: new vector endpoint, new model, new prompt version

### 4.2 Model Abstraction Layer (Model Router)

**Problem:** Hardcoded to Gemini. No failover, no multi-provider support.

**Solution:** A `ModelRouter` class wrapping LiteLLM for 100+ model support.

```yaml
# confs/models.yaml
chat:
  primary: vertex_ai/gemini-2.0-flash
  fallback:
    - vertex_ai/gemini-1.5-pro
    - openai/gpt-4o
  timeout: 30
  max_retries: 2
embedding:
  primary: vertex_ai/text-embedding-004
  dimensions: 768
evaluation:
  primary: vertex_ai/gemini-1.5-pro
```

**Features:**
- Automatic failover: if Gemini fails, falls through to GPT-4o
- Retry with exponential backoff
- Token counting and cost calculation per request
- Unified `chat()`, `embed()`, `generate()` interface

### 4.3 Prompt Registry

**Problem:** Single hardcoded prompt, no versioning or A/B testing.

**Solution:** Versioned prompts in GCS at `gs://{bucket}/prompts/{app_id}/v{N}.yaml`.

```yaml
# gs://bucket/prompts/hr-chatbot/v3.yaml
version: 3
description: "Added explicit citation instructions"
system_prompt: |
  You are a helpful HR assistant...
query_rewriter_prompt: |
  Rewrite the query for better retrieval: {query}
tool_instructions:
  rag_search: "Use when user asks about HR policies"
variables:
  company_name: "Acme Corp"
```

**Features:**
- Save/load prompt versions to GCS
- A/B traffic splitting (e.g., 80% v3, 20% v4)
- Template variable resolution (`${VAR_NAME}`)
- Pipeline evaluates all versions, auto-selects best

### 4.4 Task Detection Layer

**Problem:** The agent doesn't know which tool to use for different query types.

**Solution:** A `TaskDetector` class that classifies queries using keywords and/or LLM.

```yaml
# confs/app/hr_chatbot.yaml
task_detection:
  tasks:
    - name: general_qa
      description: "General HR policy questions"
      keywords: ["policy", "rules", "guidelines"]
      tool: rag_search
    - name: payroll
      description: "Salary, pay, compensation"
      keywords: ["salary", "paycheck", "bonus"]
      tool: payroll_lookup
  default_task: general_qa
  method: keyword  # keyword | llm | keyword_and_llm
```

### 4.5 API Gateway Middleware

**Problem:** No authentication, rate limiting, or authorization.

**Solution:** FastAPI middleware (`GatewayMiddleware`) that runs before every request.

```yaml
# confs/gateway.yaml
auth:
  enabled: true
  methods: [api_key, jwt]
rate_limiting:
  enabled: true
  tiers:
    default: {requests_per_minute: 60}
    premium: {requests_per_minute: 300}
routes:
  - path: /chat
    methods: [POST]
    roles: [user, admin]
  - path: /costs
    methods: [GET]
    roles: [admin]
```

**Features:**
- API key validation (SHA-256 hashed, stored in Secret Manager)
- JWT validation via Google OAuth2 ID tokens
- Sliding-window rate limiting per API key
- Role-based access control per endpoint
- Cost attribution headers (`X-App-ID`, `X-User-ID`)

### 4.6 Cost Tracker

**Problem:** No visibility into per-request or per-pipeline token costs.

**Solution:** A `CostTracker` class that records every LLM call's token usage and cost.

**How it works:**
1. Every chat request counts input/output tokens
2. Cost calculated using provider pricing (or LiteLLM's built-in)
3. Each record logged to BigQuery `costs` table
4. Aggregated summaries available via `/costs` endpoint
5. Dashboard visualizes cost trends per model, per app

### 4.7 Callbacks & Observability

| Signal | Destination | Purpose |
|--------|-------------|---------|
| Interaction logs | BigQuery `interactions` | Full Q&A log for analytics |
| Feedback | BigQuery `feedback` | User ratings for training |
| Structured logs | Cloud Logging | Debugging, audit trail |
| Traces | Cloud Trace (OpenTelemetry) | Latency profiling |
| Metrics | Cloud Monitoring | Alerting on degradation |
| Evaluation scores | BigQuery `evaluations` | Quality trend tracking |

---

## 5. 5 Pipeline Phases

### Phase 1: Feature Engineering

```
Ingest Documents → Chunk → Embed → Create Vector DB → Update Manifest
```

- **Input:** Documents in `data/documents/` or GCS path
- **Output:** Vertex AI Vector Search index + endpoint, manifest update
- **Config:** `confs/feature_engineering.yaml`
- **Jobs:** `IngestDocumentsJob` → `CreateVectorDbJob` (managed by `FeatureEngineeringJob`)

### Phase 2+3: Optimization & Deployment

```
Generate Eval Set → Evaluate Prompts/Models → Quality Gate → Register Model → Deploy → Smoke Test
```

- **Input:** RAG chain config, evaluation config
- **Output:** Best model+prompt combo registered, deployed to Cloud Run
- **Config:** `confs/deployment.yaml`
- **Jobs:** `GenerateDatasetJob` → `PostDeployEvalJob` → `EvaluateAndDeployJob` → `RegisterModelJob` (managed by `DeploymentJob`)

### Phase 4: Monitoring & Self-Healing

```
Evaluate Production Quality → Diagnose Root Cause → Remediate (or Alert)
```

- **Input:** Production Cloud Logging traces
- **Output:** Quality scores, diagnosis report, remediation actions
- **Config:** `confs/monitoring.yaml`
- **Jobs:** `PostDeployEvalJob` → `DiagnoseJob` → `RemediateJob` (managed by `MonitoringJob`)

**Remediation Actions:**
| Diagnosis | Action |
|-----------|--------|
| Data drift (relevance drop) | Retrigger Feature Engineering (Phase 1) |
| Prompt degradation (faithfulness drop) | Rollback to previous prompt version |
| Infrastructure issue (latency/errors) | Alert human via Cloud Monitoring |
| Unknown | Alert human for manual investigation |

### Phase 5: Fine-Tuning (Optional)

```
Prepare Dataset → Submit Fine-Tuning Job → Evaluate vs Base → Register if Better
```

- **Input:** High-quality interactions from BigQuery (rating >= 4)
- **Output:** Fine-tuned model (if quality gate passes)
- **Config:** `confs/fine_tuning.yaml`
- **Jobs:** `PrepareDatasetJob` → `TrainJob` → `EvaluateFineTunedJob` (managed by `FineTuningJob`)

### Master Pipeline (Orchestrator)

The master pipeline chains all phases in sequence:

```
Phase 1 (FE) → Phase 2+3 (Deploy) → Phase 4 (Monitor+Self-Heal) → [Phase 5 (Fine-Tune)]
```

It runs on Vertex AI Pipelines and is triggered by:
- **Cloud Scheduler** — Weekly automatic run
- **CI/CD** — On workflow dispatch with `run_pipeline=true`
- **Manual** — Via `python -m kfp_pipelines.compile_and_run --pipeline master`

---

## 6. The Offline ↔ Online Bridge (Manifest System)

This is the most critical architectural pattern in the system.

```
┌──────────────────┐         gs://bucket/manifests/app/latest.json         ┌──────────────────┐
│  OFFLINE SYSTEM  │  ──────────────── WRITES ──────────────────────────→  │   GCS MANIFEST   │
│  (KFP Pipelines) │                                                      │                  │
│                  │  Phase 1 writes: vector_index, embedding info        │  { "app_id": ... │
│                  │  Phase 2 writes: active_model, prompt_version       │    "version": 5  │
│                  │  Phase 4 writes: monitoring_scores, status           │    ...           │
└──────────────────┘                                                      └────────┬─────────┘
                                                                                   │
                                                                            READS (periodic)
                                                                                   │
                                                                          ┌────────▼─────────┐
                                                                          │  ONLINE SYSTEM   │
                                                                          │  (Cloud Run)     │
                                                                          │                  │
                                                                          │  ManifestWatcher │
                                                                          │  auto-refreshes  │
                                                                          └──────────────────┘
```

**Without the manifest**, you'd need to manually update environment variables on Cloud Run every time the pipeline creates a new vector index or selects a new model. **With the manifest**, it's automatic.

---

## 7. Config-Driven Design

### How a Config File Becomes a Running Pipeline

```
YAML File (confs/feature_engineering.yaml)
    ↓  OmegaConf.load()
DictConfig (with ${oc.env:VAR} resolved)
    ↓  OmegaConf.to_container()
Python dict
    ↓  MainSettings.model_validate()
MainSettings(job=FeatureEngineeringJob(...))     ← Pydantic discriminated union dispatch via KIND
    ↓  with setting.job as runner
Job context manager (initializes Vertex AI, logging)
    ↓  runner.run()
Pipeline execution
```

### Switching Use Cases

To switch from an HR chatbot to a SQL agent:

1. Change `confs/app/hr_chatbot.yaml` to your new app config
2. Update `confs/feature_engineering.yaml` to point to your documents
3. Update `confs/deployment.yaml` with your model preferences
4. **Zero code changes required**

---

## 8. Security Architecture

| Layer | Mechanism | Configuration |
|-------|-----------|---------------|
| **API Gateway** | API Key + JWT validation | `confs/gateway.yaml` |
| **Rate Limiting** | Sliding-window per-key | `confs/gateway.yaml` → `rate_limiting` |
| **RBAC** | Role-based endpoint auth | `confs/gateway.yaml` → `routes` |
| **Secrets** | Google Secret Manager | `terraform/main/secrets.tf` |
| **Input Guardrails** | Topic filtering, injection detection | `serving/callbacks.py` |
| **Output Guardrails** | PII redaction, toxicity filter | `serving/callbacks.py` |
| **IAM** | 2 service accounts (agent + cicd) | `terraform/bootstrap/main.tf` |
| **WIF** | Keyless GitHub → GCP auth | `terraform/bootstrap/main.tf` |

---

## 9. Observability Stack

```
┌─────────────────────────────────────────────────┐
│  Every Request                                   │
│  ┌─────────────┐  ┌──────────────┐              │
│  │ Input Guard │→ │ Agent (LLM)  │→ Output Check│
│  └──────┬──────┘  └──────┬───────┘              │
│         │                │                       │
│    ┌────▼────┐     ┌─────▼─────┐                │
│    │ Cloud   │     │ BigQuery  │                │
│    │ Logging │     │ (interact │                │
│    │ (struct)│     │  + costs) │                │
│    └─────────┘     └───────────┘                │
│                                                  │
│    ┌─────────┐     ┌───────────┐                │
│    │ Cloud   │     │ Cloud     │                │
│    │ Trace   │     │ Monitor   │                │
│    │ (OTel)  │     │ (alerts)  │                │
│    └─────────┘     └───────────┘                │
└─────────────────────────────────────────────────┘
```

---

## 10. Self-Healing Loop

The self-healing loop is the closed feedback cycle:

```
Deploy → Serve → Monitor → Diagnose → Remediate → (Re-Deploy)
  ↑                                                    │
  └────────────────────────────────────────────────────┘
```

**How it works in practice:**

1. **Phase 3 deploys** a new model+prompt combo to Cloud Run
2. **Users interact** with the chatbot; interactions logged to BigQuery
3. **Phase 4 (monitoring)** runs on schedule (daily via Cloud Scheduler)
4. **PostDeployEvalJob** evaluates recent interactions with Gemini-as-judge
5. If metrics drop below thresholds → **DiagnoseJob** runs root-cause analysis
6. **RemediateJob** dispatches automatic fixes:
   - Retriggering Feature Engineering
   - Rolling back to a previous prompt version
   - Alerting human (for infrastructure issues)
7. The cycle repeats automatically

---

## 11. Technology Stack

| Category | Technology | Purpose |
|----------|-----------|---------|
| **Runtime** | Python 3.11+ | Application language |
| **Config** | OmegaConf + Pydantic v2 | YAML parsing + validation |
| **Serving** | FastAPI + Uvicorn | HTTP API server |
| **Agent** | Google ADK (Agent Development Kit) | LLM agent framework |
| **Models** | LiteLLM | Multi-provider model routing |
| **Pipelines** | Kubeflow Pipelines (KFP) v2 | Pipeline orchestration |
| **ML Platform** | Vertex AI | Pipelines, Model Registry, Vector Search, Tuning |
| **Storage** | Google Cloud Storage | Documents, embeddings, manifests, prompts |
| **Database** | BigQuery | Interaction logs, feedback, evaluations, costs |
| **Auth** | Secret Manager + google-auth | API keys, JWT validation |
| **IaC** | Terraform | Infrastructure provisioning |
| **CI/CD** | GitHub Actions | Automated build, test, deploy |
| **Monitoring** | Cloud Monitoring + Cloud Logging | Alerts, structured logs |
| **Tracing** | OpenTelemetry + Cloud Trace | Distributed tracing |
| **Container** | Docker + Cloud Run | Serverless serving |
| **Dashboard** | Streamlit | Admin analytics UI |
