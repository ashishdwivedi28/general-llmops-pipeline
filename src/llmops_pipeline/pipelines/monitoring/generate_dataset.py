"""Monitoring — Generate QA evaluation dataset from documents using Gemini."""

from __future__ import annotations

import csv
import json
import typing as T

from langchain_community.document_loaders import DirectoryLoader
from langchain_google_vertexai import ChatVertexAI
from langchain_text_splitters import RecursiveCharacterTextSplitter

from llmops_pipeline.pipelines.base import Job, Locals


class GenerateDatasetJob(Job, frozen=True):
    """Auto-generate QA evaluation dataset from source documents.

    Uses Gemini to read document chunks and generate question/answer pairs
    that can be used by the evaluation pipeline.

    Config fields:
        gcs_documents_path: path to documents (local or GCS).
        output_csv_path: where to write CSV output.
        output_json_path: where to write JSON output.
        num_questions_per_chunk: how many QA pairs per chunk.
        model: Gemini model for generation.
        project: GCP project ID.
        location: GCP region.
    """

    KIND: T.Literal["GenerateDatasetJob"] = "GenerateDatasetJob"

    gcs_documents_path: str = "data/documents/"
    output_csv_path: str = "data/datasets/rag_eval.csv"
    output_json_path: str = "data/datasets/rag_eval.json"
    num_questions_per_chunk: int = 2
    model: str = "gemini-2.0-flash"
    project: str = ""
    location: str = "us-central1"

    def run(self) -> Locals:
        logger = self.logger_service.logger()
        logger.info("Generating QA dataset from: {}", self.gcs_documents_path)

        # Load and chunk documents
        loader = DirectoryLoader(self.gcs_documents_path, show_progress=True)
        documents = loader.load()
        splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200)
        chunks = splitter.split_documents(documents)
        logger.info("Loaded {} documents → {} chunks", len(documents), len(chunks))

        # Initialize Gemini for QA generation
        llm = ChatVertexAI(
            model_name=self.model,
            temperature=0.7,
            project=self.project,
            location=self.location,
        )

        qa_pairs = []
        for i, chunk in enumerate(chunks):
            prompt = (
                f"Based on the following text, generate {self.num_questions_per_chunk} "
                f"question-answer pairs. Return as JSON array: "
                f'[{{"question": "...", "expected_answer": "...", "context": "..."}}]\n\n'
                f"Text:\n{chunk.page_content}"
            )
            try:
                response = llm.invoke(prompt)
                pairs = json.loads(response.content)
                for pair in pairs:
                    pair["source"] = chunk.metadata.get("source", "unknown")
                    qa_pairs.append(pair)
            except Exception as e:
                logger.warning("Failed to generate QA for chunk {}: {}", i, e)

        logger.info("Generated {} QA pairs", len(qa_pairs))

        # Write CSV
        with open(self.output_csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["question", "expected_answer", "context", "source"])
            writer.writeheader()
            writer.writerows(qa_pairs)

        # Write JSON
        with open(self.output_json_path, "w") as f:
            json.dump(qa_pairs, f, indent=2)

        # Log to experiment
        with self.vertex_ai_service.run_context("generate-dataset"):
            self.vertex_ai_service.log_metrics({
                "num_qa_pairs": float(len(qa_pairs)),
                "num_source_chunks": float(len(chunks)),
            })

        return {"num_qa_pairs": len(qa_pairs), "output_csv": self.output_csv_path}
