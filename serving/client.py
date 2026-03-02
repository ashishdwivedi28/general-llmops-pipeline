"""Simple test client for the serving layer.

Usage:
    python serving/client.py --query "What is the leave policy?"
    python serving/client.py --url http://localhost:8080 --query "Hello"
"""

from __future__ import annotations

import argparse
import json

import requests


def chat(url: str, query: str, session_id: str = "test-user") -> dict:
    """Send a chat request to the server."""
    response = requests.post(
        f"{url}/chat",
        json={"query": query, "session_id": session_id},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def health(url: str) -> dict:
    """Check server health."""
    response = requests.get(f"{url}/health", timeout=5)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description="LLMOps Agent Client")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--query", default="Hello, what can you help me with?")
    parser.add_argument("--session", default="test-user")
    parser.add_argument("--health-only", action="store_true")
    args = parser.parse_args()

    if args.health_only:
        result = health(args.url)
        print(json.dumps(result, indent=2))
        return

    print(f"Query: {args.query}")
    print("-" * 40)
    result = chat(args.url, args.query, args.session)
    print(f"Response: {result.get('response', 'N/A')}")
    print(f"Latency: {result.get('latency_ms', 'N/A')} ms")


if __name__ == "__main__":
    main()
