# LLMOps Pipeline — Project Overview

## What Is This Project?

This is a **general-purpose, config-driven LLMOps pipeline** built on Google Cloud Platform (GCP). It automates the entire lifecycle of a Retrieval-Augmented Generation (RAG) chatbot — from document ingestion and vector search setup, through model evaluation and deployment, to continuous production monitoring.

The key goal: **change a YAML config file, run the pipeline, and get a production-ready RAG agent** — no code changes needed for different use cases (HR chatbot, IT support, knowledge base, etc.).

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     MASTER PIPELINE (Pipeline 0)                    │
│                     Vertex AI Pipelines (KFP)                       │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Pipeline 1:       │  │ Pipeline 2:       │  │ Pipeline 3:       │  │
│  │ Feature Eng.      │→│ Deployment        │→│ Monitoring        │  │
│  │                   │  │                   │  │                   │  │
│  │ • Create Vector   │  │ • Register Model  │  │ • Pull Prod Logs  │  │
│  │   Search Index    │  │ • Evaluate (LLM   │  │ • Evaluate Quality│  │
│  │ • Ingest Docs     │  │   as Judge)       │  │ • Alert if        │  │
│  │ • Chunk + Embed   │  │ • Quality Gate    │  │   Degraded        │  │
│  │ • Upload Vectors  │  │ • Auto-Promote    │  │ • Re-trigger P1   │  │
│  └──────────────────┘  └──────────────────┘  └──────┬───────────┘  │
│                                                      │ if degraded  │
│                                              ┌───────▼────────┐     │
│                                              │ Re-run Pipeline │     │
│                                              │ 1 (auto-heal)  │     │
│                                              └────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     SERVING LAYER                                    │
│                     Cloud Run + Google ADK                           │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ FastAPI   │  │ ADK Agent│  │ RAG Tool │  │ Guardrails +     │   │
│  │ Server    │→│ (Gemini) │→│ (Vector  │  │ Logging          │   │
│  │           │  │          │  │  Search) │  │ (Cloud Logging   │   │
│  │ /chat     │  │ LlmAgent │  │          │  │  + BigQuery)     │   │
│  │ /health   │  │          │  │          │  │                  │   │
│  │ /feedback │  │          │  │          │  │                  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
final-development-llmops/
│
├── confs/                          # YAML configs (THE config layer)
│   ├── feature_engineering.yaml    # Vector DB + ingestion settings
│   ├── deployment.yaml             # Model registration + eval thresholds
│   ├── monitoring.yaml             # Monitoring window + alert settings
│   ├── rag_chain_config.yaml       # RAG model, embedding, guardrails, prompts
│   └── generate_dataset.yaml       # QA dataset generation settings
│
├── src/llmops_pipeline/            # Core Python package
│   ├── __init__.py                 # Version
│   ├── __main__.py                 # python -m llmops_pipeline
│   ├── scripts.py                  # CLI entry point (YAML → Job dispatch)
│   ├── settings.py                 # Pydantic MainSettings (discriminated union)
│   ├── io/                         # IO layer
│   │   ├── configs.py              # OmegaConf YAML parsing + merging
│   │   ├── services.py             # LoggerService, VertexAIService, GCSService
│   │   └── vector_db.py            # VertexVectorSearch (Matching Engine)
│   └── pipelines/                  # Pipeline job definitions
│       ├── __init__.py             # JobKind union type (discriminator registry)
│       ├── base.py                 # Job ABC (Pydantic + context manager)
│       ├── feature_engineering/    # CreateVectorDB, IngestDocuments
│       ├── deployment/             # RegisterModel, EvaluateAndDeploy
│       ├── monitoring/             # GenerateDataset, PostDeployEval
│       └── managers/               # Orchestrator jobs (chain sub-jobs)
│
├── kfp_pipelines/                  # Vertex AI Pipeline definitions (KFP)
│   ├── feature_engineering.py      # Pipeline 1
│   ├── deployment.py               # Pipeline 2
│   ├── monitoring.py               # Pipeline 3
│   ├── master.py                   # Pipeline 0 (master orchestrator)
│   └── compile_and_run.py          # Compile + submit to Vertex AI
│
├── serving/                        # Agent serving layer (ADK)
│   ├── agent.py                    # LlmAgent definition
│   ├── server.py                   # FastAPI server (health, chat, feedback)
│   ├── tools.py                    # RAG retrieval tool
│   ├── callbacks.py                # Logging + guardrails
│   ├── prompt.py                   # System prompt + instruction provider
│   ├── client.py                   # Test client
│   └── utils/                      # Config + observability
│
├── terraform/                      # Infrastructure as Code
│   └── main.tf                     # Cloud Run, GCS, IAM, WIF, Artifact Registry
│
├── .github/workflows/              # CI/CD
│   └── ci-cd.yml                   # Lint → Build → Deploy (dev→staging→prod)
│
├── tests/                          # Test suite
├── data/                           # Documents + datasets
├── Dockerfile                      # Multi-stage production build
├── docker-compose.yml              # Local development
├── pyproject.toml                  # Dependencies (Poetry)
└── .env.example                    # Environment variable template
```

---

## How It Works

### 1. Config-Driven Design

Everything is controlled through YAML files in `confs/`. The system uses:
- **OmegaConf** for YAML parsing and merging (override specific fields)
- **Pydantic discriminated unions** to auto-dispatch configs to the correct Job class via the `KIND` field

```yaml
# confs/feature_engineering.yaml
job:
  KIND: FeatureEngineeringJob      # ← This selects which Job class runs
  project: my-gcp-project
  embedding_model: text-embedding-004
  chunk_size: 1000
