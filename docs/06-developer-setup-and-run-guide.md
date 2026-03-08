# Developer Setup & Run Guide

> Everything you need to develop, test, and run the LLMOps pipeline locally.
> For production deployment, see [DEPLOYMENT.md](../DEPLOYMENT.md).

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Local Python Setup](#2-local-python-setup)
3. [Environment Configuration](#3-environment-configuration)
4. [Running Locally](#4-running-locally)
5. [Running Tests](#5-running-tests)
6. [Running with Docker](#6-running-with-docker)
7. [Running KFP Pipelines Locally](#7-running-kfp-pipelines-locally)
8. [Running the Dashboard](#8-running-the-dashboard)
9. [Lab Tests (GCP Integration)](#9-lab-tests-gcp-integration)
10. [Project Structure](#10-project-structure)

---

## 1. Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11 | https://www.python.org/downloads/ |
| Poetry | 1.8.4 | `pip install "poetry==1.8.4"` |
| Docker | Latest | https://docs.docker.com/get-docker/ |
| `gcloud` | Latest | https://cloud.google.com/sdk/docs/install |
| Git | Latest | https://git-scm.com/ |

---

## 2. Local Python Setup

```powershell
# Clone the repo
git clone https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME/final-development-llmops

# Install dependencies
pip install "poetry==1.8.4"
poetry install

# Activate the virtual environment (PowerShell)
.\.venv\Scripts\Activate.ps1

# Verify the installation
python -c "from llmops_pipeline.pipelines import JobKind; print('OK')"
ruff check src/ serving/ kfp_pipelines/
```

---

## 3. Environment Configuration

```powershell
Copy-Item .env.example .env
```

Open `.env` and fill in at minimum:

```dotenv
# Required — fill in your own values
GCP_PROJECT_ID=YOUR_GCP_PROJECT_ID
GCP_LOCATION=us-central1
GCS_BUCKET=YOUR_GCP_PROJECT_ID-llmops-dev

# Agent
AGENT_NAME=llmops-rag-agent
MODEL_NAME=gemini-2.0-flash
EMBEDDING_MODEL=text-embedding-004

# Manifest (enables offline→online bridge)
MANIFEST_ENABLED=true
MANIFEST_APP_ID=llmops-app
MANIFEST_BUCKET=<YOUR_GCP_PROJECT_ID>-llmops-dev
MANIFEST_REFRESH_INTERVAL=120

# Security (set a key for local testing)
LLMOPS_API_KEYS=local-dev-key
```

---

## 4. Running Locally

### Run the serving API server

```powershell
# With Poetry (recommended)
poetry run python -m serving.server

# Or directly after activating venv
python -m serving.server
```

The server starts on http://localhost:8080.

```powershell
# Health check
curl http://localhost:8080/health

# Send a chat message
curl -X POST http://localhost:8080/chat `
  -H "Content-Type: application/json" `
  -H "X-API-Key: local-dev-key" `
  -d '{"message": "What is the leave policy?", "session_id": "s1"}'
```

### Run a pipeline job via CLI

```powershell
# Feature engineering
poetry run llmops confs/feature_engineering.yaml

# Generate dataset
poetry run llmops confs/generate_dataset.yaml

# Run evaluation
poetry run llmops confs/evaluation.yaml
```

### Use a specific config

```powershell
poetry run llmops confs/app/hr_chatbot.yaml
```

---

## 5. Running Tests

```powershell
# Run all tests
pytest tests/ -v

# Run with short traceback
pytest tests/ -v --tb=short

# Run a specific test file
pytest tests/test_serving.py -v

# Run with coverage
pytest tests/ --cov=src --cov=serving --cov-report=term-missing
```

Tests use mocks for GCP services — no real GCP credentials needed for unit tests.

---

## 6. Running with Docker

```powershell
# Build the image
docker build -t llmops-agent:local .

# Run with your .env file
docker run --env-file .env -p 8080:8080 llmops-agent:local

# Or use docker-compose (starts agent + dependencies)
docker compose up
```

Health check: http://localhost:8080/health

---

## 7. Running KFP Pipelines Locally

Compile a pipeline to a YAML file:

```powershell
# Compile monitoring pipeline
poetry run python -m kfp_pipelines.compile_and_run `
  --pipeline monitoring `
  --project YOUR_GCP_PROJECT_ID `
  --bucket YOUR_GCP_PROJECT_ID-llmops-dev `
  --location YOUR_GCP_REGION `
  --service-account llmops-agent-dev@YOUR_GCP_PROJECT_ID.iam.gserviceaccount.com
```

Available pipelines: `monitoring`, `feature_engineering`, `fine_tuning`, `deployment`, `master`

Upload the YAML to GCS manually (or it's done automatically by the script):
```powershell
gcloud storage cp pipelines/monitoring_pipeline.yaml `
  gs://YOUR_GCP_PROJECT_ID-llmops-dev/pipelines/
```

---

## 8. Running the Dashboard

```powershell
poetry run streamlit run dashboard/app.py
```

Open: http://localhost:8501

The dashboard connects to BigQuery and shows:
- Interaction logs
- Model cost tracking
- Evaluation scores
- Quality degradation trends

Requires `GCP_PROJECT_ID` in your `.env` and valid gcloud credentials.

---

## 9. Lab Tests (GCP Integration)

Lab tests in `lab_test/` test real GCP services. They require valid credentials
and a deployed GCP environment.

```powershell
# Set up lab environment
Copy-Item lab_test/.env.lab.example lab_test/.env.lab
# Fill in real GCP values in lab_test/.env.lab

# Install lab requirements
pip install -r lab_test/requirements_lab.txt

# Run all lab tests
poetry run python lab_test/run_lab_test.py

# Run individual tests
poetry run python lab_test/01_test_gemini.py     # Test Vertex AI / Gemini
poetry run python lab_test/02_test_gcs.py         # Test GCS access
poetry run python lab_test/03_test_vector_db.py   # Test Vector DB
poetry run python lab_test/04_test_rag_pipeline.py # Test full RAG
poetry run python lab_test/05_test_evaluation.py  # Test evaluation
poetry run python lab_test/06_test_serving.py     # Test serving API
```

---

## 10. Project Structure

```
final-development-llmops/
├── src/llmops_pipeline/    # Core pipeline logic, config, model routing
├── serving/                # FastAPI serving layer (agent, gateway, tools)
│   ├── server.py           # Entry point — runs the FastAPI app
│   ├── agent.py            # LLM agent with tool calling
│   ├── gateway.py          # Auth middleware (API key validation)
│   ├── tools.py            # Agent tools (HR lookup, policy search, etc.)
│   ├── prompt.py           # Prompt Registry (loads versioned prompts from GCS)
│   ├── canary.py           # Canary deployment logic
│   └── utils/              # Observability, cost tracking, config
├── kfp_pipelines/          # Kubeflow Pipelines (compile → upload → Vertex AI)
│   ├── master.py           # Orchestrates all pipelines
│   ├── monitoring.py       # Reads BigQuery → detects degradation
│   ├── feature_engineering.py
│   ├── fine_tuning.py
│   └── deployment.py
├── confs/                  # YAML configs (change config → change pipeline)
│   ├── models.yaml         # Model routing rules (Gemini/OpenAI/Anthropic)
│   ├── rag_chain_config.yaml
│   ├── monitoring.yaml
│   └── app/hr_chatbot.yaml # App-specific config
├── terraform/
│   ├── bootstrap/          # One-time GCP setup (WIF, SAs, Artifact Registry)
│   └── main/               # Main infrastructure (Cloud Run, BQ, GCS, etc.)
├── tests/                  # Unit tests (mocked GCP)
├── lab_test/               # Integration tests (real GCP)
├── dashboard/app.py        # Streamlit admin dashboard
├── DEPLOYMENT.md           # Step-by-step deployment guide
└── docs/                   # Full documentation
```

### Key Config Files

| File | Purpose |
|------|---------|
| `confs/models.yaml` | Which LLM to use, routing rules, fallback order |
| `confs/rag_chain_config.yaml` | RAG chunk size, embedding, retrieval config |
| `confs/monitoring.yaml` | Alert thresholds, degradation detection rules |
| `confs/app/hr_chatbot.yaml` | HR chatbot app-specific overrides |
| `terraform/main/terraform.tfvars` | GCP environment variables for Terraform |
| `.env` | Local development environment variables |

---

## Code Quality

```powershell
# Lint
ruff check src/ serving/ kfp_pipelines/

# Format check
ruff format --check src/ serving/ kfp_pipelines/

# Auto-fix formatting
ruff format src/ serving/ kfp_pipelines/
```

Ruff is configured in `pyproject.toml`. The CI/CD pipeline runs lint before every deploy.

---

## Adding a New Tool to the Agent

1. Add the tool function in `serving/tools.py`
2. Register it in `serving/agent.py` tool list
3. Update `confs/app/hr_chatbot.yaml` if the tool needs config
4. Write a test in `tests/test_serving.py`
5. Push to a feature branch → open PR → merge to main → auto-deploys

---

## Adding a New Pipeline

1. Create `kfp_pipelines/my_pipeline.py` following the pattern in `monitoring.py`
2. Register it in `kfp_pipelines/compile_and_run.py`
3. Add a config in `confs/my_pipeline.yaml`
4. Test: `poetry run python -m kfp_pipelines.compile_and_run --pipeline my_pipeline ...`
