"""FastAPI server — ADK agent serving with manifest-driven configuration.

Integrates:
- Pipeline Artifact Manifest — the bridge between offline pipelines and online serving.
  On startup the manifest is read to discover vector-search endpoints, active model,
  prompt version, etc.  A background ``ManifestWatcher`` refreshes the manifest
  periodically so the serving layer auto-adapts to new pipeline outputs.
- ADK's ``get_fast_api_app()`` for agent serving (background-initialized via lifespan)
- Health / readiness endpoints for Cloud Run (always available)
- Feedback endpoint for user ratings
- OpenTelemetry observability

Design notes:
    Cloud Run startup probes need /health to respond within seconds.
    All heavy GCP/ADK initialization is launched as a background asyncio Task
    inside the lifespan handler so the server starts accepting health-check
    traffic immediately.  The /ready endpoint signals 503 until the agent is
    fully initialized.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from serving.utils.config import ServerConfig
from serving.utils.observability import setup_observability
from serving.gateway import attach_gateway

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global startup state — populated by the background initializer task.
# All values default to None / False so the app stays runnable while the task
# is still starting.
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {
    "ready": False,
    "error": None,
    "config": None,
    "agent": None,
    "interaction_logger": None,
    "guardrail_checker": None,
    "manifest_watcher": None,
    "prompt_registry": None,
    "cost_tracker": None,
}


def _sync_initialize(app: FastAPI) -> None:  # pragma: no cover
    """Heavy (potentially blocking) GCP/ADK initialization.

    Runs inside a thread pool via ``asyncio.to_thread`` so it never blocks
    the event loop.  The /health endpoint remains responsive throughout.
    """
    config = ServerConfig()
    _state["config"] = config

    # -- Observability (non-fatal) -------------------------------------------
    try:
        setup_observability(
            project_id=config.GCP_PROJECT_ID,
            enable_tracing=config.ENABLE_TRACING,
        )
    except Exception as exc:
        logger.warning("Observability setup failed: %s", exc)

    # -- Manifest (non-fatal — falls back to env-var config) -----------------
    manifest = None
    if config.MANIFEST_ENABLED and config.manifest_bucket:
        try:
            from llmops_pipeline.io.manifest import ManifestWatcher

            watcher = ManifestWatcher(
                app_id=config.MANIFEST_APP_ID,
                bucket_name=config.manifest_bucket,
                project=config.GCP_PROJECT_ID,
                refresh_interval=config.MANIFEST_REFRESH_INTERVAL,
            )
            manifest = watcher.refresh()
            _state["manifest_watcher"] = watcher
            logger.info(
                "Manifest loaded (v%s) — vector endpoint: %s, model: %s",
                manifest.version,
                manifest.feature_engineering.vector_endpoint_resource_name or "<none>",
                manifest.deployment.active_model or config.MODEL_NAME,
            )
        except Exception as exc:
            logger.warning("Manifest load failed (using env-var config): %s", exc)

    # -- Prompt Registry (non-fatal — falls back to built-in prompts) ---------
    if config.PROMPT_REGISTRY_ENABLED:
        try:
            from llmops_pipeline.io.prompt_registry import PromptRegistry, PromptRegistryConfig
            from serving.prompt import set_prompt_registry

            # Resolve active prompt version from manifest if available
            active_prompt_version = config.PROMPT_ACTIVE_VERSION
            if manifest is not None and manifest.deployment.active_prompt_version:
                version_str = manifest.deployment.active_prompt_version
                # Parse "v3" → 3 or plain "3" → 3
                try:
                    active_prompt_version = int(version_str.lstrip("v"))
                except (ValueError, AttributeError):
                    pass

            prompt_config = PromptRegistryConfig(
                app_id=config.MANIFEST_APP_ID,
                bucket_name=config.manifest_bucket or "",
                project=config.GCP_PROJECT_ID,
                active_version=active_prompt_version,
            )
            registry = PromptRegistry(config=prompt_config)
            set_prompt_registry(registry)
            _state["prompt_registry"] = registry
            logger.info(
                "Prompt registry configured: app=%s, active_version=v%d",
                prompt_config.app_id,
                active_prompt_version,
            )
        except Exception as exc:
            logger.warning("Prompt registry init failed (using built-in prompts): %s", exc)

    # -- Cost Tracker (non-fatal) ----------------------------------------------
    try:
        from serving.utils.cost_tracker import CostTracker

        _state["cost_tracker"] = CostTracker(
            project_id=config.GCP_PROJECT_ID,
            bq_dataset=config.BQ_DATASET,
            bq_table="costs",
        )
        logger.info("Cost tracker initialised")
    except Exception as exc:
        logger.warning("Cost tracker init failed: %s", exc)

    # -- Callbacks (non-fatal) ------------------------------------------------
    try:
        from serving.callbacks import GuardrailChecker, InteractionLogger

        _state["interaction_logger"] = InteractionLogger(
            project_id=config.GCP_PROJECT_ID,
            bq_dataset=config.BQ_DATASET,
        )
        _state["guardrail_checker"] = GuardrailChecker(
            valid_topics=config.VALID_TOPICS,
            invalid_topics=config.INVALID_TOPICS,
        )
    except Exception as exc:
        logger.warning("Callbacks init failed: %s", exc)

    # -- ADK Agent (non-fatal — server runs in degraded mode on failure) ------
    try:
        from google.adk.cli.fast_api import get_fast_api_app

        from serving.agent import create_agent

        agent = create_agent(config, manifest=manifest)
        adk_app = get_fast_api_app(
            agent=agent,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION,
        )
        # Starlette/FastAPI allows dynamic mount — router lookup is runtime.
        app.mount("/agent", adk_app)
        _state["agent"] = agent
        _state["ready"] = True
        logger.info("Agent initialized — server is fully ready.")
    except Exception as exc:
        logger.error("Agent initialization failed: %s", exc)
        _state["error"] = str(exc)
        _state["ready"] = False

    # -- Start manifest watcher (after agent is initialized) ------------------
    # Watcher will be started from the async wrapper (see _async_init_and_watch)


async def _async_init_and_watch(app: FastAPI) -> None:
    """Coordinate blocking init (in thread) then start async manifest watcher.

    This wrapper exists so we can:
    1. Run the heavy blocking setup in a thread (to keep /health responsive).
    2. After that completes, start the ManifestWatcher's async polling loop
       from within the async context that owns the event loop.
    """
    await asyncio.to_thread(_sync_initialize, app)

    watcher = _state.get("manifest_watcher")
    if watcher is not None:
        try:
            watcher.start_async()
        except Exception as exc:
            logger.warning("Manifest watcher failed to start: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: RUF029
    """Launch background init task and yield immediately.

    The background task calls the blocking GCP/ADK setup in a thread so
    the event loop stays free and /health responds at once.
    After initialization completes, the ManifestWatcher is started as an
    async background task for periodic manifest refresh.
    """
    asyncio.create_task(_async_init_and_watch(app))
    yield
    # Shutdown: stop manifest watcher
    watcher = _state.get("manifest_watcher")
    if watcher is not None:
        watcher.stop()
    logger.info("Server shutting down.")
    _state.clear()


def create_app() -> FastAPI:
    """Create the FastAPI application with deferred ADK agent mounting."""
    app = FastAPI(
        title="LLMOps RAG Agent API",
        description="Production RAG agent powered by Google ADK + Vertex AI",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API Gateway middleware (auth, rate limiting, RBAC)
    try:
        attach_gateway(app, config_path=ServerConfig().GATEWAY_CONFIG_PATH)
    except Exception as exc:
        logger.warning("Gateway middleware attach failed: %s", exc)

    @app.get("/health")
    async def health():
        """Health check — always 200 so Cloud Run startup probe passes immediately."""
        cfg = _state.get("config") or ServerConfig()
        return {"status": "healthy", "agent": cfg.AGENT_NAME}

    @app.get("/ready")
    async def ready():
        """Readiness — 503 until ADK agent is fully initialized."""
        if _state.get("ready"):
            info: dict[str, Any] = {"status": "ready"}
            watcher = _state.get("manifest_watcher")
            if watcher is not None:
                m = watcher.current
                info["manifest_version"] = m.version
                info["active_model"] = m.deployment.active_model or "default"
                info["vector_endpoint"] = (
                    m.feature_engineering.vector_endpoint_resource_name or "none"
                )
            prompt_reg = _state.get("prompt_registry")
            if prompt_reg is not None:
                info["active_prompt_version"] = f"v{prompt_reg.config.active_version}"
            return info
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "error": _state.get("error")},
        )

    @app.get("/manifest")
    async def manifest_info():
        """Return the current pipeline artifact manifest (for debugging / admin)."""
        watcher = _state.get("manifest_watcher")
        if watcher is None:
            return {"enabled": False, "message": "Manifest not configured"}
        m = watcher.current
        return m.model_dump(mode="json")

    @app.get("/costs")
    async def costs():
        """Return aggregated cost summary (admin endpoint)."""
        tracker = _state.get("cost_tracker")
        if tracker is None:
            return {"enabled": False, "message": "Cost tracking not configured"}
        return tracker.summary().model_dump()

    @app.post("/feedback")
    async def feedback(request: Request):
        """Receive user feedback for an interaction."""
        body = await request.json()
        ilogger = _state.get("interaction_logger")
        if ilogger:
            ilogger.log_feedback(
                session_id=body.get("session_id", ""),
                interaction_id=body.get("interaction_id", ""),
                rating=body.get("rating", 0),
                comment=body.get("comment", ""),
                model=body.get("model", ""),
                prompt_version=body.get("prompt_version", ""),
            )
        return {"status": "recorded"}

    @app.post("/chat")
    async def chat(request: Request):
        """Direct chat endpoint (non-ADK) with guardrails and logging."""
        agent = _state.get("agent")
        if agent is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"error": "Agent not ready. Try again shortly."},
            )

        cfg = _state.get("config") or ServerConfig()
        gc = _state.get("guardrail_checker")
        ilogger = _state.get("interaction_logger")

        body = await request.json()
        query = body.get("query", "")
        session_id = body.get("session_id", "anonymous")

        # Input guardrail
        if gc:
            allowed, reason = gc.check_input(query)
            if not allowed:
                return JSONResponse(
                    status_code=400,
                    content={"error": reason, "response": "I can't help with that topic."},
                )

        start_time = time.time()

        try:
            from google.adk.runners import InMemoryRunner
            from google.genai import types

            runner = InMemoryRunner(agent=agent)
            session = await runner.session_service.create_session(
                app_name=cfg.AGENT_NAME,
                user_id=session_id,
            )
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=query)],
            )

            response_text = ""
            async for event in runner.run_async(
                user_id=session_id,
                session_id=session.id,
                new_message=content,
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            response_text += part.text

        except Exception as e:
            logger.error("Agent error: %s", e)
            response_text = "I'm sorry, an error occurred. Please try again."

        latency_ms = (time.time() - start_time) * 1000

        # Output guardrail
        if gc:
            output_ok, _ = gc.check_output(response_text)
            if not output_ok:
                response_text = "I generated a response but it was filtered for safety."

        # Log interaction
        if ilogger:
            ilogger.log_interaction(
                session_id=session_id,
                user_query=query,
                agent_response=response_text,
                latency_ms=latency_ms,
            )

        return {
            "response": response_text,
            "session_id": session_id,
            "latency_ms": round(latency_ms, 2),
        }

    return app


# Module-level app (for uvicorn / Cloud Run)
app = create_app()


if __name__ == "__main__":
    _cfg = ServerConfig()
    uvicorn.run(
        "serving.server:app",
        host=_cfg.HOST,
        port=_cfg.PORT,
        log_level=_cfg.LOG_LEVEL,
        reload=False,
    )