```

Running `llmops confs/feature_engineering.yaml` automatically:
1. Parses the YAML with OmegaConf
2. Validates with Pydantic `MainSettings`
3. Dispatches to `FeatureEngineeringJob` based on `KIND`
4. Starts services → runs job → stops services

### 2. Three Automated Pipelines

| Pipeline | Purpose | Jobs |
|----------|---------|------|
| **Feature Engineering** | Build knowledge base | CreateVectorDB → IngestDocuments |
| **Deployment** | Register + evaluate + deploy | RegisterModel → EvaluateAndDeploy (Gemini-as-judge) |
| **Monitoring** | Detect quality degradation | PostDeployEval (pull Cloud Logging → evaluate) |

### 3. Master Pipeline (Pipeline 0)

The master pipeline chains all three with conditional logic:
- Runs Feature Engineering → Deployment → Monitoring **sequentially**
- If monitoring detects degradation → **automatically re-triggers** Feature Engineering
- Uses `dsl.Condition` in KFP for branching

### 4. Serving Layer

The ADK agent serves the RAG chatbot on Cloud Run:
- **Google ADK** (`LlmAgent`) with Gemini 2.0 Flash
- **RAG retrieval** via Vertex AI Vector Search
- **Guardrails**: input topic filtering + output PII detection
- **Observability**: Cloud Logging, Cloud Trace (OpenTelemetry), BigQuery logging
- **Feedback endpoint**: `/feedback` for user ratings

---

## Quick Start

```bash
# 1. Clone and install
git clone <repo-url>
cd final-development-llmops
cp .env.example .env  # Fill in your GCP values
pip install poetry
poetry install

# 2. Run individual pipeline (local)
poetry run llmops confs/feature_engineering.yaml

# 3. Compile and submit to Vertex AI
python -m kfp_pipelines.compile_and_run --project $GCP_PROJECT_ID --bucket $GCS_BUCKET

# 4. Run agent locally
python -m serving.server

# 5. Deploy infrastructure
cd terraform
cp terraform.tfvars.example terraform.tfvars  # Fill in values
terraform init && terraform apply
```

---

## Design Principles

1. **Use-Case Agnostic**: Change YAML configs to adapt for any RAG chatbot (HR, IT, legal, etc.)
2. **Fully Automated**: Master pipeline runs everything end-to-end with self-healing
3. **GCP-Native**: All services are Google Cloud (no AWS, no third-party)
4. **Production-Ready**: Multi-environment CI/CD, health checks, monitoring, guardrails
5. **Config-Driven**: No code changes needed — only YAML
6. **Observable**: Every step logged, traced, and trackable
