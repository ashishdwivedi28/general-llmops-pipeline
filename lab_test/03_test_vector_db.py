"""Test FAISS vector DB with Vertex AI embeddings.

Usage:
    python lab_test/03_test_vector_db.py --project YOUR_PROJECT_ID
    python lab_test/03_test_vector_db.py --project YOUR_PROJECT_ID --docs-path data/documents/
"""

import argparse, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--docs-path", default="data/documents/")
    args = parser.parse_args()

    from lab_test.local_vector_db import LocalFaissVectorDB
    import os

    # Create sample docs if directory is empty
    docs_path = args.docs_path
    os.makedirs(docs_path, exist_ok=True)
    existing = [f for f in Path(docs_path).glob("*") if f.name != ".gitkeep"]

    if not existing:
        logger.info("Creating sample documents for testing...")
        Path(f"{docs_path}/sample_policy.txt").write_text(
            "COMPANY POLICY\n\n"
            "Employees are entitled to 25 days annual leave per year.\n"
            "Sick leave is 10 days per year with medical certificate.\n"
            "Work from home is allowed up to 3 days per week.\n"
            "Performance reviews happen twice a year in June and December.\n"
            "Training budget is $2000 per employee per year.\n"
        )
        logger.info("Created: %s/sample_policy.txt", docs_path)

    # Build vector DB
    logger.info("Building FAISS vector DB...")
    vdb = LocalFaissVectorDB(
        project=args.project,
        location=args.location,
        embedding_model="text-embedding-004",
        embedding_dimensions=768,
    )

    result = vdb.ingest_documents(docs_path, chunk_size=200, chunk_overlap=50)
    logger.info("Ingestion: %s", result)

    # Test queries
    test_queries = [
        "How many days of annual leave?",
        "Can I work from home?",
        "What is the training budget?",
    ]

    print("\n" + "="*50)
    print("QUERY RESULTS")
    print("="*50)
    for query in test_queries:
        results = vdb.query(query, top_k=2)
        print(f"\nQuery: {query}")
        for i, r in enumerate(results):
            print(f"  [{i+1}] score={r['score']:.3f} — {r['text'][:80]}...")

    # Save locally
    vdb.save_local("/tmp/lab_faiss_index")
    logger.info("✅ Index saved to /tmp/lab_faiss_index")

    # Reload test
    vdb2 = LocalFaissVectorDB(project=args.project, location=args.location)
    vdb2.load_local("/tmp/lab_faiss_index")
    assert vdb2.index.ntotal == vdb.index.ntotal, "Index size mismatch after reload"
    logger.info("✅ Index reload OK: %d vectors", vdb2.index.ntotal)

    print(f"\n✅ VECTOR DB — ALL TESTS PASSED")
    print(f"   Documents: {result['num_documents']}")
    print(f"   Chunks: {result['num_chunks']}")
    print(f"   Index size: {vdb.index.ntotal} vectors")
    print(f"   Saved to: /tmp/lab_faiss_index")

if __name__ == "__main__":
    main()
