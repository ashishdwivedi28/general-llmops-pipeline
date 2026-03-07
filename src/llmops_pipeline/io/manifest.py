"""Pipeline Artifact Manifest — the bridge between offline pipelines and online serving.

The manifest is a JSON document stored at ``gs://{bucket}/manifests/{app_id}/latest.json``
that records every runtime-critical artifact reference produced by the pipeline system.

Writers: Each pipeline phase (FE, Deployment, Monitoring) updates its own section.
Readers: The serving layer loads the manifest at startup and refreshes on a timer.

Design decisions:
- Pydantic models (strict, frozen) for validation — the manifest is always well-typed.
- Section-level updates with ``update_section()`` to avoid clobbering concurrent writes.
- Local-file fallback for testing / Qwiklabs without a real GCS bucket.
- Thread-safe via re-read-before-write pattern (eventual consistency in GCS is OK
  because only one pipeline phase writes at a time).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

try:
    from google.cloud import storage
except ImportError:
    storage = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manifest section models
# ---------------------------------------------------------------------------


class FeatureEngineeringManifest(BaseModel):
    """Artifacts produced by Pipeline Phase 1 — Feature Engineering."""

    model_config = ConfigDict(strict=True, extra="forbid")

    vector_index_resource_name: str = ""
    vector_endpoint_resource_name: str = ""
    deployed_index_id: str = "deployed_index"
    embedding_model: str = "text-embedding-004"
    embedding_dimensions: int = 768
    documents_gcs_uri: str = ""
    embeddings_gcs_uri: str = ""
    chunks_metadata_gcs_uri: str = ""
    num_documents: int = 0
    num_chunks: int = 0
    documents_hash: str = ""
    last_run: str = ""


class DeploymentManifest(BaseModel):
    """Artifacts produced by Pipeline Phase 2+3 — Evaluation & Deployment."""

    model_config = ConfigDict(strict=True, extra="forbid")

    model_resource_name: str = ""
    model_version: str = ""
    model_display_name: str = ""
    active_model: str = "gemini-2.0-flash"
    active_prompt_version: str = "v1"
    cloud_run_service_url: str = ""
    cloud_run_revision: str = ""
    eval_scores: dict[str, float] = Field(default_factory=dict)
    quality_gate_passed: bool = False
    deployment_timestamp: str = ""
    last_run: str = ""


class MonitoringManifest(BaseModel):
    """Results from Pipeline Phase 4 — Monitoring."""

    model_config = ConfigDict(strict=True, extra="forbid")

    monitoring_scores: dict[str, float] = Field(default_factory=dict)
    num_traces_evaluated: int = 0
    degraded: bool = False
    last_diagnosis: str = ""
    remediation_action: str = ""
    status: str = "healthy"
    last_run: str = ""


class PipelineManifest(BaseModel):
    """Top-level manifest aggregating all pipeline phase outputs.

    This is *the* document that bridges offline (pipelines) and online (serving).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    app_id: str = "llmops-app"
    version: str = ""
    created_at: str = ""
    updated_at: str = ""
    feature_engineering: FeatureEngineeringManifest = Field(
        default_factory=FeatureEngineeringManifest
    )
    deployment: DeploymentManifest = Field(default_factory=DeploymentManifest)
    monitoring: MonitoringManifest = Field(default_factory=MonitoringManifest)


# ---------------------------------------------------------------------------
# Manifest I/O — GCS + local file
# ---------------------------------------------------------------------------

_GCS_PREFIX = "manifests"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gcs_manifest_path(app_id: str) -> str:
    """Return the GCS blob path (without gs:// prefix)."""
    return f"{_GCS_PREFIX}/{app_id}/latest.json"


def write_manifest(
    manifest: PipelineManifest,
    *,
    bucket_name: str,
    project: str = "",
) -> str:
    """Write (or overwrite) the manifest to GCS.

    Returns:
        The ``gs://`` URI of the written manifest.
    """
    manifest_dict = manifest.model_copy(update={"updated_at": _now_iso()}).model_dump(mode="json")

    blob_path = _gcs_manifest_path(manifest.app_id)

    if bucket_name == "__local__" or not bucket_name:
        # Fallback: write to local file (for lab / testing)
        return _write_local(manifest_dict, manifest.app_id)

    try:
        client = storage.Client(project=project or None)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(
            json.dumps(manifest_dict, indent=2, default=str),
            content_type="application/json",
        )
        uri = f"gs://{bucket_name}/{blob_path}"
        logger.info("Manifest written to %s", uri)
        return uri
    except Exception as exc:
        logger.error("Failed to write manifest to GCS: %s", exc)
        # Fallback to local
        return _write_local(manifest_dict, manifest.app_id)


