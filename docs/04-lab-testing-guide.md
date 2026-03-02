# GCP Lab Testing Guide

**File:** `docs/04-lab-testing-guide.md`  
**Project:** `final-development-llmops`  
**Audience:** LLMOps learners using Qwiklabs / Cloud Skills Boost or any free-tier GCP lab

---

## Table of Contents

1. [What Is a GCP Lab?](#1-what-is-a-gcp-lab)
2. [What Works and What Doesn't](#2-what-works-and-what-doesnt)
3. [How the Lab Test Layer Works](#3-how-the-lab-test-layer-works)
4. [Step-by-Step Setup](#4-step-by-step-setup)
5. [Running Individual Tests](#5-running-individual-tests)
6. [Running the Full End-to-End Test](#6-running-the-full-end-to-end-test)
7. [Expected Output](#7-expected-output)
8. [Production vs. Lab Comparison](#8-production-vs-lab-comparison)
9. [Common Errors and Fixes](#9-common-errors-and-fixes)
10. [Adding Your Own Documents](#10-adding-your-own-documents)
11. [Next Steps After Lab](#11-next-steps-after-lab)

---

## 1. What Is a GCP Lab?

GCP labs (Qwiklabs, Cloud Skills Boost, internal sandboxes) give you a **temporary GCP project** with:
- A real Project ID (e.g. `qwiklabs-gcp-01-abc123`)
- **Pre-authenticated** credentials — no `gcloud auth login` needed
- A **time limit** (usually 1–2 hours)
- **Restricted billing** — many expensive APIs are blocked

The challenge: our production LLMOps pipeline uses **Vertex AI Vector Search (Matching Engine)** and **Vertex AI Pipelines (KFP)**, both of which are billing-heavy and often restricted in labs.

This guide shows you how to test every part of the pipeline using the `lab_test/` layer that replaces those services with free alternatives.

---

## 2. What Works and What Doesn't

| Service | Production | Lab | Why |
|---|---|---|---|
| Gemini 2.0 Flash | ✅ | ✅ | Free via Vertex AI API in most labs |
| VertexAI Embeddings (`text-embedding-004`) | ✅ | ✅ | Free API calls |
| Google Cloud Storage (GCS) | ✅ | ✅ | Standard storage, low cost |
| Cloud Logging | ✅ | ✅ | Usually enabled |
| Cloud Run | ✅ | ✅ | May require billing but small scale OK |
| **Vertex AI Vector Search** | ✅ | ❌ → FAISS | Requires billing (Matching Engine) |
| **Vertex AI Pipelines (KFP)** | ✅ | ❌ → local Python | Requires Vertex AI cluster |
| Artifact Registry | ✅ | ⚠️ limited | May hit quota |
| Vertex AI Training | ✅ | ❌ | Billing required |

**Bottom line:** Gemini + Embeddings + GCS = ✅ in lab. Everything else is replaced with free local alternatives.

---

## 3. How the Lab Test Layer Works

```
Production Pipeline                Lab Test Replacement
──────────────────────────────     ──────────────────────────────
Vertex AI Vector Search         →  LocalFaissVectorDB  (faiss-cpu)
KFP Pipeline on Vertex AI       →  Direct Python function calls
Vertex AI Managed RAG           →  FAISS retrieve + Gemini generate
Vertex AI Artifact Registry     →  Local /tmp storage
ADK Agent (full)                →  FastAPI server (serving/)
```

The lab test layer lives entirely in `lab_test/`:

```
lab_test/
├── __init__.py              Package marker
├── local_vector_db.py       LocalFaissVectorDB — drop-in FAISS replacement
├── requirements_lab.txt     Lightweight pip requirements for lab
├── .env.lab                 Lab environment variable template
├── run_lab_test.py          Master 8-step end-to-end runner
├── 01_test_gemini.py        Step 1: Gemini + embedding connectivity
├── 02_test_gcs.py           Step 2: GCS bucket + CRUD
├── 03_test_vector_db.py     Step 3: FAISS build, query, persist
├── 04_test_rag_pipeline.py  Step 4: RAG end-to-end (no server)
├── 05_test_evaluation.py    Step 5: Gemini-as-judge evaluation
└── 06_test_serving.py       Step 6: FastAPI server + /chat endpoint
```

---

## 4. Step-by-Step Setup

### 4.1 Get Your Project ID

In the GCP lab, click the lab info panel or run:

```bash
gcloud config get-value project
```

Note the project ID (e.g. `qwiklabs-gcp-01-abc123`). You will pass it to every script with `--project`.

### 4.2 Open Cloud Shell

In the GCP Console, click the **Cloud Shell** icon (top right). This gives you an authenticated terminal in your lab project.

### 4.3 Clone or Copy the Project

If you have the project in Cloud Shell already:
```bash
cd ~/final-development-llmops
```

If not, upload it via Cloud Shell Editor → Upload files, or use:
```bash
git clone <your-repo-url>  # if you pushed to GitHub
cd final-development-llmops
```

### 4.4 Install Lab Dependencies

```bash
# Install lab-specific requirements (lighter than full pyproject.toml)
pip install -r lab_test/requirements_lab.txt
```

If you get permission errors:
```bash
pip install --user -r lab_test/requirements_lab.txt
```

### 4.5 Configure Environment

```bash
# Copy lab env template
cp lab_test/.env.lab .env

# Edit with your project ID
nano .env
```

Update these lines:
```
GCP_PROJECT_ID=qwiklabs-gcp-01-YOUR-ACTUAL-ID
GCS_BUCKET=qwiklabs-gcp-01-YOUR-ACTUAL-ID-llmops-lab
```

Save and exit (`Ctrl+X`, `Y`, `Enter`).

### 4.6 Set Shell Variables (Faster Than Editing .env)

For quick testing, just export these in your shell:

```bash
export PROJECT_ID=$(gcloud config get-value project)
export LOCATION=us-central1
export BUCKET="${PROJECT_ID}-llmops-lab"
echo "Project: $PROJECT_ID"
echo "Bucket: $BUCKET"
```

---

## 5. Running Individual Tests

Run each test script in order. If a step fails, fix it before moving on.

### Test 01 — Gemini Connectivity

```bash
python lab_test/01_test_gemini.py --project $PROJECT_ID
```

**What it tests:**
- `aiplatform.init()` — GCP credentials
- `ChatVertexAI` — Gemini 2.0 Flash responds
- `VertexAIEmbeddings` — embeddings return 768 dimensions

**Expected output:**
```
[TEST 1] GCP Connectivity... PASS
[TEST 2] Gemini 2.0 Flash... PASS  Response: GEMINI_OK
[TEST 3] VertexAI Embeddings... PASS  Dimensions: 768
============================================================
All 3 tests PASSED
```

---

### Test 02 — GCS Bucket

```bash
python lab_test/02_test_gcs.py \
  --project $PROJECT_ID \
  --bucket $BUCKET
```

**What it tests:**
- Create or access the GCS bucket
- Upload a test file
- Download and verify content
- List blobs
- Delete test file

**Expected output:**
```
[TEST 1] GCP Connectivity... PASS
[TEST 2] GCS Bucket Access... PASS  Bucket: <bucket-name>
[TEST 3] GCS Write... PASS
[TEST 4] GCS Read... PASS
[TEST 5] GCS List... PASS
[TEST 6] GCS Delete... PASS
============================================================
All 6 tests PASSED
```

---

### Test 03 — FAISS Vector DB

```bash
python lab_test/03_test_vector_db.py \
  --project $PROJECT_ID \
  --docs-path data/documents
```

**What it tests:**
- Creates sample documents (`.txt` files) if folder is empty
- Builds FAISS index with real embeddings
- Runs 3 test queries
- Saves index to `/tmp/lab_faiss_index`
- Reloads and verifies vector count matches

**Important (Lab):** Only `.txt` files are supported in lab tests. This avoids permission issues with `unstructured` library and spaCy model installation. For production, PDFs are supported.
```
[TEST 1] GCP Connectivity... PASS
[TEST 2] Document Loading... PASS  Chunks: 12
[TEST 3] FAISS Index Build... PASS  Vectors: 12
[TEST 4] Vector Query... PASS  Top result (score=0.82): ...
[TEST 5] FAISS Persist... PASS  Saved to /tmp/lab_faiss_index
[TEST 6] FAISS Reload... PASS  Reloaded 12 vectors
============================================================
All 6 tests PASSED
```

> **Note:** Embedding calls take 10–30 seconds depending on document count.

---

### Test 04 — RAG Pipeline

```bash
python lab_test/04_test_rag_pipeline.py \
  --project $PROJECT_ID \
  --query "What is the annual leave policy?"
```

**What it tests:**
- Loads or builds FAISS index
- Retrieves top-3 relevant chunks for the query
- Sends them to Gemini with a RAG prompt
- Prints the generated answer

**Expected output:**
```
[1/4] Connecting to GCP...  DONE
[2/4] Loading FAISS index... DONE (12 vectors)
[3/4] Retrieving context for: "What is the annual leave policy?"
      Top chunk (score=0.84): Employees are entitled to 20 days...
[4/4] Generating answer with Gemini...

ANSWER:
Based on the HR policy documents, employees are entitled to 20 annual
leave days per year. Leave must be approved by the line manager at
least 2 weeks in advance...
```

---

### Test 05 — Evaluation (Gemini as Judge)

```bash
python lab_test/05_test_evaluation.py --project $PROJECT_ID
```

**What it tests:**
- Uses 2 built-in sample QA pairs (no dataset file needed)
- For each pair: generates a RAG answer → asks Gemini to rate it
- Computes average relevance + factuality scores
- Prints PASS (≥ 3.0) or BORDERLINE/FAIL

**Expected output:**
```
Evaluating 2 QA pairs...

QA 1: What is the annual leave entitlement?
  Answer generated. Judge scores: relevance=4, factuality=4, completeness=3

QA 2: How do I submit an IT support ticket?
  Answer generated. Judge scores: relevance=5, factuality=4, completeness=5

============================================================
Evaluation Summary
  Average Relevance:    4.50 / 5
  Average Factuality:   4.00 / 5
  Average Completeness: 4.00 / 5
  Overall Average:      4.17 / 5
  Result: PASS (threshold: 3.0)
```

---

### Test 06 — Serving Layer

```bash
python lab_test/06_test_serving.py \
  --project $PROJECT_ID \
  --location $LOCATION
```

**What it tests:**
- Starts the FastAPI server (`serving/server.py`)
- Polls `/health` until server is ready (with 30s timeout for lab cold start)
- Sends a POST to `/chat` with a test question
- Asserts non-empty response

**Note:** Server startup takes longer in GCP lab due to cold Python imports + Gemini client initialization. Expect 15-30 seconds. If it times out, see [Server Won't Start](#server-wont-start-port-in-use) section.

**Expected output:**
```
Starting server on port 8080...
Waiting for server...
Server is up!
Health: {"status": "healthy"}
✅ /health OK
Chat response (2500ms): "Based on the HR policies, you can request annual leave by..."
✅ /chat OK
```

---

## 6. Running the Full End-to-End Test

Run all 8 steps in one command:

```bash
python lab_test/run_lab_test.py \
  --project $PROJECT_ID \
  --location $LOCATION \
  --bucket $BUCKET \
  --docs-path data/documents
```

### Useful Flags

| Flag | Purpose |
|---|---|
| `--step 3` | Run only step 3 (skips all others) |
| `--skip-gcs` | Skip GCS test (if bucket not ready) |
| `--skip-serving` | **Skip serving test** (recommended for quick lab validation) |
| `--port 8081` | Use a different port for serving |

### Skip-Serving Example (Fastest for Lab)

```bash
python lab_test/run_lab_test.py \
  --project $PROJECT_ID \
  --location $LOCATION \
  --bucket $BUCKET \
  --skip-serving
```

This validates all 6 core steps (GCP, GCS, Gemini, FAISS, evaluation) without waiting for server startup. **Best for quick lab verification.**

### Run Only RAG step

```bash
python lab_test/run_lab_test.py \
  --project $PROJECT_ID \
  --location $LOCATION \
  --bucket $BUCKET \
  --step 4
```

### Full Run Expected Output

**Fast path (6/7 core tests, no server):**

```bash
python lab_test/run_lab_test.py \
  --project $PROJECT_ID \
  --location $LOCATION \
  --bucket $BUCKET \
  --skip-serving
```

```
============================================================
LLMOps Lab Test Runner
Project: qwiklabs-gcp-03-abc123   Location: us-central1
============================================================

[Step 1/6] GCP Connectivity...          PASS
[Step 2/6] GCS Bucket...                PASS
[Step 3/6] Gemini API...                PASS
[Step 4/6] Feature Eng (FAISS build)... PASS   12 chunks, 12 vectors
[Step 5/6] Dataset Generation...        PASS   2 QA pairs saved
[Step 6/6] Evaluation...                PASS   avg score: 4.17/5

============================================================
RESULTS (No Serving)
  Passed:  6 / 6
  Skipped: 1 / 7 (serving)
  
ALL CORE TESTS PASSED — Lab validation complete!
============================================================
```

**Full run with serving (all 7 steps):**

Expected time: ~1-2 minutes (first 6 steps ~40s + server startup 15-30s).

```
============================================================
LLMOps Lab Test Runner
Project: qwiklabs-gcp-03-abc123   Location: us-central1
============================================================

[Step 1/7] GCP Connectivity...          PASS
[Step 2/7] GCS Bucket...                PASS
[Step 3/7] Gemini API...                PASS
[Step 4/7] Feature Eng (FAISS build)... PASS   12 chunks, 12 vectors
[Step 5/7] Dataset Generation...        PASS   2 QA pairs saved
[Step 6/7] Evaluation...                PASS   avg score: 4.17/5
[Step 7/7] Serving (start server)...    PASS   ready in 22.5s
[Step 8/8] Chat Test...                 PASS

============================================================
RESULTS
  Passed:  8 / 8
  Failed:  0 / 8
  Skipped: 0 / 8

ALL TESTS PASSED — Lab validation complete!
============================================================
```

---

## 7. Expected Output

### Green Flags (Everything Working)

- `aiplatform.init()` completes without `403 Permission denied`
- Gemini responds to a simple prompt within 5–10 seconds
- FAISS index builds with at least 1 chunk
- `/health` returns `{"status": "ok"}`
- `/chat` returns a non-empty answer string

### Yellow Flags (Investigate But Not Fatal)

- Embedding call takes > 30 seconds (lab quota throttling — retry)
- GCS bucket creation gets `409 Already exists` (fine — it reuses)
- FAISS score is low (< 0.5) — your documents may have unrelated content

---

## 8. Production vs. Lab Comparison

| Component | Production | Lab | Code Change? |
|---|---|---|---|
| Vector Store | Vertex AI Vector Search (Matching Engine) | `LocalFaissVectorDB` (faiss-cpu) | New class, same interface |
| Pipeline Execution | Vertex AI Pipelines (KFP cluster) | Direct Python function calls | No — same Job classes |
| RAG Retrieval | Managed RAG corpus | FAISS `.query()` + manual context | New retrieve method |
| Embeddings | `text-embedding-004` via Vertex AI | Same API | No change |
| LLM | Gemini 2.0 Flash | Same model | No change |
| Evaluation | Gemini judge + custom metrics | Same logic, sample QA pairs | Same code |
| Serving | ADK Agent on Cloud Run | FastAPI on localhost | No change (same server.py) |
| Monitoring | Vertex AI Experiments + Cloud Logging | Cloud Logging only | Feature flag |
| Secrets | Secret Manager | `.env` file | Config swap |
| CI/CD | GitHub Actions → Cloud Build | Run scripts manually | Skip CI/CD |

**Key insight:** The core ML logic (embeddings, LLM, evaluation) is identical in both environments. Only the **infrastructure wrappers** change.

---

## 9. Common Errors and Fixes

### `403 Permission denied` on Vertex AI

```
google.api_core.exceptions.PermissionDenied: 403 Vertex AI API has not been used
```

**Fix:** Enable the API:
```bash
gcloud services enable aiplatform.googleapis.com
```

---

### `403 Permission denied` on GCS

```
google.api_core.exceptions.Forbidden: 403 does not have storage.buckets.create access
```

**Fix:** In some labs, bucket creation is restricted. Try:
```bash
gsutil mb gs://$BUCKET
```
Or ask the lab to pre-create a bucket and use its name.

---

### `ModuleNotFoundError: No module named 'faiss'`

```bash
pip install faiss-cpu
```

---

### `ModuleNotFoundError: No module named 'unstructured'`

```bash
pip install unstructured PyPDF2
```

Or: Remove PDFs from `data/documents/` and use only `.txt` files (recommended for lab).

---

### `RuntimeError: Failed to install en_core_web_sm` / spaCy Permission Error

```
PermissionError: [Errno 13] Permission denied: '/usr/local/lib/python3.12/dist-packages/en_core_web_sm'
```

**Fix:** The `unstructured` library tries to install spaCy models to a system directory. In the lab, this fails due to write restrictions.

**Solution:** Use only `.txt` files instead of PDFs:
```bash
# Remove any PDFs
rm data/documents/*.pdf

# Add only .txt files
cp ~/my-docs/*.txt data/documents/
```

Then re-run the test. The lab version of `local_vector_db.py` is optimized for `.txt` files and avoids the `unstructured` library entirely.

---

```
ResourceExhausted: 429 Quota exceeded for quota metric...
```

**Fix:** Add a pause between embedding calls. Edit `local_vector_db.py`:
```python
import time
time.sleep(2)  # add after each batch call
```

Or reduce document length and count.

---

### Server Won't Start (Timeout / Takes > 30s)

```
Server did not start within 10s
```

**Root cause:** FastAPI server cold start in GCP lab takes 15–30 seconds due to Python imports + Gemini client initialization. The old timeout was too short.

**Fix:** This has been fixed in the latest code — timeout increased from 10s → 30s.

If you still see this error:

1. **Run with `--skip-serving`** (fastest workaround):
   ```bash
   python lab_test/run_lab_test.py --project $PROJECT_ID ... --skip-serving
   ```
   This validates all core RAG logic (steps 1–6) without testing the server.

2. **Test serving separately** (if you want to debug):
   ```bash
   # In terminal 1 — start server manually
   python -m uvicorn serving.server:app --host 0.0.0.0 --port 8080

   # In terminal 2 — test with external flag
   python lab_test/06_test_serving.py \
     --project $PROJECT_ID \
     --location $LOCATION \
     --external-server \
     --port 8080
   ```

3. **Check server logs** — The updated code now prints server startup errors:
   ```bash
   python lab_test/run_lab_test.py ... 2>&1 | grep "Server startup error"
   ```

---

### Empty FAISS Query Results

```
No results returned from FAISS query
```

**Fix:** The index has 0 vectors. This means document loading failed.
Check:
```bash
ls -la data/documents/
```
If empty, the test scripts auto-create sample docs. Run script 03 first to force-create them.

---

### `ValueError: Index dimension mismatch`

**Fix:** Your saved FAISS index dimension doesn't match the embedding model.
Delete the old index and rebuild:
```bash
rm -rf /tmp/lab_faiss_index
python lab_test/03_test_vector_db.py --project $PROJECT_ID
```

---

### Gemini Returns Empty Response

**Fix:** Check your quota in GCP Console → Vertex AI → Quotas.
Try reducing the prompt length or adding a retry:
```python
import time
for attempt in range(3):
    response = llm.invoke(prompt)
    if response.content:
        break
    time.sleep(5)
```

---

## 10. Adding Your Own Documents

By default, the test scripts create 3 sample HR/IT policy documents. Replace them with your own:

```bash
# Remove sample documents
rm data/documents/*.txt

# Add your own TXT files (PDF not supported in lab due to permission restrictions)
cp ~/my-docs/*.txt data/documents/
```

Then rebuild the FAISS index:
```bash
python lab_test/03_test_vector_db.py \
  --project $PROJECT_ID \
  --docs-path data/documents
```

**Supported formats (Lab):**
- `.txt` — plain text (RECOMMENDED for lab)

**Note:** PDFs are not supported in GCP lab tests to avoid `unstructured` library + spaCy model permission issues. For production use, convert PDFs to text first, or the production code handles PDF parsing natively.

**Tips for good retrieval:**
- Each document should be 500–3000 words
- Keep documents focused on one topic each
- Use clear headers and sections
- Save as UTF-8 `.txt` files

---

## 11. Next Steps After Lab

Once all 8 lab tests pass, you have validated the full pipeline logic. To move to production:

### 1 — Enable Billing Services

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com
```

### 2 — Create Real Vector Search Index

Replace `LocalFaissVectorDB` with production `VertexVectorSearch`:
```python
# In production code (src/llmops_pipeline/...)
# The VertexVectorSearch class already exists — just switch configs
```

Set in your `.env`:
```
RAG_CORPUS_RESOURCE=projects/YOUR_PROJECT/locations/us-central1/ragCorpora/YOUR_CORPUS_ID
```

### 3 — Compile and Deploy KFP Pipelines

```bash
python scripts/compile_pipelines.py  # compile KFP YAML
python scripts/vertex_pipelines.py   # submit to Vertex AI
```

### 4 — Set Up CI/CD

Push to GitHub. The `.github/workflows/ci-cd.yml` handles:
- Lint + test
- Docker build + push to Artifact Registry
- Deploy to Cloud Run

### 5 — Monitor

- View pipeline runs: Vertex AI Console → Pipelines
- View logs: Cloud Logging → Filter by `llmops`
- View evaluations: Vertex AI Console → Experiments

---

## Summary

```
Lab Test Flow:
run 01 → Gemini OK
run 02 → GCS OK
run 03 → FAISS vector DB built
run 04 → RAG retrieval + generation OK
run 05 → Evaluation OK
run 06 → Serving OK
──────────────────
All OK → Ready for production
```

The `lab_test/` layer lets you validate the entire LLMOps pipeline — document ingestion, vector search, RAG generation, evaluation, and serving — without any billing-restricted services.

---

*Guide version: 1.0 | Project: final-development-llmops | Last updated: 2025*
