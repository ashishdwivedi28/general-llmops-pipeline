"""Test the RAG pipeline: vector search + Gemini answer generation.

Runs a mini RAG loop: query → FAISS retrieval → Gemini answer.

Usage:
    python lab_test/04_test_rag_pipeline.py --project YOUR_PROJECT_ID
    python lab_test/04_test_rag_pipeline.py --project YOUR_PROJECT_ID --query "What is the leave policy?"
"""

import argparse, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

RAG_PROMPT = """You are a helpful assistant. Answer the question using ONLY the context below.
If the context doesn't contain the answer, say "I don't have that information."

Context:
{context}

Question: {question}

Answer:"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--docs-path", default="data/documents/")
    parser.add_argument("--query", default="What is the annual leave entitlement?")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    from lab_test.local_vector_db import LocalFaissVectorDB
    from langchain_google_vertexai import ChatVertexAI
    import os

    logger.info("RAG Pipeline Test — project: %s", args.project)

    # Load or build vector DB
    vdb = LocalFaissVectorDB(project=args.project, location=args.location)
    loaded = vdb.load_local("/tmp/lab_faiss_index")

    if not loaded:
        logger.info("No saved index found — building from documents...")
        # Create sample docs if needed
        os.makedirs(args.docs_path, exist_ok=True)
        existing = [f for f in Path(args.docs_path).glob("*") if f.name != ".gitkeep"]
        if not existing:
            Path(f"{args.docs_path}/sample_policy.txt").write_text(
                "COMPANY POLICY\n\n"
                "Annual Leave: 25 days per year for all full-time employees.\n"
                "Sick Leave: 10 paid sick days per year.\n"
                "Work from Home: Up to 3 days per week with manager approval.\n"
                "Training Budget: $2000 per employee annually.\n"
                "Performance Reviews: June and December each year.\n"
            )
        vdb.ingest_documents(args.docs_path, chunk_size=200, chunk_overlap=50)
        vdb.save_local("/tmp/lab_faiss_index")

    logger.info("Vector DB loaded: %d vectors", vdb.index.ntotal)

    # Retrieve context
    logger.info("Query: %s", args.query)
    results = vdb.query(args.query, top_k=args.top_k)
    context = "\n\n".join([f"[Chunk {i+1}] {r['text']}" for i, r in enumerate(results)])

    logger.info("Retrieved %d chunks", len(results))
    for i, r in enumerate(results):
        logger.info("  Chunk %d (score=%.3f): %s...", i+1, r["score"], r["text"][:60])

    # Generate answer
    llm = ChatVertexAI(
        model_name="gemini-2.0-flash",
        temperature=0.0,
        project=args.project,
        location=args.location,
    )
    prompt = RAG_PROMPT.format(context=context, question=args.query)
    response = llm.invoke(prompt)

    print("\n" + "="*60)
    print("RAG PIPELINE RESULT")
    print("="*60)
    print(f"Query:    {args.query}")
    print(f"Context chunks retrieved: {len(results)}")
    print(f"\nAnswer:\n{response.content}")
    print("="*60)
    print("\n✅ RAG PIPELINE TEST PASSED")

if __name__ == "__main__":
    main()
