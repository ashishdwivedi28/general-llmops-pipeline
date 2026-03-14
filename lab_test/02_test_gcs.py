"""Quick GCS connectivity test.

Usage:
    python lab_test/02_test_gcs.py --project YOUR_PROJECT_ID --bucket YOUR_BUCKET
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
    parser.add_argument(
        "--bucket",
        required=True,
        help="GCS bucket name (will be created if missing)",
    )
    args = parser.parse_args()

    from google.cloud import storage

    client = storage.Client(project=args.project)
    logger.info("GCS client initialised for project: %s", args.project)

    # Create bucket
    try:
        bucket = client.get_bucket(args.bucket)
        logger.info("✅ Bucket exists: gs://%s", args.bucket)
    except Exception:
        bucket = client.create_bucket(args.bucket, location="us-central1")
        logger.info("✅ Created bucket: gs://%s", args.bucket)

    # Write
    blob = bucket.blob("lab_test/write_test.txt")
    blob.upload_from_string("LLMOps lab GCS test — write OK")
    logger.info("✅ File written: gs://%s/lab_test/write_test.txt", args.bucket)

    # Read
    content = blob.download_as_text()
    assert "write OK" in content
    logger.info("✅ File read back: %s", content)

    # List
    blobs = list(bucket.list_blobs(prefix="lab_test/"))
    logger.info("✅ List blobs: %d file(s) in lab_test/", len(blobs))

    # Delete
    blob.delete()
    logger.info("✅ File deleted")

    print("\n✅ GCS — ALL TESTS PASSED")
    print(f"   Bucket: gs://{args.bucket}")
    print("   Operations: create, write, read, list, delete")


if __name__ == "__main__":
    main()
