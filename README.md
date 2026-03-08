# LLMOps Pipeline

Production-ready, config-driven LLM application platform on Google Cloud Platform. Change the YAML config → the entire pipeline adapts to any LLM use case (RAG, Agent, Copilot, Chatbot, HR Bot, etc.)

---

## What This Does

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Serving API** | FastAPI + Cloud Run | Chat, feedback, health endpoints |
| **LLM Agent** | Vertex AI Gemini | Multi-tool AI agent with RAG |
| **Pipelines** | Kubeflow Pipelines + Vertex AI | Feature engineering, fine-tuning, evaluation, monitoring |
| **Storage** | GCS + BigQuery | Pipeline artifacts + interaction logs |
| **CI/CD** | GitHub Actions + Terraform | Push to main → auto-deploy |
| **Secrets** | Secret Manager | API keys injected securely at runtime |
| **Monitoring** | Cloud Monitoring | Error rate and latency alerts |

---

## Quick Start (Local)

```powershell
# Install
pip install "poetry==1.8.4"
poetry install

# Configure local .env
Copy-Item .env.example .env
# Then fill in: GCP_PROJECT_ID, GCS_BUCKET, etc.

# Run the API server locally
poetry run python -m serving.server
# Test: curl http://localhost:8080/health

# Run a pipeline job
poetry run llmops confs/feature_engineering.yaml

# Run tests
pytest tests/ -v
```

---

## Deploy to GCP

See **[DEPLOYMENT.md](DEPLOYMENT.md)** — complete guide to get live on GCP.

**Quick steps:**
1. Authenticate: `gcloud auth login` + `gcloud config set project`
2. Clone and install: `git clone ... && poetry install`
3. Bootstrap (one-time): `cd terraform/bootstrap && terraform apply`
4. Configure GitHub: Set secrets/variables from bootstrap output via `gh`
5. Deploy infrastructure: `cd terraform/main && terraform apply`
6. Add secrets: `gcloud secrets versions add llmops-api-keys`
7. Deploy app: `git push origin main` → CI/CD auto-deploys

---

## Documentation

| Doc | What It Covers |
|-----|---------------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Full deployment guide — Step 1 to live |
| [docs/06-developer-setup-and-run-guide.md](docs/06-developer-setup-and-run-guide.md) | Local dev setup, running tests, adding tools and pipelines |
| [docs/07-architecture-and-component-explanation.md](docs/07-architecture-and-component-explanation.md) | Architecture deep-dive |
| [docs/08-code-walkthrough-and-explanation.md](docs/08-code-walkthrough-and-explanation.md) | How each file works |
| [docs/09-gcp-resources-and-pipeline-flow.md](docs/09-gcp-resources-and-pipeline-flow.md) | GCP services and how they connect |
| [docs/10-github-setup-guide.md](docs/10-github-setup-guide.md) | GitHub repo secrets, branch protection, CI/CD setup |
| [docs/terraform_infrastructure.md](docs/terraform_infrastructure.md) | Terraform reference: all resources, IAM, security |

---

## Project Structure

```
final-development-llmops/
├── serving/            # FastAPI API server (agent, tools, gateway, prompts)
├── src/llmops_pipeline/# Core pipeline logic
├── kfp_pipelines/      # Kubeflow pipeline definitions
├── confs/              # YAML configs (change config = change pipeline)
├── terraform/
│   ├── bootstrap/      # One-time: WIF, SAs, Artifact Registry
│   └── main/           # Cloud Run, GCS, BigQuery, Secrets, Monitoring
├── tests/              # Unit tests
├── lab_test/           # GCP integration tests
├── dashboard/app.py    # Streamlit monitoring dashboard
├── DEPLOYMENT.md       # Deployment guide
└── docs/               # All documentation
```

---

## Daily Workflow

```powershell
# 1. Create feature branch
git checkout -b feature/my-change

# 2. Make changes and test
pytest tests/ -v

# 3. Push → triggers lint & test (no deploy on feature branches)
git push origin feature/my-change

# 4. Open PR on GitHub → merge to main → auto-deploys to Cloud Run
```
