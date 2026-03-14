"""Quick Gemini connectivity test.

Run this FIRST to verify the Vertex AI / Gemini API works in your lab.

Usage:
    python lab_test/01_test_gemini.py --project YOUR_PROJECT_ID
"""

import argparse
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    args = parser.parse_args()

    logger.info("Testing Gemini API — project: %s, location: %s", args.project, args.location)

    from google.cloud import aiplatform

    aiplatform.init(project=args.project, location=args.location)
    logger.info("✅ aiplatform.init() succeeded")

    from langchain_google_vertexai import ChatVertexAI

    llm = ChatVertexAI(
        model_name="gemini-2.0-flash",
        temperature=0.0,
        project=args.project,
        location=args.location,
    )

    logger.info("Sending test prompt to gemini-2.0-flash...")
    response = llm.invoke("You are a helpful assistant. Respond with exactly: GEMINI_API_OK")
    logger.info("Response: %s", response.content)

    from langchain_google_vertexai import VertexAIEmbeddings

    embedder = VertexAIEmbeddings(
        model_name="text-embedding-004",
        project=args.project,
        location=args.location,
    )
    logger.info("Testing text-embedding-004...")
    emb = embedder.embed_query("test embedding")
    logger.info("✅ Embedding dimensions: %d", len(emb))

    print("\n✅ GEMINI API — ALL TESTS PASSED")
    print("   Model: gemini-2.0-flash")
    print(f"   Embedding: text-embedding-004 ({len(emb)} dims)")


if __name__ == "__main__":
    main()
