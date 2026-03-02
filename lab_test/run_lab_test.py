"""End-to-end lab test runner.

Runs the complete LLMOps pipeline locally — NO Vertex AI Vector Search, NO KFP cluster.
Designed for GCP lab environments where billing-heavy services are restricted.

What this script does:
  Step 1 — Check GCP connectivity (project, credentials)
  Step 2 — Check GCS (create bucket, upload/download a file)
  Step 3 — Check Gemini API (send a test prompt)
  Step 4 — Feature Engineering (ingest sample docs → FAISS local index)
  Step 5 — Generate QA dataset (Gemini creates QA pairs from chunks)
  Step 6 — Deployment evaluation (Gemini-as-judge rates answers)
  Step 7 — Start serving layer (FastAPI + ADK-style chat)
  Step 8 — End-to-end chat test (query → FAISS retrieval → Gemini answer)

Usage:
    python lab_test/run_lab_test.py --project YOUR_PROJECT --bucket YOUR_BUCKET
    python lab_test/run_lab_test.py --project YOUR_PROJECT --skip-gcs
    python lab_test/run_lab_test.py --project YOUR_PROJECT --step 3  # run specific step only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("lab_test")

# --------------------------------------------------------------------------- #
# Result tracking                                                               #
# --------------------------------------------------------------------------- #

RESULTS: dict[str, str] = {}

def ok(step: str, msg: str = ""):
    RESULTS[step] = f"✅  PASS  {msg}"
    logger.info("✅  PASS  [%s] %s", step, msg)

def fail(step: str, msg: str = ""):
    RESULTS[step] = f"❌  FAIL  {msg}"
    logger.error("❌  FAIL  [%s] %s", step, msg)

def skip(step: str, msg: str = ""):
    RESULTS[step] = f"⏭️   SKIP  {msg}"
    logger.info("⏭️   SKIP  [%s] %s", step, msg)

def print_summary():
    print("\n" + "="*60)
    print("  LAB TEST RESULTS SUMMARY")
    print("="*60)
    for step, result in RESULTS.items():
        print(f"  {step:30s}  {result}")
    total = len(RESULTS)
    passed = sum(1 for v in RESULTS.values() if "PASS" in v)
    print("="*60)
    print(f"  Passed: {passed} / {total}")
    print("="*60 + "\n")

# --------------------------------------------------------------------------- #
# Step 1: GCP Connectivity                                                      #
# --------------------------------------------------------------------------- #

def step1_gcp_connectivity(project: str, location: str):
    logger.info("="*50)
    logger.info("STEP 1: GCP Connectivity")
    try:
        from google.cloud import aiplatform
        aiplatform.init(project=project, location=location)
        logger.info("google-cloud-aiplatform initialised for project: %s", project)
        ok("Step 1 — GCP connectivity", f"project={project}")
    except Exception as e:
        fail("Step 1 — GCP connectivity", str(e))
        raise SystemExit(1)

# --------------------------------------------------------------------------- #
# Step 2: GCS Basic Operations                                                  #
# --------------------------------------------------------------------------- #

def step2_gcs(project: str, bucket: str):
    logger.info("="*50)
    logger.info("STEP 2: Cloud Storage")
    try:
        from google.cloud import storage
        client = storage.Client(project=project)

        # Create bucket if not exists
        try:
            b = client.get_bucket(bucket)
            logger.info("GCS bucket already exists: gs://%s", bucket)
        except Exception:
            b = client.create_bucket(bucket, location="us-central1")
            logger.info("Created GCS bucket: gs://%s", bucket)

        # Upload test file
        blob = b.blob("lab_test/connectivity_check.txt")
        blob.upload_from_string("LLMOps lab test — GCS write OK")

        # Download and verify
        content = blob.download_as_text()
        assert "GCS write OK" in content, "GCS read/write mismatch"

        # Clean up
        blob.delete()

        ok("Step 2 — GCS read/write", f"gs://{bucket}")
    except Exception as e:
        fail("Step 2 — GCS read/write", str(e))

# --------------------------------------------------------------------------- #
# Step 3: Gemini API                                                            #
# --------------------------------------------------------------------------- #

def step3_gemini(project: str, location: str):
    logger.info("="*50)
    logger.info("STEP 3: Gemini API")
    try:
        from langchain_google_vertexai import ChatVertexAI
        llm = ChatVertexAI(
            model_name="gemini-2.0-flash",
            temperature=0.0,
            project=project,
            location=location,
        )
        response = llm.invoke("Reply with exactly: GEMINI_OK")
        assert response.content, "Empty response from Gemini"
        logger.info("Gemini response: %s", response.content[:100])
        ok("Step 3 — Gemini API", f"model=gemini-2.0-flash, response_len={len(response.content)}")
    except Exception as e:
        fail("Step 3 — Gemini API", str(e))

# --------------------------------------------------------------------------- #
# Step 4: Feature Engineering (Local FAISS)                                     #
# --------------------------------------------------------------------------- #

def step4_feature_engineering(project: str, location: str, docs_path: str) -> "LocalFaissVectorDB | None":
    logger.info("="*50)
    logger.info("STEP 4: Feature Engineering (Local FAISS)")
    try:
        from lab_test.local_vector_db import LocalFaissVectorDB

        # Create sample documents if docs_path is empty
        _ensure_sample_docs(docs_path)

        vdb = LocalFaissVectorDB(
            project=project,
            location=location,
            embedding_model="text-embedding-004",
            embedding_dimensions=768,
        )

        result = vdb.ingest_documents(docs_path, chunk_size=500, chunk_overlap=100)
        logger.info("Ingestion result: %s", result)

        # Verify query
        results = vdb.query("What is the company leave policy?", top_k=3)
        logger.info("Query returned %d results", len(results))

        # Save locally
        vdb.save_local("/tmp/lab_faiss_index")

        ok("Step 4 — Feature Engineering (FAISS)",
           f"docs={result['num_documents']}, chunks={result['num_chunks']}, query_results={len(results)}")
        return vdb
    except Exception as e:
        fail("Step 4 — Feature Engineering (FAISS)", str(e))
        return None

# --------------------------------------------------------------------------- #
# Step 5: Generate QA Dataset                                                   #
# --------------------------------------------------------------------------- #

def step5_generate_dataset(vdb, project: str, location: str):
    logger.info("="*50)
    logger.info("STEP 5: Generate QA Dataset (Gemini)")
    try:
        if vdb is None or len(vdb.chunks) == 0:
            skip("Step 5 — QA Dataset generation", "no vectors (step 4 failed)")
            return []

        from langchain_google_vertexai import ChatVertexAI
        import json

        llm = ChatVertexAI(model_name="gemini-2.0-flash", temperature=0.7,
                           project=project, location=location)

        # Only generate for first 2 chunks to stay within lab quota
        qa_pairs = []
        for chunk_text in vdb.chunks[:2]:
            prompt = (
                f"Based on this text, generate 2 question-answer pairs.\n"
                f"Return as JSON array: "
                f'[{{"question": "...", "expected_answer": "..."}}]\n\n'
                f"Text:\n{chunk_text[:500]}"
            )
            try:
                resp = llm.invoke(prompt)
                # Strip markdown code block if present
                raw = resp.content.strip().strip("```json").strip("```").strip()
                pairs = json.loads(raw)
                qa_pairs.extend(pairs)
            except Exception as e:
                logger.warning("QA generation failed for chunk: %s", e)

        # Save dataset
        os.makedirs("data/datasets", exist_ok=True)
        with open("data/datasets/lab_eval.json", "w") as f:
            json.dump(qa_pairs, f, indent=2)

        logger.info("Generated %d QA pairs → data/datasets/lab_eval.json", len(qa_pairs))
        ok("Step 5 — QA Dataset generation", f"num_pairs={len(qa_pairs)}")
        return qa_pairs
    except Exception as e:
        fail("Step 5 — QA Dataset generation", str(e))
        return []

# --------------------------------------------------------------------------- #
# Step 6: Deployment Evaluation (Gemini-as-Judge)                               #
# --------------------------------------------------------------------------- #

def step6_evaluation(qa_pairs: list, vdb, project: str, location: str):
    logger.info("="*50)
    logger.info("STEP 6: Deployment Evaluation (Gemini-as-Judge)")
    try:
        if not qa_pairs or vdb is None:
            skip("Step 6 — Evaluation", "no QA pairs or index (previous step failed)")
            return

        from langchain_google_vertexai import ChatVertexAI
        import json

        llm = ChatVertexAI(model_name="gemini-2.0-flash", temperature=0.0,
                           project=project, location=location)

        scores = {"answer_relevance": [], "faithfulness": [], "toxicity": []}
        for pair in qa_pairs[:3]:  # Limit to 3 for lab
            # Retrieve context
            results = vdb.query(pair["question"], top_k=3)
            context = "\n\n".join([r["text"] for r in results])

            # Generate answer using RAG
            rag_prompt = (
                f"Context:\n{context}\n\n"
                f"Question: {pair['question']}\n\n"
                f"Answer based only on the context above:"
            )
            answer_resp = llm.invoke(rag_prompt)
            answer = answer_resp.content

            # Judge
            judge_prompt = (
                f"Evaluate this QA pair (0.0 to 1.0):\n"
                f"Question: {pair['question']}\n"
                f"Expected: {pair.get('expected_answer', '')}\n"
                f"Generated: {answer}\n"
                f"Context: {context[:500]}\n\n"
                f'JSON: {{"answer_relevance": X, "faithfulness": X, "toxicity": X}}'
            )
            eval_resp = llm.invoke(judge_prompt)
            try:
                raw = eval_resp.content.strip().strip("```json").strip("```").strip()
                eval_data = json.loads(raw)
                for k in scores:
                    scores[k].append(float(eval_data.get(k, 0.0)))
            except Exception:
                pass

        avgs = {k: sum(v)/len(v) if v else 0.0 for k, v in scores.items()}
        logger.info("Evaluation scores: %s", avgs)

        decision = "PASS" if avgs["answer_relevance"] >= 0.6 else "BORDERLINE"
        ok("Step 6 — Evaluation (Gemini-as-Judge)",
           f"relevance={avgs['answer_relevance']:.2f}, faithfulness={avgs['faithfulness']:.2f}, decision={decision}")
    except Exception as e:
        fail("Step 6 — Evaluation", str(e))

# --------------------------------------------------------------------------- #
# Step 7: Start Serving Layer (local)                                           #
# --------------------------------------------------------------------------- #

def step7_serving(project: str, location: str, port: int = 8080):
    logger.info("="*50)
    logger.info("STEP 7: Serving Layer (local FastAPI)")
    try:
        import subprocess, time, urllib.request

        # Set env vars for the server
        env = os.environ.copy()
        env.update({
            "GCP_PROJECT_ID": project,
            "GCP_LOCATION": location,
            "MODEL_NAME": "gemini-2.0-flash",
            "PORT": str(port),
            "RAG_CORPUS_RESOURCE": "",  # Empty = no remote RAG corpus needed for lab
        })

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "serving.server:app",
             "--host", "0.0.0.0", "--port", str(port), "--timeout-keep-alive", "5"],
            env=env,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for startup (30s timeout for lab cold start)
        max_retries = 30
        for attempt in range(max_retries):
            time.sleep(1)
            try:
                urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
                ok("Step 7 — Serving layer started", f"http://localhost:{port}")
                return proc
            except Exception as e:
                if attempt % 5 == 0 and attempt > 0:
                    logger.debug(f"  Still waiting... ({attempt}s elapsed)")

        # If failed, show server logs for debugging
        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=2)
            if stderr:
                logger.error("Server startup error:\n%s", stderr.decode('utf-8', errors='ignore')[:500])
        except:
            pass

        fail("Step 7 — Serving layer", f"server did not start within {max_retries}s")
        return None
    except Exception as e:
        fail("Step 7 — Serving layer", str(e))
        return None

# --------------------------------------------------------------------------- #
# Step 8: End-to-End Chat Test                                                  #
# --------------------------------------------------------------------------- #

def step8_chat_test(port: int = 8080):
    logger.info("="*50)
    logger.info("STEP 8: End-to-End Chat Test")
    try:
        import urllib.request, json

        payload = json.dumps({"query": "Hello, what can you help me with?", "session_id": "lab-test"}).encode()
        req = urllib.request.Request(
            f"http://localhost:{port}/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        response_text = data.get("response", "")
        latency = data.get("latency_ms", 0)
        assert response_text, "Empty chat response"
        logger.info("Chat response (%d ms): %s...", latency, response_text[:100])
        ok("Step 8 — End-to-end chat", f"latency={latency:.0f}ms, response_len={len(response_text)}")
    except Exception as e:
        fail("Step 8 — End-to-end chat", str(e))

# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _ensure_sample_docs(docs_path: str):
    """Create sample docs if the directory is empty."""
    os.makedirs(docs_path, exist_ok=True)
    existing = list(Path(docs_path).glob("*"))
    existing = [f for f in existing if f.name != ".gitkeep"]

    if not existing:
        logger.info("No documents found — creating sample documents for lab testing")
        # Create sample HR policy documents
        (Path(docs_path) / "hr_leave_policy.txt").write_text(
            "LEAVE POLICY\n\n"
            "Annual Leave: All employees are entitled to 25 days of annual leave per year.\n"
            "Sick Leave: Employees receive 10 days of paid sick leave per year.\n"
            "Maternity Leave: 26 weeks of paid maternity leave for eligible employees.\n"
            "Paternity Leave: 4 weeks of paid paternity leave.\n"
            "Emergency Leave: Up to 3 days for immediate family emergencies.\n"
            "Leave applications must be submitted at least 2 weeks in advance.\n"
        )
        (Path(docs_path) / "it_support_policy.txt").write_text(
            "IT SUPPORT POLICY\n\n"
            "Help Desk Hours: Monday to Friday, 8:00 AM to 6:00 PM.\n"
            "Priority Levels:\n"
            "- P1 (Critical): Response within 1 hour. System down, data loss.\n"
            "- P2 (High): Response within 4 hours. Major functionality impaired.\n"
            "- P3 (Medium): Response within 1 business day. Minor issues.\n"
            "- P4 (Low): Response within 3 business days. Enhancements.\n"
            "Contact: helpdesk@company.com or ext. 1234.\n"
            "Remote support available via TeamViewer.\n"
        )
        (Path(docs_path) / "expense_policy.txt").write_text(
            "EXPENSE REIMBURSEMENT POLICY\n\n"
            "Travel Expenses: Economy class for flights under 6 hours.\n"
            "Meal Allowance: $50 per day for domestic travel, $75 for international.\n"
            "Hotel: Up to $200 per night for domestic, $300 for international.\n"
            "All expenses must be submitted within 30 days with receipts.\n"
            "Approval required for expenses over $500.\n"
            "Submit claims via the expense management portal.\n"
        )
        logger.info("Created 3 sample documents in %s", docs_path)

# --------------------------------------------------------------------------- #
# Main                                                                          #
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="LLMOps Lab End-to-End Test Runner")
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--bucket", default="", help="GCS bucket (optional)")
    parser.add_argument("--docs-path", default="data/documents/")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--step", type=int, default=0, help="Run only this step (0 = all)")
    parser.add_argument("--skip-gcs", action="store_true", help="Skip GCS step (if bucket not accessible)")
    parser.add_argument("--skip-serving", action="store_true", help="Skip serving/chat steps")
    args = parser.parse_args()

    os.chdir(ROOT)  # Run from project root

    run_all = args.step == 0
    vdb = None
    qa_pairs = []
    proc = None

    try:
        # Step 1: GCP connectivity
        if run_all or args.step == 1:
            step1_gcp_connectivity(args.project, args.location)

        # Step 2: GCS
        if (run_all or args.step == 2) and not args.skip_gcs:
            if args.bucket:
                step2_gcs(args.project, args.bucket)
            else:
                skip("Step 2 — GCS read/write", "--bucket not provided")
        elif args.skip_gcs:
            skip("Step 2 — GCS read/write", "--skip-gcs flag set")

        # Step 3: Gemini API
        if run_all or args.step == 3:
            step3_gemini(args.project, args.location)

        # Step 4: Feature Engineering
        if run_all or args.step == 4:
            vdb = step4_feature_engineering(args.project, args.location, args.docs_path)

        # Step 5: QA Dataset
        if run_all or args.step == 5:
            qa_pairs = step5_generate_dataset(vdb, args.project, args.location)

        # Step 6: Evaluation
        if run_all or args.step == 6:
            step6_evaluation(qa_pairs, vdb, args.project, args.location)

        # Step 7: Serving
        if (run_all or args.step == 7) and not args.skip_serving:
            proc = step7_serving(args.project, args.location, args.port)

        # Step 8: Chat test
        if (run_all or args.step == 8) and not args.skip_serving and proc is not None:
            step8_chat_test(args.port)

    finally:
        if proc is not None:
            proc.terminate()
            logger.info("Stopped serving process")

        print_summary()


if __name__ == "__main__":
    main()
