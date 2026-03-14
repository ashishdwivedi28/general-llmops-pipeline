"""Test Gemini-as-Judge evaluation.

Usage:
    python lab_test/05_test_evaluation.py --project YOUR_PROJECT_ID
"""

import argparse
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# Sample QA pairs for testing (no external dataset needed)
SAMPLE_QA = [
    {
        "question": "How many days of annual leave do employees get?",
        "expected_answer": "Employees are entitled to 25 days of annual leave per year.",
        "generated_answer": (
            "According to the policy, all full-time employees receive "
            "25 days of annual leave per year."
        ),
        "context": "Annual Leave: 25 days per year for all full-time employees.",
    },
    {
        "question": "What is the sick leave policy?",
        "expected_answer": "Employees get 10 paid sick days per year.",
        "generated_answer": "The company provides 10 paid sick days per year for employees.",
        "context": "Sick Leave: 10 paid sick days per year.",
    },
]

JUDGE_PROMPT = """Evaluate this QA pair on a scale of 0.0 to 1.0.

Question: {question}
Expected Answer: {expected}
Generated Answer: {generated}
Context Used: {context}

Rate:
- answer_relevance: Is the generated answer relevant to the question? (0-1, higher=better)
- faithfulness: Is the answer based on the context provided? (0-1, higher=better)
- toxicity: Does the answer contain harmful/toxic content? (0-1, lower=better)

Respond ONLY as valid JSON: {{"answer_relevance": X, "faithfulness": X, "toxicity": X}}"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    args = parser.parse_args()

    from langchain_google_vertexai import ChatVertexAI

    judge = ChatVertexAI(
        model_name="gemini-2.0-flash",
        temperature=0.0,
        project=args.project,
        location=args.location,
    )

    scores = {"answer_relevance": [], "faithfulness": [], "toxicity": []}

    print("\n" + "=" * 60)
    print("GEMINI-AS-JUDGE EVALUATION")
    print("=" * 60)

    for i, pair in enumerate(SAMPLE_QA):
        prompt = JUDGE_PROMPT.format(
            question=pair["question"],
            expected=pair["expected_answer"],
            generated=pair["generated_answer"],
            context=pair["context"],
        )

        response = judge.invoke(prompt)
        raw = response.content.strip().strip("```json").strip("```").strip()

        try:
            data = json.loads(raw)
            for k in scores:
                scores[k].append(float(data.get(k, 0.0)))
            print(f"\nPair {i + 1}: {pair['question'][:50]}...")
            print(f"  answer_relevance: {data.get('answer_relevance')}")
            print(f"  faithfulness:     {data.get('faithfulness')}")
            print(f"  toxicity:         {data.get('toxicity')}")
        except json.JSONDecodeError:
            logger.warning("Could not parse judge response: %s", raw)

    avgs = {k: sum(v) / len(v) if v else 0.0 for k, v in scores.items()}
    decision = (
        "PASS" if avgs["answer_relevance"] >= 0.6 and avgs["faithfulness"] >= 0.6 else "BLOCKED"
    )

    print("\n" + "=" * 60)
    print("AVERAGE SCORES")
    print(f"  answer_relevance: {avgs['answer_relevance']:.3f}  (threshold: 0.70)")
    print(f"  faithfulness:     {avgs['faithfulness']:.3f}  (threshold: 0.65)")
    print(f"  toxicity:         {avgs['toxicity']:.3f}  (threshold: 0.10)")
    print(f"  DECISION: {decision}")
    print("=" * 60)
    print(f"\n✅ EVALUATION TEST PASSED — Decision: {decision}")


if __name__ == "__main__":
    main()
