"""Global services — logger, Vertex AI Experiments, Cloud Storage.

Each service follows the start/stop lifecycle pattern used by pipeline Job context managers.
"""

from __future__ import annotations

import abc
import contextlib as ctx
import sys
import typing as T

import loguru
import pydantic as pdt
from google.cloud import aiplatform, storage
from typing_extensions import override


# --- Base ---


class Service(abc.ABC, pdt.BaseModel, strict=True, frozen=True, extra="forbid"):
    """Abstract service with start/stop lifecycle."""

    @abc.abstractmethod
    def start(self) -> None:
        """Initialize the service."""

    def stop(self) -> None:
        """Tear down the service (no-op by default)."""


# --- Logger ---


class LoggerService(Service, frozen=True):
    """Loguru-based structured logging.

    Parameters:
        sink: output target (stderr/stdout/filepath).
        level: minimum log level.
        colorize: enable colour output.
        serialize: output as JSON.
    """

    sink: str = "stderr"
    level: str = "DEBUG"
    format: str = (
        "<green>[{time:YYYY-MM-DD HH:mm:ss}]</green>"
        "<level>[{level}]</level>"
        "<cyan>[{name}:{function}:{line}]</cyan>"
        " <level>{message}</level>"
    )
    colorize: bool = True
    serialize: bool = False

    @override
    def start(self) -> None:
        loguru.logger.remove()
        sinks = {"stderr": sys.stderr, "stdout": sys.stdout}
        config = self.model_dump()
        config["sink"] = sinks.get(config["sink"], config["sink"])
        loguru.logger.add(**config)

    def logger(self) -> loguru.Logger:
        """Return the global loguru logger."""
        return loguru.logger


# --- Vertex AI Experiments ---


class VertexAIService(Service, frozen=True):
    """Vertex AI Experiments tracking service.

    Replaces MLflow — uses `google.cloud.aiplatform` for experiment tracking,
    metric logging, and model registry operations.

    Parameters:
        project: GCP project ID.
        location: GCP region.
        experiment_name: Vertex AI Experiment name.
        staging_bucket: GCS bucket for pipeline artifacts.
    """

    project: str = ""
    location: str = "us-central1"
    experiment_name: str = "llmops-experiment"
    staging_bucket: str = ""

    @override
    def start(self) -> None:
        aiplatform.init(
            project=self.project,
            location=self.location,
            experiment=self.experiment_name,
            staging_bucket=self.staging_bucket or None,
        )

    @ctx.contextmanager
    def run_context(self, run_name: str) -> T.Generator[None, None, None]:
        """Context manager wrapping a Vertex AI Experiment run."""
        aiplatform.start_run(run_name)
        try:
            yield
        finally:
            aiplatform.end_run()

    def log_metrics(self, metrics: dict[str, float]) -> None:
        """Log metrics to the active experiment run."""
        aiplatform.log_metrics(metrics)

    def log_params(self, params: dict[str, str]) -> None:
        """Log parameters to the active experiment run."""
        aiplatform.log_params(params)


# --- Cloud Storage ---


class GCSService(Service, frozen=True):
    """Google Cloud Storage helper.

    Parameters:
        project: GCP project ID.
        bucket_name: default GCS bucket.
    """

    project: str = ""
    bucket_name: str = ""

    _client: storage.Client | None = None

    class Config:
        arbitrary_types_allowed = True

    @override
    def start(self) -> None:
        object.__setattr__(self, "_client", storage.Client(project=self.project))

    def client(self) -> storage.Client:
        if self._client is None:
            raise RuntimeError("GCSService not started. Call start() first.")
        return self._client

    def upload_blob(self, source_path: str, destination_blob: str) -> str:
        """Upload a local file to GCS. Returns gs:// URI."""
        bucket = self.client().bucket(self.bucket_name)
        blob = bucket.blob(destination_blob)
        blob.upload_from_filename(source_path)
        return f"gs://{self.bucket_name}/{destination_blob}"

    def download_blob(self, source_blob: str, destination_path: str) -> str:
        """Download a blob from GCS to local path."""
        bucket = self.client().bucket(self.bucket_name)
        blob = bucket.blob(source_blob)
        blob.download_to_filename(destination_path)
        return destination_path

    def list_blobs(self, prefix: str = "") -> list[str]:
        """List blob names under a prefix."""
        bucket = self.client().bucket(self.bucket_name)
        return [b.name for b in bucket.list_blobs(prefix=prefix)]
