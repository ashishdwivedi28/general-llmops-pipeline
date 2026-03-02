"""OpenTelemetry setup — tracing to Cloud Trace."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def setup_observability(project_id: str, enable_tracing: bool = True) -> None:
    """Configure OpenTelemetry with Cloud Trace exporter.

    Args:
        project_id: GCP project ID.
        enable_tracing: Whether to enable.
    """
    if not enable_tracing:
        logger.info("Tracing disabled.")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        exporter = CloudTraceSpanExporter(project_id=project_id)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        logger.info("OpenTelemetry → Cloud Trace configured for project: %s", project_id)
    except ImportError:
        logger.warning("OpenTelemetry packages not installed — tracing disabled")
    except Exception as e:
        logger.warning("Failed to setup tracing: %s", e)