def read_manifest(
    app_id: str,
    *,
    bucket_name: str,
    project: str = "",
) -> PipelineManifest:
    """Read the latest manifest from GCS.

    Returns a default (empty) manifest if the file does not exist yet.
    """
    blob_path = _gcs_manifest_path(app_id)

    if bucket_name == "__local__" or not bucket_name:
        return _read_local(app_id)

    try:
        client = storage.Client(project=project or None)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        if not blob.exists():
            logger.info(
                "No manifest found at gs://%s/%s — returning default",
                bucket_name,
                blob_path,
            )
            return PipelineManifest(app_id=app_id, created_at=_now_iso(), version="0")

        content = blob.download_as_text()
        data = json.loads(content)
        manifest = PipelineManifest.model_validate(data)
        logger.info("Manifest loaded from gs://%s/%s", bucket_name, blob_path)
        return manifest
    except Exception as exc:
        logger.error("Failed to read manifest from GCS: %s — returning default", exc)
        return PipelineManifest(app_id=app_id, created_at=_now_iso(), version="0")


def update_section(
    app_id: str,
    section: str,
    data: dict[str, Any],
    *,
    bucket_name: str,
    project: str = "",
) -> PipelineManifest:
    """Read-modify-write a single section of the manifest.

    ``section`` must be one of: ``feature_engineering``, ``deployment``, ``monitoring``.

    Args:
        app_id: Application identifier.
        section: Manifest section to update.
        data: Dict of field values to patch into the section.
        bucket_name: GCS bucket (or ``__local__`` for local file).
        project: GCP project ID.

    Returns:
        The updated ``PipelineManifest``.
    """
    valid_sections = {"feature_engineering", "deployment", "monitoring"}
    if section not in valid_sections:
        raise ValueError(f"Invalid section '{section}'. Must be one of {valid_sections}")

    manifest = read_manifest(app_id, bucket_name=bucket_name, project=project)

    # Patch the section
    section_model = getattr(manifest, section)
    updated_fields = {**section_model.model_dump(), **data, "last_run": _now_iso()}
    new_section = type(section_model).model_validate(updated_fields)

    # Build new manifest with updated section + bumped version
    version = str(int(manifest.version or "0") + 1)
    new_manifest = manifest.model_copy(
        update={
            section: new_section,
            "version": version,
            "updated_at": _now_iso(),
        }
    )

    write_manifest(new_manifest, bucket_name=bucket_name, project=project)
    logger.info("Manifest section '%s' updated (v%s)", section, version)
    return new_manifest


# ---------------------------------------------------------------------------
# Local file fallback (for testing / lab / offline dev)
# ---------------------------------------------------------------------------

_LOCAL_DIR = Path(".manifests")


def _local_path(app_id: str) -> Path:
    return _LOCAL_DIR / app_id / "latest.json"


def _write_local(data: dict, app_id: str) -> str:
    path = _local_path(app_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info("Manifest written to local file: %s", path)
    return str(path)


def _read_local(app_id: str) -> PipelineManifest:
    path = _local_path(app_id)
    if not path.exists():
        logger.info("No local manifest found at %s — returning default", path)
        return PipelineManifest(app_id=app_id, created_at=_now_iso(), version="0")
    try:
        data = json.loads(path.read_text())
        return PipelineManifest.model_validate(data)
    except Exception as exc:
        logger.error("Failed to read local manifest: %s", exc)
        return PipelineManifest(app_id=app_id, created_at=_now_iso(), version="0")


# ---------------------------------------------------------------------------
# Manifest watcher (used by the serving layer for periodic refresh)
# ---------------------------------------------------------------------------


class ManifestWatcher:
    """Periodically polls GCS for manifest updates.

    Usage in the serving layer::

        watcher = ManifestWatcher("my-app", bucket_name="my-bucket", project="p")
        watcher.start(interval_seconds=60)
        ...
        manifest = watcher.current  # always up-to-date
        watcher.stop()
    """

    def __init__(
        self,
        app_id: str,
        *,
        bucket_name: str,
        project: str = "",
        refresh_interval: int = 60,
    ):
        self.app_id = app_id
        self.bucket_name = bucket_name
        self.project = project
        self.refresh_interval = refresh_interval
        self._current: PipelineManifest | None = None
        self._task: Any = None  # asyncio.Task reference
        self._running = False

    @property
    def current(self) -> PipelineManifest:
        """Return the most recently fetched manifest (or default)."""
        if self._current is None:
            self._current = read_manifest(
                self.app_id, bucket_name=self.bucket_name, project=self.project
            )
        return self._current

    def refresh(self) -> PipelineManifest:
        """Force an immediate refresh from storage."""
        try:
            self._current = read_manifest(
                self.app_id, bucket_name=self.bucket_name, project=self.project
            )
            logger.debug("Manifest refreshed: v%s", self._current.version)
        except Exception as exc:
            logger.warning("Manifest refresh failed: %s", exc)
        return self.current

    async def _poll_loop(self) -> None:
        """Async polling loop — runs as a background asyncio task."""
        import asyncio

        self._running = True
        logger.info(
            "Manifest watcher started for '%s' (every %ds)",
            self.app_id,
            self.refresh_interval,
        )
        while self._running:
            try:
                await asyncio.to_thread(self.refresh)
            except Exception as exc:
                logger.warning("Manifest poll error: %s", exc)
            await asyncio.sleep(self.refresh_interval)

    def start_async(self) -> None:
        """Start the background polling task (call from within an async context)."""
        import asyncio

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    def stop(self) -> None:
        """Stop the polling task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Manifest watcher stopped for '%s'", self.app_id)
