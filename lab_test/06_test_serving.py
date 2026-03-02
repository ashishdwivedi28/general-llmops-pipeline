"""Test the serving layer (FastAPI server) locally.

Starts the server and runs health check + chat endpoint test.

Usage:
    # Terminal 1 — start server:
    python -m uvicorn serving.server:app --port 8080

    # Terminal 2 — run this test:
    python lab_test/06_test_serving.py --project YOUR_PROJECT_ID
"""

import argparse, sys, logging, time, subprocess, json, urllib.request, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

def wait_for_server(url: str, timeout: int = 20) -> bool:
    for i in range(timeout):
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False

def test_health(base_url: str):
    resp = urllib.request.urlopen(f"{base_url}/health", timeout=5)
    data = json.loads(resp.read())
    logger.info("Health: %s", data)
    assert data.get("status") == "healthy"
    logger.info("✅ /health OK")

def test_chat(base_url: str, query: str):
    payload = json.dumps({"query": query, "session_id": "lab-test"}).encode()
    req = urllib.request.Request(
        f"{base_url}/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    response = data.get("response", "")
    latency = data.get("latency_ms", 0)
    logger.info("Chat response (%dms): %s", latency, response[:120])
    assert response, "Empty response"
    logger.info("✅ /chat OK")
    return response

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--external-server", action="store_true",
                        help="Don't start server — connect to already-running one")
    parser.add_argument("--query", default="What is the annual leave policy?")
    args = parser.parse_args()

    base_url = f"http://localhost:{args.port}"
    proc = None

    try:
        if not args.external_server:
            # Start server as subprocess
            env = os.environ.copy()
            env.update({
                "GCP_PROJECT_ID": args.project,
                "GCP_LOCATION": args.location,
                "MODEL_NAME": "gemini-2.0-flash",
                "PORT": str(args.port),
                "RAG_CORPUS_RESOURCE": "",
            })
            logger.info("Starting server on port %d...", args.port)
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "serving.server:app",
                 "--host", "0.0.0.0", "--port", str(args.port)],
                env=env,
                cwd=str(Path(__file__).parent.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        logger.info("Waiting for server at %s...", base_url)
        if not wait_for_server(f"{base_url}/health", timeout=20):
            logger.error("Server did not start in time")
            sys.exit(1)

        logger.info("Server is up!")
        test_health(base_url)

        response = test_chat(base_url, args.query)

        print("\n" + "="*60)
        print("SERVING LAYER TEST RESULTS")
        print("="*60)
        print(f"  Server URL:  {base_url}")
        print(f"  Query:       {args.query}")
        print(f"  Response:    {response[:200]}")
        print("="*60)
        print("\n✅ SERVING LAYER — ALL TESTS PASSED")

    finally:
        if proc:
            proc.terminate()
            logger.info("Server stopped")

if __name__ == "__main__":
    main()
