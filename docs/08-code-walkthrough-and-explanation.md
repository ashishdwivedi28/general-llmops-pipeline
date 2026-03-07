# LLMOps Pipeline — Code Walkthrough & Explanation

> **Document:** File-by-file code explanation for every module in the project  
> **Author:** Ashish Dwivedi  
> **Last Updated:** March 2026

---

## Table of Contents

1. [Project Structure Overview](#1-project-structure-overview)
2. [Core Framework (`src/llmops_pipeline/`)](#2-core-framework)
3. [IO Layer (`src/llmops_pipeline/io/`)](#3-io-layer)
4. [Pipeline Jobs (`src/llmops_pipeline/pipelines/`)](#4-pipeline-jobs)
5. [Serving Layer (`serving/`)](#5-serving-layer)
6. [KFP Pipeline Definitions (`kfp_pipelines/`)](#6-kfp-pipeline-definitions)
7. [Configuration Files (`confs/`)](#7-configuration-files)
8. [Infrastructure (`terraform/`)](#8-infrastructure)
9. [CI/CD (`.github/workflows/`)](#9-cicd)
10. [Tests (`tests/`)](#10-tests)
11. [Dashboard (`dashboard/`)](#11-dashboard)
12. [Project Root Files](#12-project-root-files)

---

## 1. Project Structure Overview

```
final-development-llmops/
├── src/llmops_pipeline/           # Core pipeline library (installable package)
│   ├── __init__.py                # Package metadata
│   ├── __main__.py                # python -m llmops_pipeline entry
│   ├── scripts.py                 # CLI entry point (llmops command)
│   ├── settings.py                # MainSettings Pydantic model
│   ├── io/                        # Input/Output layer
│   │   ├── configs.py             # OmegaConf YAML parsing
│   │   ├── manifest.py            # Pipeline Artifact Manifest
│   │   ├── model_router.py        # Multi-provider model routing
│   │   ├── prompt_registry.py     # Versioned prompt management
│   │   ├── services.py            # GCP service wrappers
│   │   └── vector_db.py           # Vector database operations
│   └── pipelines/                 # Pipeline job definitions
│       ├── __init__.py            # JobKind union type (15 jobs)
│       ├── base.py                # Job base class
│       ├── managers/              # Orchestrator jobs (4)
│       ├── feature_engineering/   # Phase 1 leaf jobs (2)
│       ├── deployment/            # Phase 2+3 leaf jobs (4)
│       ├── monitoring/            # Phase 4 leaf jobs (3)
│       └── fine_tuning/           # Phase 5 leaf jobs (3)
├── serving/                       # FastAPI serving layer
│   ├── server.py                  # Main FastAPI app + endpoints
│   ├── agent.py                   # ADK agent creation
│   ├── tools.py                   # Agent tools (RAG, search)
│   ├── callbacks.py               # Guardrails + logging
│   ├── gateway.py                 # Auth middleware
│   ├── canary.py                  # Canary deployment
│   ├── task_detection.py          # Query classification
│   ├── prompt.py                  # Prompt management bridge
│   └── utils/                     # Config, cost tracker, observability
├── kfp_pipelines/                 # Vertex AI Pipeline definitions
│   ├── compile_and_run.py         # Compile & submit entry point
│   ├── feature_engineering.py     # Phase 1 KFP pipeline
│   ├── deployment.py              # Phase 2+3 KFP pipeline
│   ├── monitoring.py              # Phase 4 KFP pipeline
│   ├── fine_tuning.py             # Phase 5 KFP pipeline
│   └── master.py                  # Master orchestrator pipeline
├── confs/                         # YAML configuration files
├── terraform/                     # Infrastructure as Code
│   ├── bootstrap/                 # One-time setup (WIF, AR, SAs)
│   └── main/                      # Ongoing infra (Cloud Run, BQ, etc.)
├── tests/                         # Test suite
├── dashboard/                     # Streamlit admin dashboard
└── data/                          # Documents & datasets
```

---

## 2. Core Framework

### `src/llmops_pipeline/__init__.py`
Package-level metadata. Defines `__version__`.

### `src/llmops_pipeline/__main__.py`
Enables `python -m llmops_pipeline` execution. Simply calls `scripts.main()`.

### `src/llmops_pipeline/scripts.py`
**The CLI entry point.** This is where `llmops confs/feature_engineering.yaml` starts.

**Flow:**
1. Parse CLI arguments (`files` + `extras` for inline overrides)
2. Load each YAML file via `configs.parse_file()` (OmegaConf)
3. Merge all configs with `configs.merge_configs()`
4. Convert to plain Python dict with `configs.to_object()`
5. Validate with `MainSettings.model_validate()` — this triggers Pydantic discriminated union dispatch
6. `with setting.job as runner: runner.run()` — context manager lifecycle

**Key detail:** The `KIND` field in YAML determines which Python class is instantiated. For example, `KIND: FeatureEngineeringJob` creates a `FeatureEngineeringJob` instance.

### `src/llmops_pipeline/settings.py`
Defines `MainSettings` — the top-level Pydantic model:

```python
class MainSettings(pdt.BaseModel):
    job: JobKind = pdt.Field(discriminator="KIND")
```

The `discriminator="KIND"` tells Pydantic to look at the `KIND` field to pick the right job class from the `JobKind` union.

---

## 3. IO Layer

### `src/llmops_pipeline/io/configs.py`
OmegaConf utilities for YAML parsing:
- `parse_file(path)` — Load YAML
- `parse_string(s)` — Parse inline config
- `merge_configs(list)` — Merge with later overriding earlier
- `to_object(config)` — Convert to Python dict (resolves `${oc.env:VAR}` references)

### `src/llmops_pipeline/io/manifest.py`
**The Pipeline Artifact Manifest — the bridge between offline and online systems.**

**Models:**
- `FeatureEngineeringManifest` — Vector index, embedding info, document counts
- `DeploymentManifest` — Active model, prompt version, eval scores, Cloud Run info
- `MonitoringManifest` — Quality scores, degradation status
- `PipelineManifest` — Top-level container for all sections

**Functions:**
- `write_manifest()` — Write full manifest to GCS (fallback: local `.manifests/` dir)
- `read_manifest()` — Read from GCS (returns empty default if not found)
- `update_section()` — Read-modify-write pattern for a single section (feature_engineering, deployment, monitoring)

**`ManifestWatcher` class** — Used by serving layer, polls GCS for manifest changes on a timer.

### `src/llmops_pipeline/io/model_router.py`
**Multi-provider model routing via LiteLLM.**

**Models:**
- `ModelConfig` — Single model config (provider, name, timeout, retries)
- `RoutingConfig` — Chat, embedding, evaluation model trees with fallback chains

**`ModelRouter` class:**
- `chat(messages)` — Send to primary model, fall through chain on failure
- `embed(texts)` — Embedding with the configured model
- `health_check()` — Test all configured models
- Automatic token counting and cost tracking per call

### `src/llmops_pipeline/io/prompt_registry.py`
**Versioned prompt management with GCS backend.**

**Models:**
- `PromptVersion` — A single prompt (system_prompt, query_rewriter, refusal, tool_instructions, variables)
- `PromptRegistryConfig` — App ID, bucket, active version, A/B traffic split

**Functions:**
- `save_prompt()` / `load_prompt()` — Read/write to GCS or local
- `list_prompt_versions()` — All available versions
- `resolve_variables()` — Substitute `${VAR}` in template text
- `select_prompt_version_ab()` — Weighted random selection for A/B testing

**`PromptRegistry` class** — Facade with caching, variable resolution, A/B selection.

### `src/llmops_pipeline/io/services.py`
GCP service wrappers for Vertex AI and Cloud Storage. Provides `VertexAIService` and `StorageService`.

### `src/llmops_pipeline/io/vector_db.py`
Vector database operations — create index, deploy to endpoint, similarity search. Supports Vertex AI Vector Search and local FAISS fallback.

---

## 4. Pipeline Jobs

### `src/llmops_pipeline/pipelines/base.py`
**The `Job` base class** — all 15 job types inherit from this.

**Key features:**
- `frozen=True` — immutable after creation
- `logger_service` / `vertex_ai_service` — injected services
- `__enter__` / `__exit__` — context manager lifecycle (init Vertex AI, log start/end)
- `run() -> Locals` — abstract method, returns a dict of results

### `src/llmops_pipeline/pipelines/__init__.py`
**Defines `JobKind` — the discriminated union of all 15 job types:**

```python
JobKind = Annotated[
    FeatureEngineeringJob | DeploymentJob | MonitoringJob | FineTuningJob
    | IngestDocumentsJob | CreateVectorDbJob | GenerateDatasetJob
    | PostDeployEvalJob | EvaluateAndDeployJob | RegisterModelJob
    | DiagnoseJob | RemediateJob | PrepareDatasetJob | TrainJob
    | EvaluateFineTunedJob,
    ...
]
```

### Manager Jobs (4 Orchestrators)

| Manager | Config | Chains |
|---------|--------|--------|
| `FeatureEngineeringJob` | `feature_engineering.yaml` | IngestDocuments → CreateVectorDb |
| `DeploymentJob` | `deployment.yaml` | GenerateDataset → PostDeployEval → EvaluateAndDeploy → RegisterModel |
| `MonitoringJob` | `monitoring.yaml` | PostDeployEval → Diagnose → Remediate |
| `FineTuningJob` | `fine_tuning.yaml` | PrepareDataset → Train → EvaluateFineTuned |

Each manager job creates its child jobs with the correct config, runs them in sequence, and passes results between them.

### Feature Engineering Leaf Jobs (Phase 1)

| Job | Purpose |
|-----|---------|
| `IngestDocumentsJob` | Loads documents from GCS/local, chunks them, creates embeddings |
| `CreateVectorDbJob` | Creates Vertex AI Vector Search index + endpoint, deploys index |

### Deployment Leaf Jobs (Phase 2+3)

| Job | Purpose |
|-----|---------|
| `GenerateDatasetJob` | Generates Q&A eval dataset from documents using LLM |
| `PostDeployEvalJob` | Evaluates using Gemini-as-judge (relevance, faithfulness, toxicity) |
| `EvaluateAndDeployJob` | Quality gate check + Cloud Run deployment |
| `RegisterModelJob` | Registers champion model in Vertex AI Model Registry |

### Monitoring Leaf Jobs (Phase 4)

| Job | Purpose |
|-----|---------|
| `PostDeployEvalJob` | (Reused) evaluates production interactions |
| `DiagnoseJob` | Root-cause analysis (data drift vs prompt vs infrastructure) |
| `RemediateJob` | Dispatches fixes (retrigger FE, rollback prompt, alert human) |

**DiagnoseJob details:**
- Compares each metric against its threshold
- Queries BigQuery for infrastructure metrics (latency p95, error rate)
- Returns a `DiagnosisReport` with primary cause and confidence scores

**RemediateJob details:**
- `retrigger_feature_engineering` — writes GCS signal file
- `rollback_prompt_version` — uses PromptRegistry to revert to previous version
- `review_prompt_version` — logs a warning alert
- `investigate_infrastructure` — logs a critical alert

### Fine-Tuning Leaf Jobs (Phase 5)

| Job | Purpose |
|-----|---------|
| `PrepareDatasetJob` | Queries BQ for high-quality interactions, formats as JSONL |
| `TrainJob` | Submits Vertex AI supervised fine-tuning (SFT) job |
| `EvaluateFineTunedJob` | Compares fine-tuned vs base model using Gemini-as-judge |

---

## 5. Serving Layer

### `serving/server.py`
**The main FastAPI application.** This is what Cloud Run hosts.

**Architecture:**
1. `create_app()` — Creates FastAPI app with middleware, endpoints
2. `lifespan()` — Async context manager, launches background init task
3. `_sync_initialize()` — Heavy GCP setup (agent, manifest, prompts) in a thread pool
4. `_state` dict — Global state holding agent, config, watchers, trackers

**Endpoints:**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Always 200 — Cloud Run startup probe |
| `/ready` | GET | 200 when agent initialized, 503 otherwise |
| `/chat` | POST | Main chat endpoint with guardrails |
| `/feedback` | POST | User rating submission |
| `/manifest` | GET | Current pipeline manifest |
| `/costs` | GET | Cost summary |
| `/agent/*` | * | ADK agent routes (mounted sub-app) |

**Initialization flow:**
1. Server starts → `/health` immediately returns 200
2. Background task runs `_sync_initialize()` in a thread
3. Init loads: config → observability → manifest → prompt registry → cost tracker → callbacks → ADK agent
4. Once complete, `_state["ready"] = True` → `/ready` returns 200
5. ManifestWatcher starts polling GCS for updates

### `serving/agent.py`
Creates the Google ADK agent with configured tools. The agent is initialized with the system prompt from the Prompt Registry and the tools from `tools.py`.

### `serving/tools.py`
Agent tools (functions the LLM can call):
- `rag_search` — Retrieve from Vertex AI Vector Search
- `rag_engine_search` — Retrieve from Vertex AI RAG Engine
- `get_pipeline_manifest` — Read current manifest status

### `serving/callbacks.py`
- `InteractionLogger` — Logs to Cloud Logging + BigQuery (interactions & feedback tables)
- `GuardrailChecker` — Input/output validation (topic filtering, basic injection detection)

### `serving/gateway.py`
API Gateway middleware:
- `GatewayMiddleware(BaseHTTPMiddleware)` — Intercepts all requests
- `InMemoryRateLimiter` — Sliding-window rate limiting
- JWT validation via `google.oauth2.id_token`
- API key validation with SHA-256 hashing
- RBAC enforcement based on `gateway.yaml` routes

### `serving/canary.py`
- `SmokeTest` — HTTP checks against Cloud Run (health, ready, chat, manifest)
- `CanaryManager` — Manages traffic splits on Cloud Run revisions (10% → 50% → 100%)

### `serving/task_detection.py`
- `TaskDetector` — Classifies queries using keyword matching and/or LLM
- Loads app config from YAML (`confs/app/hr_chatbot.yaml`)
- Routes to the appropriate tool based on detected task type

### `serving/prompt.py`
Bridge module between the serving layer and the Prompt Registry. Holds a module-level registry reference that `agent.py` uses to get the active system prompt.

### `serving/utils/config.py`
`ServerConfig` — Loads all environment variables using `pydantic-settings`:
- GCP project, location, bucket
- Agent name, model, embedding model
- Manifest settings, prompt registry settings
- Gateway config path, BQ dataset

### `serving/utils/cost_tracker.py`
- `CostRecord` — Single request cost (frozen Pydantic model)
- `CostSummary` — Aggregated totals by model and by app
- `CostTracker` — Records costs, writes to BigQuery, provides summaries
- `estimate_cost()` — Uses explicit pricing or falls back to LiteLLM

### `serving/utils/observability.py`
Sets up OpenTelemetry tracing with Cloud Trace exporter.

---

## 6. KFP Pipeline Definitions

These define Vertex AI Pipelines using the Kubeflow Pipelines (KFP) SDK.

### `kfp_pipelines/compile_and_run.py`
**CLI tool to compile and submit pipelines.**

- `compile_pipelines()` — Compiles all pipeline functions to YAML
- `submit_pipeline()` — Submits compiled YAML to Vertex AI Pipelines
- `PIPELINE_REGISTRY` — Maps names to pipeline functions

### `kfp_pipelines/feature_engineering.py`
KFP pipeline for Phase 1 with 2 components:
1. `ingest_and_chunk` — Document processing
2. `create_vector_db` — Index creation

### `kfp_pipelines/deployment.py`
KFP pipeline for Phase 2+3 with components for evaluation and deployment.

### `kfp_pipelines/monitoring.py`
KFP pipeline for Phase 4 with 3 components:
1. `evaluate_quality` — Gemini-as-judge evaluation
2. `diagnose_degradation` — Root-cause analysis
3. `remediate` — Automated fix dispatch

### `kfp_pipelines/fine_tuning.py`
KFP pipeline for Phase 5 with 3 components:
1. `prepare_finetuning_dataset` — BQ → JSONL
2. `submit_finetuning_job` — Vertex AI SFT
3. `evaluate_finetuned_model` — Quality gate

### `kfp_pipelines/master.py`
**The Master Pipeline** — orchestrates all phases sequentially:
1. Phase 1: Feature Engineering
2. Phase 2+3: Deploy
3. Phase 3: Post-deploy eval
4. Phase 4: Self-healing (diagnose → remediate → retrigger)

Each phase passes outputs to the next. Phase 4 includes conditional logic: if degraded → diagnose → remediate → retrigger FE.

---

## 7. Configuration Files

| File | Job Type | Key Settings |
|------|----------|-------------|
| `feature_engineering.yaml` | `FeatureEngineeringJob` | documents_path, embedding_model, embedding_dimensions |
| `deployment.yaml` | `DeploymentJob` | active_model, active_prompt_version, quality thresholds |
| `monitoring.yaml` | `MonitoringJob` | monitoring_window_days, metric_thresholds |
| `fine_tuning.yaml` | `FineTuningJob` | min_rating, min_samples, base_model, epochs, quality_gate |
| `models.yaml` | — | Chat/embedding/eval model routing with fallback chains |
| `evaluation.yaml` | — | Offline/online eval metrics, quality gate thresholds |
| `gateway.yaml` | — | Auth methods, rate limiting, RBAC routes |
| `rag_chain_config.yaml` | — | RAG retrieval parameters (top_k, distance threshold) |
| `generate_dataset.yaml` | `GenerateDatasetJob` | Q&A generation settings |
| `app/hr_chatbot.yaml` | — | Task detection, app-specific settings |

All pipeline configs follow the pattern:
```yaml
job:
  KIND: <JobClassName>
  project: ${oc.env:GCP_PROJECT_ID}
  location: ${oc.env:GCP_LOCATION,us-central1}
  gcs_bucket: ${oc.env:GCS_BUCKET}
  # ... job-specific fields
```

---

## 8. Infrastructure

### `terraform/bootstrap/main.tf`
One-time setup creating:
- 16 GCP APIs enabled
- Artifact Registry repository
- Terraform state GCS bucket
- Agent Service Account (with 7 roles)
- CI/CD Service Account (with 5 roles)
- Workload Identity Federation pool + provider (keyless GitHub auth)

### `terraform/main/main.tf`
Ongoing infrastructure:
- GCS bucket for pipeline artifacts
- Cloud Run v2 service (with health/liveness probes)
- IAM bindings (public access for dev, authenticated for staging/prod)

### `terraform/main/bigquery.tf`
BigQuery dataset `llmops` with 4 tables:
- `interactions` — Full Q&A log (session, query, response, latency, model, tokens)
- `feedback` — User ratings (session, rating, comment)
- `evaluations` — Evaluation scores (metrics, quality gate pass/fail)
- `costs` — Per-request costs (model, tokens, cost_usd)

### `terraform/main/secrets.tf`
Secret Manager secrets:
- `llmops-api-keys` — API keys for gateway auth
- `llmops-openai-key` — OpenAI API key
- `llmops-anthropic-key` — Anthropic API key

### `terraform/main/scheduler.tf`
Cloud Scheduler jobs:
- Daily monitoring at 2 AM UTC
- Weekly master pipeline on Sunday at 3 AM UTC

### `terraform/main/monitoring.tf`
Cloud Monitoring:
- Email notification channel
- Error rate alert (>5% 5xx responses over 5 minutes)
- Latency alert (p95 > 10 seconds)
- Log-based metric for quality degradation

### `terraform/main/api_gateway.tf`
(Optional) Cloud API Gateway with OpenAPI spec template.

---

## 9. CI/CD

### `.github/workflows/ci-cd.yml`
5-stage pipeline:

| Stage | Trigger | What It Does |
|-------|---------|-------------|
| **1. Lint & Test** | Every push, every PR | `ruff check`, `ruff format`, `pytest` |
| **2. Build & Push** | Push to `main` only | Docker build → Artifact Registry |
| **3. Deploy** | Push to `main` only | Cloud Run deployment with secrets |
| **4. Pipeline Submit** | workflow_dispatch only | Compiles + submits master pipeline |
| **5. Monitoring** | Daily cron schedule | Submits monitoring pipeline |

**Branch protection:** Feature branches → Lint & Test only. PRs → Lint & Test. Merge to main → Full pipeline.

---

## 10. Tests

| Test File | Scope |
|-----------|-------|
| `test_config.py` | Config loading, OmegaConf parsing |
| `test_manifest.py` | Manifest I/O, section updates, local fallback |
| `test_model_router.py` | ModelRouter instantiation, fallback logic |
| `test_prompt_registry.py` | Prompt save/load, versioning, A/B selection |
| `test_endpoints.py` | FastAPI endpoints (health, chat, feedback, manifest, costs) |
| `test_pipelines_mock.py` | Job registry, DiagnoseJob, RemediateJob, fine-tuning jobs |
| `test_evaluation.py` | Task detection, canary deployment |
| `test_serving.py` | Serving config, agent creation |
| `conftest.py` | Shared fixtures |

---

## 11. Dashboard

### `dashboard/app.py`
Streamlit admin dashboard with 6 pages:

1. **Overview** — System health, manifest version, active model, serving URL
2. **Pipeline Manifest** — Full JSON manifest viewer
3. **Cost Analytics** — Token usage over time, cost per model
4. **Monitoring Scores** — Quality metric trends (relevance, faithfulness)
5. **Model Configuration** — Active models, routing config, failover chain
6. **Feedback Analytics** — Rating distribution, feedback trends

---

## 12. Project Root Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Poetry project config, dependencies, tool settings |
| `poetry.toml` | Poetry local config (virtualenvs.in-project = true) |
| `Dockerfile` | Two-stage Docker build (builder + production) |
| `docker-compose.yml` | Local development container |
| `.env.example` | Template for environment variables |
| `.gitignore` | Git ignore patterns |
| `.dockerignore` | Docker build ignore patterns |
| `README.md` | Project readme |
| `DEPLOYMENT.md` | Deployment instructions |
