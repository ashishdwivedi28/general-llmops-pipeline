# LLMOps Pipeline — Complete Project Understanding Guide

> **Who this document is for:** Anyone who is new to this project, has never deployed to GCP, or needs to explain this system to someone else. No prior knowledge assumed.

---

## Table of Contents

1. [What Problem Are We Solving?](#1-what-problem-are-we-solving)
2. [The Big Picture — What Is This Project?](#2-the-big-picture--what-is-this-project)
3. [How Everything Connects — The Journey of One Request](#3-how-everything-connects--the-journey-of-one-request)
4. [The 7-Layer Architecture Explained](#4-the-7-layer-architecture-explained)
5. [Component Deep-Dives](#5-component-deep-dives)
6. [The Three Pipelines Explained](#6-the-three-pipelines-explained)
7. [GCP Services — What Each One Does](#7-gcp-services--what-each-one-does)
8. [API Gateway — How It Works](#8-api-gateway--how-it-works)
9. [Vertex AI Pipelines (KFP) — How It Works](#9-vertex-ai-pipelines-kfp--how-it-works)
10. [Terraform — Infrastructure as Code](#10-terraform--infrastructure-as-code)
11. [CI/CD — How Code Goes to Production](#11-cicd--how-code-goes-to-production)
12. [Config-Driven Design — The Core Idea](#12-config-driven-design--the-core-idea)
13. [Project Structure Walkthrough](#13-project-structure-walkthrough)
14. [Real-World Example: HR Chatbot](#14-real-world-example-hr-chatbot)
15. [Moving from Lab to Cloud — What Changes](#15-moving-from-lab-to-cloud--what-changes)

---

## 1. What Problem Are We Solving?

### The Problem with LLM Applications

Imagine you build an HR chatbot that answers employee questions. It works great on day 1. But:
- HR updates the leave policy document. The chatbot still gives the **old, wrong answer**.
- Nobody notices until 50 employees get incorrect information.
- You have no way to know the chatbot quality is degrading.
- Redeploying requires manual steps — someone has to remember to update the knowledge base.

This is not just an HR chatbot problem. **Every LLM application has this problem.**

### What LLMOps Solves

| Problem | What This Pipeline Does |
|---|---|
| Knowledge goes stale | Auto-reingest documents when they change |
| No way to know if quality dropped | Automated evaluation scores every response |
| Someone has to manually deploy | CI/CD + Vertex AI Pipelines handle everything |
| Can't tell if model is hallucinating | Gemini-as-judge evaluates every batch of answers |
| Security risks (prompt injection, PII) | Guardrails run on every request |
| Can't reproduce previous results | Everything config-driven + tracked in Vertex AI Experiments |

---

## 2. The Big Picture — What Is This Project?

Think of this project as **two systems working together**:

```
┌─────────────────────────────────────────┐
│  OFFLINE SYSTEM (Vertex AI Pipelines)   │
│                                         │
│  "The Factory"                          │
│  Prepares everything before users hit  │
│  the system:                            │
│  • Reads documents                      │
│  • Builds knowledge base                │
│  • Tests answer quality                 │
│  • Only deploys if quality PASSES       │
│  • Monitors and re-runs itself          │
│                                         │
└──────────────────┬──────────────────────┘
                   │ delivers
                   ▼
┌─────────────────────────────────────────┐
│  ONLINE SYSTEM (Cloud Run + ADK)        │
│                                         │
│  "The Shop Front"                       │
│  Handles real user requests 24/7:       │
│  • Receives user question               │
│  • Searches knowledge base              │
│  • Calls Gemini to generate answer      │
│  • Applies safety guardrails            │
│  • Returns answer to user               │
│                                         │
└─────────────────────────────────────────┘
```

**The key innovation**: The offline system (pipelines) continuously feeds and validates the online system (serving layer). If quality drops, the pipeline fixes it automatically — **without human intervention**.

---

## 3. How Everything Connects — The Journey of One Request

Here is what happens from the moment a user types a question:

```
User types: "What is my annual leave entitlement?"
     │
     ▼
[1] API GATEWAY (Cloud API Gateway)
     Checks: Is this user authenticated?
     Checks: Are they making too many requests?
     Checks: Is this a malicious request?
     → Passes to Cloud Run
     │
     ▼
[2] FASTAPI SERVER (serving/server.py on Cloud Run)
     Receives the HTTP request
     Runs guardrail: Is this question on-topic?
     → Passes to ADK Agent
     │
     ▼
[3] ADK AGENT (serving/agent.py)
     Gemini model reads the question
     Decides: "I need to search the knowledge base"
     → Calls the RAG tool
     │
     ▼
[4] RAG TOOL (serving/tools.py)
     Converts "annual leave entitlement" into a vector (768 numbers)
     Searches Vertex AI Vector Search for similar document chunks
     Returns: top 3 most relevant paragraphs from HR policy documents
     │
     ▼
[5] GEMINI GENERATES ANSWER
     Given: User question + retrieved context paragraphs
     Generates: A grounded, factual answer
     │
     ▼
[6] POST-PROCESSING GUARDRAILS (serving/callbacks.py)
     Checks: Does the answer contain PII (names, emails, SSNs)?
     Checks: Is the answer safe?
     → Removes/flags any violations
     │
     ▼
[7] RESPONSE LOGGED (Cloud Logging + Cloud Trace)
     Every step is recorded as a trace
     This trace is later used by the Monitoring Pipeline
     │
     ▼
User receives: "Employees receive 20 days of annual leave per year.
                Leave is calculated pro-rata for part-time staff..."
```

---

## 4. The 7-Layer Architecture Explained

Our system follows a 7-layer architecture. Think of it like a building — each floor does something specific:

```
┌─────────────────────────────────────────────────────────────┐
│ LAYER 7: MONITORING                                          │
│ Cloud Logging + Cloud Monitoring + Vertex AI Experiments     │
│ Watches everything, fires alerts when quality drops          │
├─────────────────────────────────────────────────────────────┤
│ LAYER 6: POST-PROCESSING                                     │
│ Guardrails + PII Detection (Cloud DLP) + Safety Filters      │
│ Cleans and validates every response before it leaves         │
├─────────────────────────────────────────────────────────────┤
│ LAYER 5: MODEL ABSTRACTION                                   │
│ Gemini 2.0 Flash via Vertex AI                              │
│ The actual LLM — generates text given context + question     │
├───────────────┬─────────────────────┬───────────────────────┤
│ LAYER 4a      │ LAYER 4b            │ LAYER 4c              │
│ PROMPT        │ RAG ENGINE          │ FINE-TUNE (optional)  │
│ SERVICE       │                     │                       │
│ YAML prompts  │ Vector Search +     │ Custom model training │
│ versioned     │ Gemini retrieval    │ (not used currently)  │
├───────────────┴─────────────────────┴───────────────────────┤
│ LAYER 3: REQUEST CONTROLLER                                  │
│ Cloud Run + ADK Agent (serving/)                            │
│ Receives requests, decides which tools to use, coordinates   │
├─────────────────────────────────────────────────────────────┤
│ LAYER 2: API GATEWAY                                         │
│ Cloud API Gateway + Cloud Armor                              │
│ Auth, rate limiting, WAF — the security checkpoint           │
├─────────────────────────────────────────────────────────────┤
│ LAYER 1: CLIENTS                                             │
│ Web app / Mobile app / API consumer / Browser Extension      │
│ Where the end user interacts                                 │
└─────────────────────────────────────────────────────────────┘
```

**Our code covers Layers 3–7.** Layer 1 (client app) and Layer 2 (API Gateway) are separate — the setup guide covers adding API Gateway in the production hardening step.

---

## 5. Component Deep-Dives

### Component 1: Config Engine (`src/llmops_pipeline/settings.py` + `confs/`)

**What it is:** Instead of hardcoding values in Python, everything is in YAML files.

**Why it matters:** Change the use case (HR chatbot → IT support chatbot) by changing one line in a YAML file, not by rewriting code.

**How it works:**
```yaml
# confs/feature_engineering.yaml
job:
  KIND: FeatureEngineeringJob      # ← Python reads this and runs the right class
  project: my-gcp-project
  embedding_model: text-embedding-004
  chunk_size: 1000
```

When you run `python -m llmops_pipeline confs/feature_engineering.yaml`:
1. OmegaConf reads the YAML
2. Pydantic `MainSettings` validates every field (fails fast if something is wrong)
3. The `KIND` field tells Python exactly which Job class to run
4. No guesswork — if `KIND: FeatureEngineeringJob`, it runs `FeatureEngineeringJob`

**Files:** `settings.py`, `io/configs.py`, all files under `confs/`

---

### Component 2: Services Layer (`src/llmops_pipeline/io/services.py`)

**What it is:** Clean wrappers around GCP services so the rest of the code doesn't need to know the GCP API details.

**Services wrapped:**
- `LoggerService` — structured JSON logging that goes to both console and Cloud Logging
- `VertexAIService` — tracks experiment runs, logs metrics (accuracy, latency) to Vertex AI Experiments
- `GCSService` — upload/download files to Google Cloud Storage

**Why it matters:** If Google changes its SDK, you fix one file (`services.py`). All pipeline code stays the same.

---

### Component 3: Document Ingestion (`src/llmops_pipeline/pipelines/feature_engineering/ingest_documents.py`)

**What it is:** Reads your source documents, breaks them into pieces, converts to numbers (embeddings), stores them.

**Step by step:**
```
Your PDF/TXT documents in GCS
        ↓
DirectoryLoader reads all files
        ↓
RecursiveCharacterTextSplitter cuts into chunks
(1000 characters each, 200 character overlap so context isn't lost)
        ↓
text-embedding-004 converts each chunk to 768 numbers (a vector)
"Annual leave policy" → [0.23, -0.41, 0.87, ...]  (768 numbers)
        ↓
Vectors uploaded to Vertex AI Vector Search index
```

**Why 1000 characters?** Too small = not enough context. Too large = retrieval becomes imprecise. 1000 is a tested sweet spot.

**Why overlap?** If a sentence falls at the boundary of chunk 1 and chunk 2, overlap ensures neither chunk loses half that sentence.

---

### Component 4: Vector Database (`src/llmops_pipeline/io/vector_db.py`)

**What it is:** A database that searches by meaning, not by exact words.

**Simple example:**
- Traditional database: search "leave" → only finds documents containing the word "leave"
- Vector database: search "time off" → finds documents about "vacation", "holiday", "annual leave" because they have similar *meaning*

**In production:** Vertex AI Vector Search (Matching Engine)
**In lab:** FAISS (local, free, same concept)

**How retrieval works:**
```
User query: "parental leave"
        ↓
Convert to vector: [0.15, 0.72, -0.33, ...]
        ↓
Vertex AI Vector Search finds 3 nearest vectors
(nearest = most similar meaning)
        ↓
Returns the text of those 3 document chunks
        ↓
Those chunks become the context for Gemini
```

---

### Component 5: RAG Chain (`serving/tools.py`)

**RAG = Retrieval-Augmented Generation.**

Without RAG:
- User: "What's the leave policy?"
- Gemini: Makes something up (hallucination) — dangerous

With RAG:
- User: "What's the leave policy?"
- System retrieves: 3 paragraphs from `hr_policy_2024.pdf`
- Gemini: "Based on the HR policy document: employees receive 20 days..."
- Answer is grounded in real documents — no hallucination

**The prompt template:**
```
Context from documents:
{retrieved_paragraph_1}
{retrieved_paragraph_2}
{retrieved_paragraph_3}

User question: {question}

Answer ONLY based on the context above. If unsure, say so.
```

---

### Component 6: Guardrails (`serving/callbacks.py`)

**What it is:** Safety checks that run on every request — like a bouncer at a club.

**Input guardrails (before Gemini):**
- Is this question on-topic? (HR chatbot shouldn't answer questions about competitor pricing)
- Does the question contain obvious injection attempts?
- Is it within allowed topics from the config?

**Output guardrails (after Gemini):**
- Does the answer contain PII (names, phone numbers, SSNs)? → Remove/flag
- Is the answer toxic or harmful? → Block
- Does the answer stay on topic?

**Config-driven:**
```yaml
# confs/rag_chain_config.yaml
guardrails:
  valid_topics: ["leave", "HR policy", "benefits", "salary"]
  invalid_topics: ["competitor", "politics", "personal advice"]
```

---

### Component 7: Agent Layer (`serving/agent.py`)

**What it is:** Google ADK (Agent Development Kit) gives our chatbot multi-step reasoning.

**Simple chatbot (no agent):** User asks → LLM answers. One step.

**Agent (our system):** User asks → Agent thinks "which tool do I need?" → calls RAG tool → gets context → Gemini generates answer → agent formats and returns. Multiple steps, agent decides the path.

**Why this matters:** Tomorrow you can add a Calendar tool (book leave), a Forms tool (submit HR requests), or a Database tool (check leave balance) — just add the tool to `tools.py`. The agent decides which to call based on the question.

---

### Component 8: Model Registry (`src/llmops_pipeline/pipelines/deployment/register_model.py`)

**What it is:** Before deploying, we register the model config in Vertex AI Model Registry with lifecycle labels.

**Labels:**
- `stage=champion` — the new candidate, not yet in production
- `stage=production` — the current live version

**Why this matters:** You can always roll back. You can see what model served production last week. You have a history.

---

### Component 9: Pipeline Orchestration (`kfp_pipelines/`)

**What it is:** Kubeflow Pipelines (KFP) turns Python functions into managed, monitored, retry-able jobs that run on Vertex AI infrastructure.

**Full explanation in Section 9 below.**

---

### Component 10: Monitoring (`src/llmops_pipeline/pipelines/monitoring/`)

**What it is:** The pipeline that watches the live system and detects quality degradation.

**How it works:**
```
Cloud Logging has traces from every production request
        ↓
Monitoring pipeline pulls last N days of traces
        ↓
Runs same Gemini-as-judge evaluation on production responses
        ↓
Computes: average relevance, faithfulness, toxicity
        ↓
Compares against baseline threshold (e.g. 0.75)
        ↓
If score < threshold → Alert fires → Triggers Pipeline 1 re-run
If score >= threshold → Log metrics → Continue monitoring
```

---

## 6. The Three Pipelines Explained

### Pipeline 1: Feature Engineering

**Purpose:** Build the knowledge base (vector index).

```
Documents in GCS
      │
      ▼
CreateVectorDB job
  • Creates Vertex AI Vector Search index (if not exists)
  • Creates index endpoint
      │
      ▼ (index resource name passed forward)
IngestDocuments job
  • Loads all documents from GCS
  • Chunks them
  • Generates embeddings (text-embedding-004)
  • Upserts vectors into the index
  • Saves chunk metadata to GCS
```

**When does it run?** On schedule (e.g. weekly) or when triggered manually or by monitoring degradation.

---

### Pipeline 2: Deployment (with Quality Gate)

**Purpose:** Register the model and only promote to production if it passes evaluation.

```
RegisterModel job
  • Creates model entry in Vertex AI Model Registry
  • Labels it: stage=champion
      │
      ▼
EvaluateAndDeploy job
  • Loads QA dataset generated by Pipeline 1
  • For each QA pair:
      - Retrieves context from vector index
      - Generates answer with Gemini
      - Judge Gemini rates: relevance, faithfulness, toxicity (0 to 1)
  • Computes average scores
      │
      ├─ Score >= threshold (e.g. 0.75)?
      │     YES → Update label: stage=production
      │           Trigger Cloud Run deployment
      │           Deploy new Docker image
      │
      └─ Score < threshold?
            NO  → Block deployment
                  Alert the team
                  Label stays: champion (not promoted)
```

**This is the quality gate.** No model with scores below threshold can reach production. Ever.

---

### Pipeline 3: Monitoring

**Purpose:** Continuously check that what's running in production is still good.

```
Runs weekly (or on schedule)
      │
      ▼
Pull last 7 days of Cloud Logging traces
      │
      ▼
For each production trace:
  • Extract: user question, retrieved context, generated answer
  • Judge Gemini scores this interaction
      │
      ▼
Compute aggregate scores
      │
      ├─ Score degraded (dropped > 15% from baseline)?
      │     YES → Fire Cloud Monitoring alert
      │           Trigger Pipeline 1 re-run (self-healing loop)
      │
      └─ Score stable?
            NO  → Log to Vertex AI Experiments
                  Update monitoring dashboard
                  Continue
```

---

### The Master Pipeline (Pipeline 0)

Chains all three sequentially:

```
Feature Engineering
        ↓
Deployment (with quality gate)
        ↓
Monitoring trigger (starts the weekly cron)
```

Running `python -m kfp_pipelines.compile_and_run --pipeline master` submits this entire chain to Vertex AI.

---

## 7. GCP Services — What Each One Does

| Service | Simple Explanation | Used By |
|---|---|---|
| **Gemini 2.0 Flash** | The AI brain — generates text given instructions + context | Online serving + evaluation |
| **text-embedding-004** | Converts text to 768 numbers that represent meaning | Document ingestion + query retrieval |
| **Vertex AI Vector Search** | Database that searches by meaning, not exact words | Feature engineering + serving |
| **Vertex AI Pipelines (KFP)** | Runs Python functions as managed, retry-able cloud jobs with a visual UI | All three pipelines |
| **Vertex AI Experiments** | Records metrics from every pipeline run so you can compare and track progress | Evaluation + monitoring |
| **Vertex AI Model Registry** | Stores model configs with version labels (champion/production) | Deployment pipeline |
| **Vertex AI Agent Engine** | Manages conversation sessions and memory for the ADK agent | Serving layer |
| **Cloud Run** | Runs our FastAPI server — auto-scales from 0 to 100 instances based on traffic | Serving layer |
| **Cloud Storage (GCS)** | Like Google Drive for code and data — stores documents, embeddings, model artifacts | Everything |
| **Artifact Registry** | Stores Docker images (the packaged versions of our application code) | CI/CD + Cloud Run |
| **Cloud Logging** | Collects all logs (structured JSON) — searchable and queryable | Monitoring + debugging |
| **Cloud Trace** | Records timing of every step in every request (distributed tracing) | Performance monitoring |
| **Cloud Monitoring** | Dashboards + alerts — fires alerts when metrics cross thresholds | Monitoring pipeline |
| **Cloud DLP** | Scans text for PII (personally identifiable information) | Output guardrails |
| **Secret Manager** | Stores API keys and secrets securely (instead of .env files in production) | Production deployment |
| **Terraform** | Automatically creates all GCP resources — Cloud Run, IAM, GCS, etc. | Infrastructure setup |
| **GitHub Actions** | CI/CD system — auto-builds, tests, and deploys when you push code | CI/CD |

---

## 8. API Gateway — How It Works

### What Problem It Solves

Without an API Gateway, any user who knows your Cloud Run URL can:
- Send unlimited requests (cost explosion)
- Use your API without authentication
- Attack with malformed requests

The API Gateway is the **front door** — it controls who gets in and how much they can use.

### How It Works (Example)

```
[Your Web App]
     │
     │ sends: POST https://api.yourdomain.com/chat
     │        with: Authorization: Bearer <token>
     │
     ▼
[Cloud API Gateway]
     │
     ├─ Step 1: Validate auth token
     │   If invalid → 401 Unauthorized (request dies here)
     │
     ├─ Step 2: Check rate limit
     │   User has made 100 requests in last minute? → 429 Too Many Requests
     │
     ├─ Step 3: Cloud Armor WAF check
     │   Is this a known attack pattern (SQL injection, XSS)? → 403 Forbidden
     │
     ├─ Step 4: Log the request metadata
     │   {user_id, timestamp, endpoint, latency} → Cloud Logging
     │
     └─ Step 5: Forward to Cloud Run
          Strips the auth token, adds service headers
          Cloud Run receives: internal request
```

### In Simple Points

- **Authentication:** Every request must have a valid token. Anonymous requests blocked.
- **Rate limiting:** Max 60 requests per minute per user. Prevents abuse.
- **WAF (Web Application Firewall):** Blocks known attack patterns before they reach your code.
- **Request logging:** Every request logged with who made it and when.
- **Cost control:** Fewer requests reach Cloud Run = lower compute cost.

### In Our Project

API Gateway is a **Phase 6 (Production Hardening)** component. During lab testing and early development, Cloud Run is accessed directly. When going to production, a Cloud API Gateway sits in front. The Terraform file (`terraform/main.tf`) has the Cloud Run service defined — you would add the gateway as an additional resource.

---

## 9. Vertex AI Pipelines (KFP) — How It Works

### What Problem It Solves

Without managed pipelines, to run feature engineering you'd:
1. SSH into a server
2. Manually run scripts
3. Hope nothing crashes
4. Manually check logs
5. Restart if it fails

With Vertex AI Pipelines:
1. Define each step as a Python function with `@dsl.component`
2. Connect them in order with `@dsl.pipeline`
3. Submit once — Vertex AI runs everything on managed containers
4. Visual UI shows which steps passed/failed
5. Automatic retry on failure
6. Every run is logged and reproducible

### How KFP Code Works

```python
# Step 1: Define a component (one job = one container)
@dsl.component(
    base_image="python:3.11-slim",
    packages_to_install=["google-cloud-aiplatform", "langchain"],
)
def ingest_documents(project: str, bucket: str, chunk_size: int) -> str:
    # This code runs inside a container on Vertex AI
    # It can return values that get passed to the next component
    ... do the work ...
    return "done"

# Step 2: Define the pipeline (chain the components)
@dsl.pipeline(name="feature-engineering-pipeline")
def feature_engineering_pipeline(project: str, bucket: str):
    step1 = create_vector_db(project=project, ...)
    step2 = ingest_documents(project=project, ...)
    step2.after(step1)  # step2 only runs after step1 finishes

# Step 3: Compile to a JSON file
compiler.Compiler().compile(feature_engineering_pipeline, "fe_pipeline.json")

# Step 4: Submit to Vertex AI
aiplatform.PipelineJob(
    display_name="feature-engineering",
    template_path="fe_pipeline.json",
    ...
).submit()
```

### What You See in the UI

In GCP Console → Vertex AI → Pipelines, you see a visual graph:

```
[CreateVectorDB] ─────────────────▶ [IngestDocuments]
    PASS ✅                              PASS ✅
   (2m 15s)                           (14m 32s)
```

Each box shows: status, duration, input/output parameters, logs.

### Why Each Component Is a Separate Container

- **Isolation:** If IngestDocuments crashes, it doesn't affect other components
- **Retry:** Failed components can be retried without rerunning everything
- **Scaling:** Each component can use different machine types (IngestDocuments might need more memory for large documents)
- **Caching:** Vertex AI caches completed components — if CreateVectorDB already ran today, it won't re-run it

---

## 10. Terraform — Infrastructure as Code

### What Problem It Solves

Without Terraform, to set up the project you'd:
1. Open GCP Console
2. Click through 20 screens to create Cloud Run service
3. Click through 15 screens to create service account
4. Grant permissions one by one
5. Create GCS bucket manually
6. Set up Artifact Registry manually
7. Hope you remembered everything

With Terraform:
```bash
cd terraform
terraform apply
```
→ All resources created in ~3 minutes. Reproducible. Version-controlled.

### How Terraform Works

```
terraform/main.tf
     │
     │ defines resources
     ▼
┌──────────────────────────────────────────┐
│  resource "google_cloud_run_service"     │  → Creates Cloud Run service
│  resource "google_service_account"       │  → Creates service account
│  resource "google_storage_bucket"        │  → Creates GCS bucket
│  resource "google_artifact_registry"     │  → Creates Docker registry
│  resource "google_iam_binding"           │  → Grants permissions
│  resource "google_workload_identity"     │  → CI/CD keyless auth
└──────────────────────────────────────────┘
     │
     ▼
terraform plan   ← shows you what it WILL do (no changes yet)
terraform apply  ← actually creates the resources
terraform destroy ← deletes everything (use with caution)
```

### In Simple Points

- **Terraform is a recipe.** `main.tf` is the recipe; `terraform apply` is the cooking.
- **State file.** Terraform remembers what it created in a state file (stored in GCS bucket). This prevents duplicate resources.
- **`terraform plan`** is always safe to run. It only shows what would change — no actual changes.
- **`terraform apply`** creates/updates real resources. Review the plan first.
- **`terraform destroy`** deletes everything Terraform created. Use only to clean up.

### What Our Terraform Creates

```
terraform apply creates:
├── google_storage_bucket          — stores documents, embeddings, model artifacts
├── google_artifact_registry       — stores Docker images
├── google_cloud_run_service       — runs the FastAPI agent server
├── google_service_account × 2    — agent identity + CI/CD identity
├── google_project_iam_binding × 7 — grants minimum required permissions
└── google_iam_workload_identity   — allows GitHub Actions to deploy without keys
```

---

## 11. CI/CD — How Code Goes to Production

### What Problem It Solves

Without CI/CD:
1. Developer writes code
2. Tests locally (maybe)
3. Manually builds Docker image
4. Manually pushes to registry
5. Manually updates Cloud Run
6. Hopes nothing broke

With CI/CD (`.github/workflows/ci-cd.yml`):
1. Developer pushes code to GitHub
2. **Everything else is automatic**

### The CI/CD Flow

```
Developer pushes code
     │
     ▼
GitHub Actions triggers

[On any Pull Request]
     │
     ├─ Run linting (ruff, mypy)
     ├─ Run tests (pytest)
     ├─ Build Docker image (verify it builds)
     ├─ terraform plan (show what infra would change)
     └─ Post comment on PR with results

[On merge to main]
     │
     ├─ All tests pass
     ├─ Build Docker image
     ├─ Push to Artifact Registry (with SHA tag)
     └─ Deploy to Cloud Run (dev environment)

[On git tag (e.g. v1.2.0)]
     │
     ├─ Build Docker image (same code as dev)
     ├─ Require human approval (production gate)
     └─ deploy to Cloud Run (production environment)
```

### Workload Identity Federation (WIF) — No Keys

Traditional approach (risky):
```
GitHub has a service account JSON key file stored as a secret
→ If that secret leaks, attacker has full GCP access
→ Keys need to be rotated regularly
```

WIF approach (what we use):
```
GitHub Actions proves its identity through Google's cryptographic verification
→ No JSON key file ever created
→ Nothing to leak
→ No rotation needed
→ GitHub can only assume the CICD service account, nothing else
```

---

## 12. Config-Driven Design — The Core Idea

This is what makes this pipeline **use-case agnostic**.

### Same Pipeline, Different Use Cases

```yaml
# HR Chatbot
job:
  KIND: FeatureEngineeringJob
  documents_gcs_path: gs://bucket/hr-policies/
  chunk_size: 1000
  eval_threshold: 0.75

# IT Support Bot
job:
  KIND: FeatureEngineeringJob
  documents_gcs_path: gs://bucket/it-procedures/
  chunk_size: 800
  eval_threshold: 0.80

# Legal Knowledge Base
job:
  KIND: FeatureEngineeringJob
  documents_gcs_path: gs://bucket/legal-docs/
  chunk_size: 1500
  eval_threshold: 0.85
```

**Same Python code, different YAML = completely different system behavior.** No code changes needed to switch use cases.

### How the `KIND` Dispatcher Works

```python
# settings.py
class MainSettings(BaseSettings):
    job: JobKind = Field(..., discriminator="KIND")

# When KIND=FeatureEngineeringJob → Python creates a FeatureEngineeringJob instance
# When KIND=RegisterModelJob → Python creates a RegisterModelJob instance
# Pydantic handles the routing automatically via discriminated unions
```

---

## 13. Project Structure Walkthrough

```
final-development-llmops/
│
├── confs/                          ← YAML config files (change these for different use cases)
│   ├── feature_engineering.yaml    ← Settings for building vector index
│   ├── deployment.yaml             ← Settings for model evaluation + deployment
│   ├── monitoring.yaml             ← Settings for production monitoring
│   ├── rag_chain_config.yaml       ← RAG model, prompts, guardrail topics
│   └── generate_dataset.yaml       ← Settings for generating QA test pairs
│
├── src/llmops_pipeline/            ← Core Python package (the pipeline logic)
│   ├── settings.py                 ← Pydantic config dispatcher (KIND → Job class)
│   ├── scripts.py                  ← CLI: run any job with one command
│   ├── io/
│   │   ├── configs.py              ← OmegaConf YAML loader
│   │   ├── services.py             ← GCP service wrappers (logging, GCS, Vertex AI)
│   │   └── vector_db.py            ← Vertex AI Vector Search wrapper
│   └── pipelines/
│       ├── feature_engineering/    ← Pipeline 1 job classes
│       ├── deployment/             ← Pipeline 2 job classes
│       ├── monitoring/             ← Pipeline 3 job classes
│       └── managers/               ← Manager classes (chain multiple jobs)
│
├── kfp_pipelines/                  ← KFP pipeline definitions (Vertex AI Pipelines)
│   ├── feature_engineering.py      ← Pipeline 1 KFP definition
│   ├── deployment.py               ← Pipeline 2 KFP definition
│   ├── monitoring.py               ← Pipeline 3 KFP definition
│   ├── master.py                   ← Pipeline 0 (runs all three)
│   └── compile_and_run.py          ← Compile JSON + submit to Vertex AI
│
├── serving/                        ← Online serving layer (Cloud Run)
│   ├── server.py                   ← FastAPI app (/health, /chat, /feedback)
│   ├── agent.py                    ← Google ADK LlmAgent definition
│   ├── tools.py                    ← RAG retrieval tool (searches vector index)
│   ├── callbacks.py                ← Guardrails + interaction logging
│   ├── prompt.py                   ← System prompt template
│   └── utils/
│       ├── config.py               ← Pydantic ServerConfig (env vars)
│       └── observability.py        ← OpenTelemetry → Cloud Trace + Cloud Logging
│
├── lab_test/                       ← Lab testing layer (no billing-heavy services)
│   ├── local_vector_db.py          ← FAISS (free replacement for Vertex AI Vector Search)
│   ├── run_lab_test.py             ← Master 8-step lab test runner
│   └── 01_test_*.py to 06_test_*  ← Individual component tests
│
├── terraform/
│   └── main.tf                     ← Provisions all GCP infra (Cloud Run, GCS, IAM, etc.)
│
├── .github/workflows/
│   └── ci-cd.yml                   ← GitHub Actions: test → build → deploy
│
├── Dockerfile                      ← Multi-stage build (~200MB image, non-root user)
├── docker-compose.yml              ← Local development setup
├── pyproject.toml                  ← Python dependencies (Poetry)
└── .env.example                    ← Environment variable template
```

---

## 14. Real-World Example: HR Chatbot

Here is the complete flow for an HR chatbot, start to finish:

**Step 1 — Configure (5 minutes)**
```yaml
# confs/rag_chain_config.yaml
use_case: hr_support_bot
model: gemini-2.0-flash
documents_gcs_path: gs://company-bucket/hr-policies/
guardrails:
  valid_topics: ["leave", "benefits", "salary", "policy"]
eval_threshold: 0.75
```

**Step 2 — Upload Documents (2 minutes)**
```bash
gsutil -m cp hr_policies/*.pdf gs://company-bucket/hr-policies/
```

**Step 3 — Run Pipeline 1 — Feature Engineering (20–45 min)**
- Gemini reads 50 HR policy PDFs
- Creates ~2,400 text chunks
- Converts each to a 768-dimension vector
- Builds Vertex AI Vector Search index
- Generates 200 QA test pairs for evaluation

**Step 4 — Run Pipeline 2 — Deployment (15–30 min)**
- Registers new model config in Vertex AI Model Registry
- Runs all 200 QA pairs through the RAG chain
- Gemini-as-judge scores average 0.82 (above 0.75 threshold)
- PASSES quality gate
- Updates label to `stage=production`
- Triggers Cloud Run deployment

**Step 5 — Live Traffic**
- Employee: "What's my parental leave entitlement?"
- System retrieves: top-3 chunks from parental leave policy PDF
- Gemini generates grounded answer
- Guardrails pass (no PII, on-topic)
- Answer returned in ~2.5 seconds

**Week 4 — HR Updates the Policy**
- Pipeline 3 runs weekly, detects quality score dropped from 0.82 → 0.58
- Automatically fires Cloud Monitoring alert
- Automatically triggers Pipeline 1 re-run
- New index built from updated documents
- Pipeline 2 evaluates: 0.84 → PASS
- New version deployed
- All automated — no human intervention

---

## 15. Moving from Lab to Cloud — What Changes

| Component | Lab (What You Tested) | Production (What We Deploy) |
|---|---|---|
| Vector Search | FAISS (local, in-memory) | Vertex AI Vector Search (Matching Engine) |
| Pipeline Execution | Direct Python function calls | Vertex AI Pipelines (KFP) managed containers |
| Server | Local `uvicorn` process | Cloud Run (auto-scaled, HTTPS) |
| Auth | Manual gcloud credentials | Workload Identity Federation (keyless) |
| Secrets | `.env` file | Secret Manager |
| CI/CD | Manual script runs | GitHub Actions auto-deploy |
| Infrastructure | Manual resource creation | Terraform (automated) |
| Monitoring | Console print statements | Cloud Monitoring + alerting policies |

**The good news:** The core ML logic is identical. RAG retrieval, Gemini calls, evaluation scoring — all the same code. Only the infrastructure layer changes.

See [03-setup-guide.md](03-setup-guide.md) for the complete step-by-step production deployment guide.

---

## 16. How the Pipeline Is Dynamic — NL2SQL Example

### The Core Principle

This pipeline is built around **Google ADK tools**. The pipeline itself (orchestration, evaluation, monitoring, CI/CD) never changes. Only the **tools** the agent uses change. That is what makes it dynamic.

```
RAG Agent:      user question → search documents → Gemini answers
NL2SQL Agent:   user question → generate SQL → run on database → Gemini formats result
Drive Agent:    user question → search Google Drive → Gemini summarizes
API Agent:      user question → call external API → Gemini interprets

Pipeline 1, 2, 3: IDENTICAL in all cases
CI/CD, Terraform:  IDENTICAL in all cases
Evaluation logic:  IDENTICAL (just different QA pairs)
```

### What Changes vs What Stays the Same

| Component | RAG Agent | NL2SQL Agent | Stays Same? |
|---|---|---|---|
| `serving/tools.py` | RAG retrieval tool | SQL execution tool | ❌ Change tool |
| `serving/agent.py` | No change needed | No change needed | ✅ Same |
| `serving/prompt.py` | RAG-focused prompt | SQL-focused prompt | Update prompt |
| `confs/rag_chain_config.yaml` | RAG topics/thresholds | SQL topics/thresholds | Update values |
| `kfp_pipelines/` | Same structure | Same structure | ✅ Same |
| Evaluation QA pairs | Document Q&A | SQL result Q&A | Update dataset |
| Monitoring | Same logic | Same logic | ✅ Same |
| Terraform / CI/CD | Unchanged | Unchanged | ✅ Same |
| Feature Engineering | Ingest documents | Ingest schema descriptions | Update config |

### Step-by-Step: Convert to NL2SQL

**Step 1 — Add the SQL tool (`serving/tools.py`)**

```python
# serving/tools.py

def create_nl2sql_tool(project: str, dataset_id: str) -> T.Callable:
    """Tool that converts natural language to SQL and runs it on BigQuery."""
    from google.adk.tools import FunctionTool
    from google.cloud import bigquery

    def nl2sql(query: str) -> str:
        """Execute a natural language query against the database.
        
        Args:
            query: The user's question in plain English.
        Returns:
            Query results as a formatted string.
        """
        # Step 1: Get schema context (from a schema description stored in GCS)
        schema = _load_schema_context(project, dataset_id)
        
        # Step 2: Gemini generates SQL (agent does this via its model)
        # The agent will call this tool with the generated SQL
        
        # Step 3: Run on BigQuery
        client = bigquery.Client(project=project)
        job = client.query(query)
        rows = list(job.result())
        return str(rows[:20])  # Return top 20 rows
    
    return FunctionTool(func=nl2sql)


def create_tools(config) -> list:
    """Create tools based on use_case in config."""
    if config.USE_CASE == "nl2sql":
        return [create_nl2sql_tool(
            project=config.GCP_PROJECT_ID,
            dataset_id=config.BIGQUERY_DATASET_ID,
        )]
    elif config.USE_CASE == "rag":
        return [create_rag_retrieval_tool(
            rag_corpus_resource=config.RAG_CORPUS_RESOURCE,
        )]
    return []
```

**Step 2 — Update the config (`confs/rag_chain_config.yaml`)**

```yaml
# confs/nl2sql_config.yaml
use_case: "nl2sql"

model:
  name: "gemini-2.0-flash"
  temperature: 0.0          # Lower temp = more deterministic SQL

guardrails:
  valid_topics:
    - "sales data"
    - "customer orders"
    - "product inventory"
    - "financial reports"
  invalid_topics:
    - "delete"
    - "drop table"          # Block destructive SQL
    - "insert"
    - "update"
  pii_detection: true

bigquery:
  dataset_id: "company_analytics"
  allowed_tables:
    - "orders"
    - "products"
    - "customers"
```

**Step 3 — Update the prompt (`serving/prompt.py`)**

```python
# serving/prompt.py

def get_system_prompt():
    return """
    You are a data analyst assistant. When the user asks a question:
    1. Think about what SQL query would answer it
    2. Use the nl2sql tool to run the query
    3. Explain the results in plain English
    
    Rules:
    - Never suggest DELETE, UPDATE, INSERT, or DROP queries
    - Only query the allowed tables: orders, products, customers
    - Always explain what the data means in business terms
    """
```

**Step 4 — Update Feature Engineering config**

Instead of ingesting PDF documents, ingest database schema descriptions:

```yaml
# confs/feature_engineering.yaml
job:
  KIND: FeatureEngineeringJob
  # Instead of PDF documents, use schema description TXT files
  documents_gcs_path: gs://bucket/schema-descriptions/
  # e.g. "orders.txt" contains: "The orders table has columns: order_id, customer_id,
  #  order_date, total_amount, status. Each row represents one customer order."
```

This gives the agent semantic knowledge of what each table/column means.

**Step 5 — Update evaluation dataset**

Generate QA pairs that test SQL reasoning instead of document retrieval:

```python
# In confs/generate_dataset.yaml, update sample QA pairs:
# "What were the top 5 products by revenue last month?"
# "How many orders were placed in Q4 2025?"
# "Which customers have not ordered in 90 days?"
```

**Everything else is unchanged.** Pipeline 2 (quality gate), Pipeline 3 (monitoring), CI/CD, Terraform, Cloud Run — all identical. Only the tool, prompt, and config changed.

### Other Use Cases You Can Build

| Agent Type | Tool to Add | What Changes |
|---|---|---|
| **NL2SQL** | BigQuery execution tool | `tools.py` + prompt + config |
| **Google Drive** | `DriveSearchTool` from ADK | `tools.py` + prompt |
| **Web Search** | `GoogleSearchTool` from ADK | `tools.py` + prompt |
| **HR Forms** | HTTP API call tool | `tools.py` + prompt |
| **Code Assistant** | Code execution tool | `tools.py` + prompt |
| **Multi-tool** | Combine 2–3 tools | `tools.py` — agent decides which to use |

In every case: **add the tool to `tools.py`, update the prompt, update the YAML config. The 3 pipelines, CI/CD, and Terraform never change.**

---

*Document version: 2.0 | Project: final-development-llmops | Reflects actual codebase*

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
